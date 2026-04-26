import pickle
import itertools
from concurrent.futures import ProcessPoolExecutor

from config import AVAILABLE_ITEMS, AVAILABLE_TMS


def is_valid_team(team, assignment, variants, raw_matchup_info):
    """Return True if the team has no resource conflicts.

    Checks:
      - No two team members share the same base species.
      - Non-berry items (those in AVAILABLE_ITEMS) are not used more than their quantity allows.
      - TMs used by assigned-opponent locked movesets do not exceed AVAILABLE_TMS quantities.
        (Flex members with expanded variants are skipped — their locked moves are unknown at this stage.)
    """
    # Species uniqueness: no two variants with the same species
    species_seen = set()
    for var_key in team:
        var = variants.get(var_key)
        sp  = var.species if var else var_key
        if sp in species_seen:
            return False
        species_seen.add(sp)

    # Item conflict: count usage of each AVAILABLE_ITEMS item across the team
    if AVAILABLE_ITEMS:
        item_counts = {}
        for var_key in team:
            var = variants.get(var_key)
            if var is None:
                continue
            item_tag = var.item_tag
            if item_tag in AVAILABLE_ITEMS:
                item_counts[item_tag] = item_counts.get(item_tag, 0) + 1
                if item_counts[item_tag] > AVAILABLE_ITEMS[item_tag]:
                    return False

    # TM conflict: count usage of each AVAILABLE_TMS move from locked movesets
    if AVAILABLE_TMS:
        tm_counts = {}
        for var_key in team:
            var = variants.get(var_key)
            if var is None or var.move_tag != 'expanded':
                continue
            assigned_opp = assignment.get(var_key)
            if assigned_opp is None:
                continue  # flex member — skip; locked moves unknown
            result = raw_matchup_info.get(var_key, {}).get(assigned_opp, {}).get(None)
            locked = result.locked_moves if result and result.locked_moves else []
            for move in locked:
                if move in AVAILABLE_TMS:
                    tm_counts[move] = tm_counts.get(move, 0) + 1
                    if tm_counts[move] > AVAILABLE_TMS[move]:
                        return False

    return True


def build_scores(prunedMatchupInfo):
    """Build raw MCTS scores and min-max normalized scores from the direct matchup context.

    Returns:
        raw_scores: dict[myPokemon][opponentPokemon] -> float
        normalized: dict[myPokemon][opponentPokemon] -> float in [0, 1]
        my_pokemon: list of BOX Pokémon names (in encounter order)
        opponents:  list of opponent Pokémon names (in encounter order)
    """
    raw_scores = {}
    for pmi in prunedMatchupInfo:
        score = pmi.none.score
        raw_scores.setdefault(pmi.myPokemon, {})[pmi.opponentPokemon] = score

    my_pokemon = list(raw_scores.keys())
    opponents = list(next(iter(raw_scores.values())).keys())

    normalized = {p: {} for p in my_pokemon}
    for opp in opponents:
        vals = [raw_scores[p][opp] for p in my_pokemon]
        lo, hi = min(vals), max(vals)
        for p in my_pokemon:
            if hi == lo:
                normalized[p][opp] = 1.0
            else:
                normalized[p][opp] = (raw_scores[p][opp] - lo) / (hi - lo)

    return raw_scores, normalized, my_pokemon, opponents


def print_matchup_rankings(m):
    """Print per-opponent and overall rankings for all BOX Pokémon."""
    raw_scores, normalized, my_pokemon, opponents = build_scores(m.prunedMatchupInfo)

    # Per-opponent ranking
    for opp in opponents:
        print(f"=== vs {opp} ===")
        ranked = sorted(my_pokemon, key=lambda p: raw_scores[p][opp], reverse=True)
        for rank, p in enumerate(ranked, 1):
            ratio = raw_scores[p][opp]
            norm = normalized[p][opp]
            print(f"  {rank:2d}. {p:<12}  ratio={ratio:8.4f}  norm={norm:.3f}")
        print()

    # Overall ranking by sum of normalized scores
    print("=== Overall ranking (sum of normalized scores) ===")
    totals = {p: sum(normalized[p][opp] for opp in opponents) for p in my_pokemon}
    ranked_overall = sorted(my_pokemon, key=lambda p: totals[p], reverse=True)
    for rank, p in enumerate(ranked_overall, 1):
        print(f"  {rank:2d}. {p:<12}  total={totals[p]:.3f}")
    print()


