import pickle
import itertools


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

    results = []
    for team in itertools.combinations(my_pokemon, 6):
        avg_score = {
            p: sum(normalized[p][opp] for opp in opponents) / n_opp
            for p in team
        }

        best_score = -1.0
        best_covered_pos = None
        best_perm = None

        for covered_pos in itertools.combinations(range(6), n_opp):
            flex_pos = [i for i in range(6) if i not in set(covered_pos)]
            flex_contribution = sum(avg_score[team[i]] for i in flex_pos)
            covered_members = [team[i] for i in covered_pos]

            for perm in itertools.permutations(range(n_opp)):
                score = flex_contribution + sum(
                    normalized[covered_members[j]][opponents[perm[j]]]
                    for j in range(n_opp)
                )
                if score > best_score:
                    best_score = score
                    best_covered_pos = covered_pos
                    best_perm = perm

        assignment = {
            team[best_covered_pos[j]]: opponents[best_perm[j]]
            for j in range(n_opp)
        }
        results.append((best_score, team, assignment))

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