def select_best_team(m, n):
    """Return the top-n teams of 6, ranked by optimal assignment score.

    When len(opponents) == 6, every team slot is scored by 1-to-1 matchup (one
    team member assigned to counter each opponent).

    When len(opponents) < 6, the n_opp "covered" slots are scored by 1-to-1
    matchup and the remaining "flex" slots are scored by each member's average
    normalized score across all opponents. The optimizer chooses both which
    members fill covered vs flex slots and which opponent each covered member
    is assigned to.
    """
    raw_scores, normalized, my_pokemon, opponents = build_scores(m.prunedMatchupInfo)
    n_opp = len(opponents)

    # Variant conflict-checking (only when m carries variant data)
    _variants         = getattr(m, 'variants', None)
    _raw_matchup_info = getattr(m, 'rawMatchupInfo', None)
    check_conflicts   = _variants is not None and _raw_matchup_info is not None

    results = []
    for team in itertools.combinations(my_pokemon, 6):
        avg_score = {
            p: sum(normalized[p][opp] for opp in opponents) / n_opp
            for p in team
        }

        # Phase 1: find the assignment of n_opp members to opponents that
        # maximises matchup score alone (flex contribution is ignored here).
        best_matchup_score = -1.0
        best_covered = None
        best_perm = None

        for covered in itertools.combinations(range(6), n_opp):
            covered_members = [team[i] for i in covered]
            for perm in itertools.permutations(range(n_opp)):
                matchup_score = sum(
                    normalized[covered_members[j]][opponents[perm[j]]]
                    for j in range(n_opp)
                )
                if matchup_score > best_matchup_score:
                    best_matchup_score = matchup_score
                    best_covered = covered
                    best_perm = perm

        # Phase 2: remaining members fill flex slots, scored by average.
        flex_score = sum(
            avg_score[team[i]]
            for i in range(6) if i not in set(best_covered)
        )
        best_score = (best_matchup_score, flex_score)

        assignment = {
            team[best_covered[j]]: opponents[best_perm[j]]
            for j in range(n_opp)
        }

        if check_conflicts and not is_valid_team(team, assignment, _variants, _raw_matchup_info):
            continue

        results.append((best_score, team, assignment))

    results.sort(key=lambda x: x[0], reverse=True)
    return results[:n]


def _score_teams_worker(args):
    """Worker function for parallel team scoring in select_best_team_e4.

    Args:
        args: (teams_chunk, normalized, valid_trainer_opps)
            teams_chunk: list of 6-pokemon tuples to score
            normalized: dict[pokemon][opp] -> float in [0,1]
            valid_trainer_opps: list[list[str]] — per-trainer opp keys

    Returns list of (team_score, team, trainer_assignments).
    """
    teams_chunk, normalized, valid_trainer_opps = args
    results = []
    for team in teams_chunk:
        matchup_scores = []   # sum of assigned matchups per trainer (no flex)
        flex_scores    = []   # flex contribution per trainer
        trainer_assignments = []

        for valid_opps in valid_trainer_opps:
            n_opp = min(len(valid_opps), 6)

            if n_opp == 0:
                matchup_scores.append(0.0)
                flex_scores.append(0.0)
                trainer_assignments.append({p: None for p in team})
                continue

            avg_score = {
                p: sum(normalized[p][o] for o in valid_opps) / len(valid_opps)
                for p in team
            }

            best_matchup = -1.0
            best_flex    = 0.0
            best_covered = None
            best_perm    = None

            for covered in itertools.combinations(range(6), n_opp):
                covered_members = [team[i] for i in covered]
                covered_set = set(covered)
                flex = sum(avg_score[team[i]] for i in range(6) if i not in covered_set)
                best_ms = -1.0
                best_perm_inner = None
                for perm in itertools.permutations(range(n_opp)):
                    ms = sum(
                        normalized[covered_members[j]][valid_opps[perm[j]]]
                        for j in range(n_opp)
                    )
                    if ms > best_ms:
                        best_ms = ms
                        best_perm_inner = perm
                if best_ms > best_matchup:
                    best_matchup = best_ms
                    best_flex    = flex
                    best_covered = covered
                    best_perm    = best_perm_inner

            assignment = {
                team[best_covered[j]]: valid_opps[best_perm[j]]
                for j in range(n_opp)
            }
            covered_set = set(best_covered)
            for i in range(6):
                if i not in covered_set:
                    assignment[team[i]] = None

            matchup_scores.append(best_matchup)
            flex_scores.append(best_flex)
            trainer_assignments.append(assignment)

        total_scores = [m + f for m, f in zip(matchup_scores, flex_scores)]
        team_score = min(total_scores) if total_scores else 0.0
        results.append((team_score, team, trainer_assignments))
    return results


def select_best_team_e4(m, n, trainer_opp_keys):
    """Return the top-n teams of 6, ranked by minimax assignment score for Elite Four.

    trainer_opp_keys: list[list[str]] — per-trainer full team opp keys.

    For each 6-pokemon combination:
    - Finds the best assignment against each trainer's full team (sum of normalized
      scores for assigned members + average score for flex members).
    - Takes the minimum per-trainer score as the team's overall score (minimax).

    Uses 12 worker processes for parallel evaluation.
    Returns list of (score, team, trainer_assignments).
    """
    NUM_WORKERS = 12
    raw_scores, normalized, my_pokemon, _ = build_scores(m.prunedMatchupInfo)

    valid_trainer_opps = [
        [o for o in opp_keys if o in normalized.get(my_pokemon[0], {})]
        for opp_keys in trainer_opp_keys
    ]

    all_combos = list(itertools.combinations(my_pokemon, 6))
    chunk_size = max(1, len(all_combos) // NUM_WORKERS)
    chunks = [all_combos[i:i + chunk_size]
              for i in range(0, len(all_combos), chunk_size)]

    results = []
    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        for chunk_results in executor.map(
            _score_teams_worker,
            [(chunk, normalized, valid_trainer_opps) for chunk in chunks]
        ):
            results.extend(chunk_results)

    results.sort(key=lambda x: x[0], reverse=True)
    return results[:n]


if __name__ == "__main__":
    with open("matchup_info.pkl", "rb") as f:
        m = pickle.load(f)

    print_matchup_rankings(m)

    best_teams = select_best_team(m, 5)
    print("=== Top 5 Teams ===")
    for score, team, assignment in best_teams:
        print(f"Score: {score:.4f}")
        print(f"  Team: {', '.join(team)}")
        for my_poke, opp_poke in assignment.items():
            print(f"    {my_poke} -> {opp_poke}")
        print()
