"""
team_analyzer.py

Parses PlayerPokemon.txt and OpponentTeam.txt (PS-paste format produced by
emerald_reader.py) into PS packed pipe strings, then builds a MatchupInfo
object that runs MCTS for every player-Pokémon vs opponent-Pokémon pairing.

Both party and PC box Pokémon are treated as equal candidates.

Usage:
    from team_analyzer import parse_player_file, parse_opponent_file, build_matchup_info

    box = parse_player_file()   # list of PS pipe strings
    opp = parse_opponent_file() # list of PS pipe strings

    m = build_matchup_info(node_script_path)
    # m.prunedMatchupInfo is a list of PrunedMatchupInfo objects
"""

import os
import pickle
import re

from MatchupInfo import MatchupInfo, print_move_stats
from NodeIPC import NodeIPC
from config import BADGE_BOOST_ATK, BADGE_BOOST_DEF, BADGE_BOOST_SP, BADGE_BOOST_SPE, \
    AVAILABLE_ITEMS, AVAILABLE_TMS
from variant_builder import _set_pipe_item, _set_pipe_moves

_HERE         = os.path.dirname(os.path.abspath(__file__))
PLAYER_FILE   = os.path.join(_HERE, "PlayerPokemon.txt")
OPPONENT_FILE = os.path.join(_HERE, "OpponentTeam.txt")

# Set to True to enable Elite Four mode: multi-trainer OpponentTeam.txt,
# minimax team selection, and faint-aware sequential simulation.
ELITE_FOUR_MODE: bool = True

_STAT_ORDER = ['HP', 'Atk', 'Def', 'SpA', 'SpD', 'Spe']


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _to_ps_id(name: str) -> str:
    """Convert display name to PS move/ability ID: lowercase, strip spaces and hyphens."""
    return re.sub(r'[-\s]', '', name).lower()


def _parse_stat_line(s: str, default: int) -> list:
    """Parse '2 HP / 10 Atk / ...' into [hp, atk, def, spa, spd, spe].

    Any stat not mentioned defaults to `default` (0 for EVs, 31 for IVs).
    """
    vals = {stat: default for stat in _STAT_ORDER}
    for part in s.split('/'):
        part = part.strip()
        m = re.match(r'(\d+)\s+(\S+)', part)
        if m:
            val, label = int(m.group(1)), m.group(2)
            if label in vals:
                vals[label] = val
    return [vals[s] for s in _STAT_ORDER]


def _format_stat_field(vals: list, skip_val: int) -> str:
    """Format [hp, atk, def, spa, spd, spe] as a PS EV/IV field string.

    Positions equal to skip_val become ''. Trailing '' entries are omitted.
    Example: [2,1,0,0,1,4] with skip 0 → '2,1,,,1,4'
    """
    parts = [str(v) if v != skip_val else '' for v in vals]
    while parts and parts[-1] == '':
        parts.pop()
    return ','.join(parts)


# ─── Block parser ─────────────────────────────────────────────────────────────

def _parse_block(lines: list) -> str:
    """Parse the lines for a single Pokémon into a PS packed pipe string.

    Expected line types (PS paste format):
      Name           — first non-empty line
      Ability: X
      Level: N
      EVs: X HP / Y Atk / ...
      IVs: X HP / Y Atk / ...
      NatureName     — any other plain word after the name
      - MoveName

    Returns the packed pipe string, or None if no name was found.
    """
    name = ability = nature = level_str = None
    item = ''
    evs  = [0]  * 6   # default 0 per stat
    ivs  = [31] * 6   # default 31 per stat
    moves = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('Ability:'):
            ability = _to_ps_id(line[len('Ability:'):].strip())
        elif line.startswith('Level:'):
            level_str = line[len('Level:'):].strip()
        elif line.startswith('EVs:'):
            evs = _parse_stat_line(line[len('EVs:'):].strip(), 0)
        elif line.startswith('IVs:'):
            ivs = _parse_stat_line(line[len('IVs:'):].strip(), 31)
        elif line.startswith('-'):
            moves.append(_to_ps_id(line[1:].strip()))
        elif name is None:
            if ' @ ' in line:
                name, raw_item = line.split(' @ ', 1)
                item = re.sub(r"[-\s'.]", '', raw_item).lower()
            else:
                name = line
        else:
            nature = line   # first plain word after name is the nature

    if name is None:
        return None

    # Strip gender suffix embedded by to_showdown and route it to the pipe gender field
    gender = ''
    for suffix in (' (M)', ' (F)'):
        if name.endswith(suffix):
            gender = suffix[-2]   # 'M' or 'F'
            name = name[:-len(suffix)]
            break

    ev_str    = _format_stat_field(evs, skip_val=0)
    iv_str    = _format_stat_field(ivs, skip_val=31)
    moves_str = ','.join(moves)
    ability   = ability   or ''
    nature    = nature    or ''
    level_str = level_str or '100'

    # PS packed format: nickname|species|item|ability|moves|nature|evs|gender|ivs|shiny|level|
    # species left empty — PS infers from nickname
    return f"{name}||{item}|{ability}|{moves_str}|{nature}|{ev_str}|{gender}|{iv_str}||{level_str}|"


# ─── File parsers ─────────────────────────────────────────────────────────────

def _split_into_blocks(text: str, skip_pattern=None) -> list:
    """Split text into non-empty line groups, optionally filtering out lines by regex."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        if skip_pattern and re.match(skip_pattern, stripped):
            continue
        lines.append(stripped)

    blocks = []
    current = []
    for line in lines:
        if line:
            current.append(line)
        else:
            if current:
                blocks.append(current)
                current = []
    if current:
        blocks.append(current)
    return blocks


def parse_player_file(path: str = PLAYER_FILE) -> list:
    """Parse PlayerPokemon.txt into a flat list of PS pipe strings.

    Both active party and PC box Pokémon are included with no distinction.
    """
    with open(path, encoding='utf-8') as f:
        text = f.read()
    blocks = _split_into_blocks(text, skip_pattern=r'^===')
    return [s for s in (_parse_block(b) for b in blocks) if s is not None]


def parse_opponent_file(path: str = OPPONENT_FILE) -> list:
    """Parse OpponentTeam.txt into a list of PS pipe strings."""
    with open(path, encoding='utf-8') as f:
        text = f.read()
    blocks = _split_into_blocks(text)
    return [s for s in (_parse_block(b) for b in blocks) if s is not None]


def parse_opponent_file_e4(path: str = OPPONENT_FILE) -> list[tuple[str, list[str]]]:
    """Parse a multi-trainer OpponentTeam.txt into an ordered list of (trainer, pipes) tuples.

    Trainers are separated by lines of the form:
        Trainer: Sidney

    Pokemon blocks within each trainer section use the standard PS paste format.
    Returns [("Sidney", [pipe1, ...]), ("Phoebe", [...]), ...] in file order.
    The list order defines the battle sequence — earlier trainers are fought first.
    """
    _TRAINER_RE = re.compile(r'^Trainer:\s*(.+)', re.IGNORECASE)

    with open(path, encoding='utf-8') as f:
        text = f.read()

    trainers: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []

    def _flush():
        if current_name is None:
            return
        blocks = _split_into_blocks('\n'.join(current_lines))
        pipes = [s for s in (_parse_block(b) for b in blocks) if s is not None]
        trainers.append((current_name, pipes))

    for line in text.splitlines():
        stripped = line.strip()
        m = _TRAINER_RE.match(stripped)
        if m:
            _flush()
            current_name = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    _flush()
    return trainers


# ─── MatchupInfo builder ──────────────────────────────────────────────────────

def build_matchup_info(node_script_path: str, level_multiplier: float = 1.0,
                       num_workers: int = 15,
                       num_runs: int = 3) -> MatchupInfo:
    """Parse the capture files and run shallow search for all player vs opponent pairings.

    Returns a MatchupInfo whose prunedMatchupInfo contains one entry per pairing.
    """
    box_raw = parse_player_file()
    opp_raw = parse_opponent_file()

    box_raw = box_raw

    badge_boosts = {
        'atkBoost': BADGE_BOOST_ATK,
        'defBoost': BADGE_BOOST_DEF,
        'spaBoost': BADGE_BOOST_SP,
        'spdBoost': BADGE_BOOST_SP,
        'speBoost': BADGE_BOOST_SPE,
    }

    return MatchupInfo(
        node_script_path,
        level_multiplier=level_multiplier,
        num_workers=num_workers,
        num_runs=num_runs,
        box_raw=box_raw,
        opp_raw=opp_raw,
        badge_boosts=badge_boosts,
    )


def build_matchup_info_e4(node_script_path: str, trainers: list[tuple[str, list[str]]],
                           level_multiplier: float = 1.0,
                           num_workers: int = 8,
                           num_runs: int = 3) -> tuple[MatchupInfo, list[tuple[str, list[str]]]]:
    """Build matchup info for Elite Four mode.

    Flattens all trainer pokemon into a single opponent pool (deduplicating by
    pipe string), runs MatchupInfo against that pool, and returns both the
    MatchupInfo and the original trainers list for use in simulation.

    Returns (matchup_info, trainers).
    """
    box_raw = parse_player_file()

    seen = set()
    opp_raw = []
    for _, pipes in trainers:
        for pipe in pipes:
            if pipe not in seen:
                seen.add(pipe)
                opp_raw.append(pipe)

    badge_boosts = {
        'atkBoost': BADGE_BOOST_ATK,
        'defBoost': BADGE_BOOST_DEF,
        'spaBoost': BADGE_BOOST_SP,
        'spdBoost': BADGE_BOOST_SP,
        'speBoost': BADGE_BOOST_SPE,
    }

    matchup_info = MatchupInfo(
        node_script_path,
        level_multiplier=level_multiplier,
        num_workers=num_workers,
        num_runs=num_runs,
        box_raw=box_raw,
        opp_raw=opp_raw,
        badge_boosts=badge_boosts,
    )
    return matchup_info, trainers


def compute_e4_berry_recs(
    node_script_path: str,
    matchup_info,
    trainers: list[tuple[str, list[str]]],
    num_workers: int = 12,
    num_runs: int = 3,
) -> dict[str, dict[str, str]]:
    """Run lum-berry matchups for box pokemon without held items.

    For each such pokemon, simulates all opponent matchups with a Lum Berry
    equipped, aggregates the statuses absorbed per trainer, and computes the
    optimal cure berry for each trainer fight.

    Returns {poke_name: {trainer_name: berry_ps_id}}.
    """
    from MatchupInfo import _run_matchup_context
    from search_process import ShallowSearchProcess
    from berry_logic import assign_berry

    # Find box pokemon with no held item; set item field to lumberry
    lum_box = {}
    for poke_name, pipe in matchup_info.box.items():
        fields = pipe.split('|')
        if not fields[2]:
            moves = [m.lower().replace(' ', '').replace('-', '') for m in fields[4].split(',') if m]
            fields[2] = 'chestoberry' if 'rest' in moves else 'lumberry'
            lum_box[poke_name] = '|'.join(fields)

    if not lum_box:
        print("No box pokemon without held items — skipping lum berry analysis.")
        return {}

    # Map matchup_info.opp keys to trainer names.
    # opp keys may be deduplicated (e.g. "Dusclops#1") so strip the suffix before matching.
    trainer_opp_names = []
    for trainer_name, pipes in trainers:
        trainer_species = {p.split('|')[0] for p in pipes}
        opp_names_for_trainer = [
            opp_name for opp_name in matchup_info.opp
            if opp_name.split('#')[0] in trainer_species
        ]
        trainer_opp_names.append((trainer_name, opp_names_for_trainer))

    total = len(lum_box) * len(matchup_info.opp)
    print(f"\n=== Lum Berry Analysis: {len(lum_box)} candidates × "
          f"{len(matchup_info.opp)} opponents = {total} pairs ===")

    raw_lum = {name: {} for name in lum_box}

    ipc = NodeIPC(node_script_path)
    search_proc = None
    try:
        search_proc = ShallowSearchProcess(node_script_path, num_workers=num_workers,
                                           verbose=False)
        decision_fn = search_proc.search
        completed = 0

        for poke_name, lum_pipe in lum_box.items():
            for opp_name, opp_pipe in matchup_info.opp.items():
                result = _run_matchup_context(
                    ipc=ipc, decision_fn=decision_fn,
                    team1=lum_pipe, team2=opp_pipe,
                    setup_p1=None, setup_p2=None,
                    num_runs=num_runs,
                    badge_boosts=matchup_info.badge_boosts,
                    is_lum=True,
                    max_runs=3,
                    turn_limit=5
                )
                raw_lum[poke_name][opp_name] = result
                completed += 1
                statuses = result.lum_statuses or []
                print(f"  {poke_name} vs {opp_name}: statuses={statuses}")
                if completed % 10 == 0 or completed == total:
                    print(f"  {completed}/{total} pairs done")
    finally:
        if search_proc is not None:
            search_proc.close()
        ipc.close()

    # Aggregate lum_statuses per trainer and compute berry recommendation
    berry_recs = {}
    for poke_name in lum_box:
        trainer_statuses = {}
        for trainer_name, opp_names in trainer_opp_names:
            statuses = []
            for opp_name in opp_names:
                result = raw_lum[poke_name].get(opp_name)
                if result:
                    statuses.extend(result.lum_statuses or [])
            trainer_statuses[trainer_name] = statuses
        berry_recs[poke_name] = assign_berry(trainer_statuses)

    return berry_recs


def print_berry_recs(berry_recs: dict[str, dict[str, str]]) -> None:
    """Print E4 berry recommendations in a readable format."""
    if not berry_recs:
        return
    print("\n=== Berry Recommendations ===")
    for poke_name, trainer_berries in berry_recs.items():
        print(f"  {poke_name}:")
        for trainer_name, berry in trainer_berries.items():
            print(f"    vs {trainer_name}: {berry}")
    print()


# ─── Variant matchup builder ──────────────────────────────────────────────────

def _level_pipe(pipe: str, multiplier: float) -> str:
    """Apply a level multiplier to the level field (index -2) of a PS pipe string."""
    fields = pipe.split('|')
    try:
        natural = int(fields[-2]) if fields[-2].strip() else 100
    except ValueError:
        natural = 100
    fields[-2] = str(max(1, int(natural * multiplier)))
    return '|'.join(fields)


class VariantMatchupInfo:
    """Matchup results for all (item×move variant) × opponent pairings.

    Drop-in replacement for MatchupInfo in find_best_surviving_team and
    select_best_team: exposes the same box, opp, badge_boosts, and
    prunedMatchupInfo attributes.
    """

    def __init__(self, rawMatchupInfo, prunedMatchupInfo, variants,
                 berry_map, box, opp, badge_boosts):
        self.rawMatchupInfo    = rawMatchupInfo
        self.prunedMatchupInfo = prunedMatchupInfo
        self.variants          = variants   # dict[var_key → VariantInfo]
        self.berry_map         = berry_map  # dict[var_key → {opp_name: berry_ps_id}]
        self.box               = box        # dict[var_key → leveled pipe string]
        self.opp               = opp        # dict[opp_name → pipe string]
        self.badge_boosts      = badge_boosts

    def get_battle_pipe(self, var_key: str, assigned_opp: str | None) -> str:
        """Return the pipe string for the 6v6 battle with correct item and moves.

        For lum variants: substitutes the appropriate cure berry.
        For expanded variants: substitutes the 4 locked moves for assigned_opp
            (falls back to original_moves for flex/unassigned slots).
        """
        pipe = self.box[var_key]
        var  = self.variants.get(var_key)
        if var is None:
            return pipe

        # Berry substitution for lum variants
        if var.is_lum:
            berry_per_opp = self.berry_map.get(var_key, {})
            if assigned_opp and assigned_opp in berry_per_opp:
                berry = berry_per_opp[assigned_opp]
            else:
                from berry_logic import best_berry_for_flex
                scores_per_opp = {
                    opp_name: self.rawMatchupInfo[var_key][opp_name][None].score
                    for opp_name in self.rawMatchupInfo.get(var_key, {})
                }
                berry = best_berry_for_flex(scores_per_opp, berry_per_opp)
            pipe = _set_pipe_item(pipe, berry)

        # Move substitution for expanded variants
        if var.move_tag == 'expanded':
            moves = None
            if assigned_opp:
                result = self.rawMatchupInfo.get(var_key, {}).get(assigned_opp, {}).get(None)
                if result and result.locked_moves:
                    moves = result.locked_moves
            pipe = _set_pipe_moves(pipe, moves if moves is not None else var.original_moves)

        return pipe


def build_variant_matchup_info(node_script_path: str, level_multiplier: float = 1.0,
                                num_workers: int = 15,
                                num_runs: int = 3) -> VariantMatchupInfo:
    """Parse capture files, build all (item×move) variants, and run matchup sims.

    Returns a VariantMatchupInfo whose prunedMatchupInfo contains one entry
    per (variant_key, opponent) pairing.
    """
    from MatchupInfo import _run_matchup_context, PrunedMatchupInfo, get_teams_at_level, MatchupResult
    from search_process import ShallowSearchProcess
    from variant_builder import build_variants
    from berry_logic import assign_berry

    box_raw = parse_player_file()
    opp_raw = parse_opponent_file()

    badge_boosts = {
        'atkBoost': BADGE_BOOST_ATK,
        'defBoost': BADGE_BOOST_DEF,
        'spaBoost': BADGE_BOOST_SP,
        'spdBoost': BADGE_BOOST_SP,
        'speBoost': BADGE_BOOST_SPE,
    }

    # Opponent dict (no level scaling for opponents)
    _, opp = get_teams_at_level(1.0, box_raw=[], opp_raw=opp_raw)

    # Build all (item × move) variant combinations for player pokemon
    variants = build_variants(box_raw, AVAILABLE_ITEMS, AVAILABLE_TMS)

    # Apply level multiplier to each variant's pipe string
    leveled_box = {var_key: _level_pipe(var_info.pipe_string, level_multiplier)
                   for var_key, var_info in variants.items()}

    total = len(variants) * len(opp)
    print(f"Running variant matchup info: {len(variants)} variants × {len(opp)} opponents "
          f"= {total} pairs (multiplier={level_multiplier}, workers={num_workers}, "
          f"min_runs={num_runs})...")

    # ── Run all matchup simulations ─────────────────────────────────────────
    raw_results = {var_key: {} for var_key in variants}

    ipc = NodeIPC(node_script_path)
    search_proc = None
    try:
        search_proc = ShallowSearchProcess(node_script_path, num_workers=num_workers,
                                           verbose=False)
        decision_fn = search_proc.search
        completed   = 0

        for var_key, var_info in variants.items():
            is_expanded = var_info.move_tag == 'expanded'
            for opp_name, opp_pipe in opp.items():
                result = _run_matchup_context(
                    ipc=ipc, decision_fn=decision_fn,
                    team1=leveled_box[var_key], team2=opp_pipe,
                    setup_p1=None, setup_p2=None,
                    num_runs=num_runs,
                    badge_boosts=badge_boosts,
                    move_pool=var_info.move_pool       if is_expanded else None,
                    original_moves=var_info.original_moves if is_expanded else None,
                    is_lum=var_info.is_lum,
                )
                raw_results[var_key][opp_name] = result
                completed += 1
                tag = ' [sentinel]' if result.num_runs == 0 else ''
                print(f"  {var_key} vs {opp_name}: {result.score:+.3f}{tag}, numBattles={result.num_runs}")
                print(f"numTurns={result.turnCounts}")
                print(f"scores={[round(s, 2) for s in result.rawScores]}")
                print_move_stats(result)
                print()
                if completed % 10 == 0 or completed == total:
                    print(f"  {completed}/{total} pairs done")
                    print()
    finally:
        if search_proc is not None:
            search_proc.close()
        ipc.close()

    # ── Expanded score correction ────────────────────────────────────────────
    # Sentinel (num_runs==0) means expanded variant locked on the original moves.
    # Replace sentinel results with the corresponding orig variant's score.
    # If ALL matchups for an expanded variant are sentinel, discard it entirely.
    discard_keys = set()
    for var_key, var_info in variants.items():
        if var_info.move_tag != 'expanded':
            continue
        orig_key = f'{var_info.species}|{var_info.item_tag}|orig'
        results  = raw_results[var_key]

        if all(r.num_runs == 0 for r in results.values()):
            discard_keys.add(var_key)
            continue

        for opp_name, result in results.items():
            if result.num_runs == 0:
                orig_result = raw_results.get(orig_key, {}).get(opp_name)
                if orig_result:
                    raw_results[var_key][opp_name] = MatchupResult(
                        score=orig_result.score,
                        action_stats={},
                        num_runs=orig_result.num_runs,
                        variance=orig_result.variance,
                        lum_statuses=orig_result.lum_statuses,
                        locked_moves=list(var_info.original_moves) if var_info.original_moves else [],
                    )

    for key in discard_keys:
        del raw_results[key]
        del leveled_box[key]
        del variants[key]
    if discard_keys:
        print(f"  Discarded {len(discard_keys)} all-sentinel expanded variant(s): "
              f"{', '.join(sorted(discard_keys))}")

    # ── Build rawMatchupInfo in {var_key: {opp_name: {None: result}}} format ─
    raw_matchup_info = {
        var_key: {opp_name: {None: result} for opp_name, result in opp_results.items()}
        for var_key, opp_results in raw_results.items()
    }

    # ── Build prunedMatchupInfo ──────────────────────────────────────────────
    pruned = []
    for var_key, opp_results in raw_matchup_info.items():
        for opp_name, contexts in opp_results.items():
            pruned.append(PrunedMatchupInfo(
                myPokemon=var_key, opponentPokemon=opp_name,
                none=contexts[None],
                move1=None, move2=None, move3=None, move4=None,
            ))

    # ── Berry map for lum variants ───────────────────────────────────────────
    berry_map = {}
    for var_key, var_info in variants.items():
        if not var_info.is_lum:
            continue
        lum_per_opp = {
            opp_name: (raw_results[var_key][opp_name].lum_statuses or [])
            for opp_name in opp
            if opp_name in raw_results.get(var_key, {})
        }
        berry_map[var_key] = assign_berry(lum_per_opp)

    return VariantMatchupInfo(
        rawMatchupInfo=raw_matchup_info,
        prunedMatchupInfo=pruned,
        variants=variants,
        berry_map=berry_map,
        box=leveled_box,
        opp=opp,
        badge_boosts=badge_boosts,
    )


def _print_variant_team(ordered: list, assignment: dict, matchup_info: VariantMatchupInfo):
    """Print item and move assignments for each team member."""
    print("[team] Final team assignments:")
    for var_key in ordered:
        var          = matchup_info.variants[var_key]
        assigned_opp = assignment.get(var_key)
        role         = f"vs {assigned_opp}" if assigned_opp else "(flex)"

        # Resolve item
        if var.is_lum:
            berry_per_opp = matchup_info.berry_map.get(var_key, {})
            if assigned_opp and assigned_opp in berry_per_opp:
                item_str = berry_per_opp[assigned_opp]
            else:
                from berry_logic import best_berry_for_flex
                scores_per_opp = {
                    o: matchup_info.rawMatchupInfo[var_key][o][None].score
                    for o in matchup_info.rawMatchupInfo.get(var_key, {})
                }
                item_str = best_berry_for_flex(scores_per_opp, berry_per_opp)
        else:
            item_str = var.item_tag  # 'orig' means unchanged original item

        # Resolve moves
        if var.move_tag == 'expanded' and assigned_opp:
            result = matchup_info.rawMatchupInfo.get(var_key, {}).get(assigned_opp, {}).get(None)
            moves  = result.locked_moves if result and result.locked_moves else var.original_moves
        else:
            moves = var.original_moves

        print(f"  {var_key:<35} {role:<20} item={item_str}, moves={moves}")


# ─── Team selector ────────────────────────────────────────────────────────────

def _play_full_game(ipc, search_proc, player_team_str: str, opp_team_str: str,
                    badge_boosts: dict, turn_limit: int = 200):
    """Play one complete simulated battle using shallow search for p1 decisions.

    Returns (winner, any_p1_fainted) where winner is 'p1', 'p2', or None,
    and any_p1_fainted is True if any p1 Pokémon ended with hp == 0.
    """
    from battle_sim import parse_ipc_response
    from MatchupInfo import _apply_turn
    from battle_mode import _flags_for_trainer
    from config import APPROVED_OPPONENT_ITEMS, OPP_ITEMS, TRAINER_NAME

    raw = ipc.send({"new": True, "team1": player_team_str, "team2": opp_team_str,
                    **(badge_boosts or {})})
    state = parse_ipc_response(raw)
    if OPP_ITEMS and APPROVED_OPPONENT_ITEMS:
        state['opp_items_remaining'] = OPP_ITEMS
        state['opp_items_initial']   = OPP_ITEMS
        state['opp_item_ps_id']      = APPROVED_OPPONENT_ITEMS
    state['opp_ai_flags'] = _flags_for_trainer(TRAINER_NAME)

    turn = 0
    p1_move_log: list[str] = []
    p2_move_log: list[str] = []

    while not state["is_over"] and turn < turn_limit:
        p1_actions = state.get("p1_moves") or state.get("p1_switches")
        if not p1_actions:
            break
        action, _ = search_proc.search(state)

        # Record p1's move before the turn (slots stable pre-turn)
        from MatchupInfo import _extract_p1_move_used
        p1_used = _extract_p1_move_used(state, action) or action

        state = _apply_turn(ipc, state, action)
        turn += 1

        # Record p2's last move from post-turn state
        try:
            last = state['battle']['sides'][1]['pokemon'][0].get('lastMove') or {}
            raw = last.get('move', '') if isinstance(last, dict) else ''
            # PS stores lastMove as '[Move:moveid]' — strip the brackets
            p2_used = raw.strip('[').removeprefix('Move:').rstrip(']') if raw else '?'
        except (IndexError, KeyError):
            p2_used = '?'

        p1_move_log.append(p1_used)
        p2_move_log.append(p2_used)

    if turn >= turn_limit:
        sides = state['battle'].get('sides', [{}, {}])
        p1_mons = sides[0].get('pokemon', [])
        p2_mons = sides[1].get('pokemon', [])

        p1_name = p1_mons[0].get('name', 'p1') if p1_mons else 'p1'
        p2_name = p2_mons[0].get('name', 'p2') if p2_mons else 'p2'

        print(f"  [turn limit] {p1_name}: {', '.join(p1_move_log)}")
        print(f"  [turn limit] {p2_name}: {', '.join(p2_move_log)}")

        for side_label, mons in (('p1', p1_mons), ('p2', p2_mons)):
            for mon in mons:
                name = mon.get('name', '?')
                hp   = mon.get('hp', 0)
                maxhp = mon.get('maxhp', 1)
                slots = mon.get('moveSlots', [])
                pp_str = ', '.join(
                    f"{s.get('id') or s.get('move', '?')}:{s.get('pp', 0)}/{s.get('maxpp', 0)}"
                    for s in slots
                )
                print(f"  [turn limit]   {name}: {hp}/{maxhp} HP  [{pp_str}]")

    winner = state["winner"]
    p1_side = state["battle"].get("sides", [{}])[0]
    any_p1_fainted = any(p.get("hp", 0) == 0 for p in p1_side.get("pokemon", []))
    return winner, any_p1_fainted


def find_best_surviving_team(node_script_path: str, matchup_info,
                              n_games: int = 3,
                              num_workers: int = 15):
    """Try teams in ranked order; return first that wins n_games without any Pokémon fainting.

    Teams with the same lead and same set of non-lead members are considered identical
    and skipped on subsequent encounters.

    Prints progress as teams are tried, games are played, and results are determined.

    Returns (score, team_tuple, assignment_dict) for the winning team, or None if all fail.
    """
    from battle_sim import assemble_team_string, assemble_opponent_string, reorder_team
    from test2 import build_scores, select_best_team
    from search_process import ShallowSearchProcess

    is_variant = isinstance(matchup_info, VariantMatchupInfo)

    raw_scores, _, _, opponents = build_scores(matchup_info.prunedMatchupInfo)
    all_teams = select_best_team(matchup_info, n=10 ** 9)
    opp_first = next(iter(matchup_info.opp))

    ipc = NodeIPC(node_script_path)
    search_proc = None
    try:
        search_proc = ShallowSearchProcess(node_script_path, num_workers=num_workers,
                                           verbose=False)

        tried = set()
        rank = 0
        for score, team, assignment in all_teams:
            ordered = reorder_team(list(team), assignment, opponents)
            dedup_key = (ordered[0], frozenset(ordered[1:]))
            if dedup_key in tried:
                continue
            tried.add(dedup_key)
            rank += 1

            print(f"\n=== Team #{rank}  score={score} ===")
            print(f"  Members (lead first): {', '.join(ordered)}")
            print("  Matchup data:")
            for p in ordered:
                scores_str = "  ".join(
                    f"{opp}: {raw_scores[p][opp]:+.3f}" for opp in opponents
                )
                print(f"    {p:<12}  {scores_str}")

            # For variant matchups, substitute the correct berry and locked moves
            if is_variant:
                battle_box = {k: matchup_info.get_battle_pipe(k, assignment.get(k))
                              for k in ordered}
            else:
                battle_box = matchup_info.box
            player_str = assemble_team_string(ordered, battle_box)
            opp_str = assemble_opponent_string(opp_first, matchup_info.opp)

            team_ok = True
            for game_num in range(1, n_games + 1):
                winner, any_faint = _play_full_game(ipc, search_proc, player_str, opp_str,
                                                    matchup_info.badge_boosts)
                if winner != 'p1' or any_faint:
                    reason = "faint" if any_faint else "loss"
                    print(f"  Game {game_num}: FAILED ({reason}) — moving to next team")
                    team_ok = False
                    break
                print(f"  Game {game_num}: WIN — no faints")

            if team_ok:
                print(f"\n=== Selected team: {', '.join(ordered)} ===")
                if is_variant:
                    _print_variant_team(ordered, assignment, matchup_info)
                return score, team, assignment

        print("\nAll teams failed.")
        return None
    finally:
        if search_proc is not None:
            search_proc.close()
        ipc.close()


# ─── Elite Four simulation ────────────────────────────────────────────────────

def _play_full_game_e4(ipc, search_proc, player_team_str: str, opp_team_str: str,
                       badge_boosts: dict, trainer_name: str, turn_limit: int = 200):
    """Play one complete simulated battle for Elite Four mode.

    Returns (winner, fainted_names) where fainted_names is a set of player
    pokemon species names (matching box keys) that ended the battle with hp == 0.
    """
    from battle_sim import parse_ipc_response
    from MatchupInfo import _apply_turn, _extract_p1_move_used
    from battle_mode import _flags_for_trainer
    from config import APPROVED_OPPONENT_ITEMS, OPP_ITEMS

    raw = ipc.send({"new": True, "team1": player_team_str, "team2": opp_team_str,
                    **(badge_boosts or {})})
    state = parse_ipc_response(raw)
    if OPP_ITEMS and APPROVED_OPPONENT_ITEMS:
        state['opp_items_remaining'] = OPP_ITEMS
        state['opp_items_initial']   = OPP_ITEMS
        state['opp_item_ps_id']      = APPROVED_OPPONENT_ITEMS
    state['opp_ai_flags'] = _flags_for_trainer(trainer_name)

    turn = 0
    p1_move_log: list[str] = []
    p2_move_log: list[str] = []

    while not state["is_over"] and turn < turn_limit:
        p1_actions = state.get("p1_moves") or state.get("p1_switches")
        if not p1_actions:
            break
        action, _ = search_proc.search(state)
        p1_used = _extract_p1_move_used(state, action) or action

        state = _apply_turn(ipc, state, action)
        turn += 1

        try:
            last = state['battle']['sides'][1]['pokemon'][0].get('lastMove') or {}
            raw_move = last.get('move', '') if isinstance(last, dict) else ''
            p2_used = raw_move.strip('[').removeprefix('Move:').rstrip(']') if raw_move else '?'
        except (IndexError, KeyError):
            p2_used = '?'

        p1_move_log.append(p1_used)
        p2_move_log.append(p2_used)

    if turn >= turn_limit:
        sides = state['battle'].get('sides', [{}, {}])
        p1_mons = sides[0].get('pokemon', [])
        p2_mons = sides[1].get('pokemon', [])
        p1_name = p1_mons[0].get('name', 'p1') if p1_mons else 'p1'
        p2_name = p2_mons[0].get('name', 'p2') if p2_mons else 'p2'
        print(f"  [turn limit] {p1_name}: {', '.join(p1_move_log)}")
        print(f"  [turn limit] {p2_name}: {', '.join(p2_move_log)}")
        for side_label, mons in (('p1', p1_mons), ('p2', p2_mons)):
            for mon in mons:
                name  = mon.get('name', '?')
                hp    = mon.get('hp', 0)
                maxhp = mon.get('maxhp', 1)
                slots = mon.get('moveSlots', [])
                pp_str = ', '.join(
                    f"{s.get('id') or s.get('move', '?')}:{s.get('pp', 0)}/{s.get('maxpp', 0)}"
                    for s in slots
                )
                print(f"  [turn limit]   {name}: {hp}/{maxhp} HP  [{pp_str}]")

    winner = state["winner"]
    p1_mons = state["battle"].get("sides", [{}])[0].get("pokemon", [])
    fainted_names = {p['name'] for p in p1_mons
                     if p.get('hp', 0) == 0 and p.get('name')}
    return winner, fainted_names


def find_best_surviving_team_e4(node_script_path: str, matchup_info: MatchupInfo,
                                 trainers: list[tuple[str, list[str]]],
                                 berry_recs: dict = None,
                                 n_games: int = 10,
                                 num_workers: int = 15):
    """Elite Four: find the first team (by minimax score) that wins all 5 trainers.

    For each team:
      - Plays n_games per trainer in order.
      - Any loss → team rejected immediately.
      - Pokemon that fainted in any game against a trainer are excluded from
        subsequent trainers (union of faints across all n_games for that trainer).
      - Lead for each trainer is chosen by reorder_team using the existing assignment.

    Returns (score, team, assignment) or None if all teams fail.
    """
    from battle_sim import assemble_team_string, assemble_opponent_string, reorder_team
    from test2 import select_best_team_e4, build_scores
    from search_process import ShallowSearchProcess

    badge_boosts = matchup_info.badge_boosts or {}

    # ── Build pipe→opp_key mapping ───────────────────────────────────────────
    # Replicate get_teams_at_level's deduplication: exact-duplicate pipe strings
    # collapse to one entry; same-species duplicates get #N suffixes.
    seen_pipes: set = set()
    opp_raw_ordered: list = []
    for _, pipes in trainers:
        for pipe in pipes:
            if pipe not in seen_pipes:
                seen_pipes.add(pipe)
                opp_raw_ordered.append(pipe)

    name_total: dict[str, int] = {}
    for pipe in opp_raw_ordered:
        name = pipe.split('|')[0]
        name_total[name] = name_total.get(name, 0) + 1

    name_seen: dict[str, int] = {}
    pipe_to_key: dict[str, str] = {}
    for pipe in opp_raw_ordered:
        name = pipe.split('|')[0]
        if name_total[name] == 1:
            pipe_to_key[pipe] = name
        else:
            name_seen[name] = name_seen.get(name, 0) + 1
            pipe_to_key[pipe] = f"{name}#{name_seen[name]}"

    # Per-trainer opp keys (deduplicated within each trainer)
    trainer_opp_keys: list[list[str]] = []
    for _, pipes in trainers:
        seen_t: set = set()
        keys: list[str] = []
        for pipe in pipes:
            if pipe not in seen_t:
                seen_t.add(pipe)
                keys.append(pipe_to_key[pipe])
        trainer_opp_keys.append(keys)

    # Raw lead name (for assemble_opponent_string which uses the raw opp_dict)
    trainer_leads_raw = [pipes[0].split('|')[0] if pipes else None
                         for _, pipes in trainers]

    raw_scores, normalized, my_pokemon, _ = build_scores(matchup_info.prunedMatchupInfo)

    all_teams = select_best_team_e4(matchup_info, n=10 ** 9,
                                    trainer_opp_keys=trainer_opp_keys)

    ipc = NodeIPC(node_script_path)
    search_proc = None
    try:
        search_proc = ShallowSearchProcess(node_script_path, num_workers=num_workers,
                                           verbose=False)

        tried = set()
        rank = 0
        for score, team, trainer_assignments in all_teams:
            # Order team by first trainer's assignment for display and dedup
            first_assign = trainer_assignments[0] if trainer_assignments else {}
            ordered = reorder_team(list(team), first_assign, trainer_opp_keys[0])
            dedup_key = (ordered[0], frozenset(ordered[1:]))
            if dedup_key in tried:
                continue
            tried.add(dedup_key)
            rank += 1

            print(f"\n=== E4 Team #{rank}  minimax_score={score:.4f} ===")
            print(f"  Members: {', '.join(ordered)}")

            # Print complete matchup info for all trainers
            for i, (trainer_name, _) in enumerate(trainers):
                t_opps = trainer_opp_keys[i]
                t_assign = trainer_assignments[i]
                inv_assign = {opp: p for p, opp in t_assign.items()}
                t_score = sum(
                    normalized.get(p, {}).get(opp, 0)
                    for p, opp in t_assign.items()
                )
                # Row headers: NAME @ berry for each of my pokemon
                row_headers = []
                for p in ordered:
                    berry = (berry_recs or {}).get(p, {}).get(trainer_name, '')
                    row_headers.append(f"{p} @ {berry}" if berry else p)
                row_w = max(len(h) for h in row_headers) + 2

                # Column headers: opponent names, plus (flex) if any flex members
                flex_members = {p for p, opp in t_assign.items() if opp is None}
                col_headers = t_opps + (['(avg)'] if flex_members else [])
                col_w = max((len(h) for h in col_headers), default=8) + 2
                col_w = max(col_w, 9)  # minimum width for score cell

                t_score = sum(
                    normalized.get(p, {}).get(opp, 0)
                    for p, opp in t_assign.items() if opp is not None
                ) + sum(
                    (sum(normalized.get(p, {}).get(o, 0) for o in t_opps) / len(t_opps)
                     if t_opps else 0.0)
                    for p, opp in t_assign.items() if opp is None
                )
                print(f"  [{trainer_name}]  assignment_score={t_score:.3f}")

                # Header row (opponents as columns)
                print(f"    {' ' * row_w}{''.join(h.ljust(col_w) for h in col_headers)}")

                # One row per pokemon
                for p, row_hdr in zip(ordered, row_headers):
                    assigned_opp = t_assign.get(p)
                    cells = []
                    for opp in t_opps:
                        score = raw_scores.get(p, {}).get(opp, 0)
                        cell = f"[{score:+.3f}]" if opp == assigned_opp else f" {score:+.3f} "
                        cells.append(cell.ljust(col_w))
                    if flex_members:
                        if p in flex_members and t_opps:
                            avg = sum(raw_scores.get(p, {}).get(o, 0) for o in t_opps) / len(t_opps)
                            cells.append(f" {avg:+.3f}~".ljust(col_w))
                        else:
                            cells.append(' ' * col_w)
                    print(f"    {row_hdr:<{row_w}}{''.join(cells)}")
            print()

            success = True
            available = list(team)

            for i, (trainer_name, trainer_pipes) in enumerate(trainers):
                if not available:
                    print(f"  [{trainer_name}] No pokemon remaining — team failed")
                    success = False
                    break

                # Build raw opp dict for battle string assembly
                opp_dict: dict[str, str] = {}
                for pipe in trainer_pipes:
                    opp_dict[pipe.split('|')[0]] = pipe

                # Order available team members using this trainer's assignment
                t_assign_available = {p: opp for p, opp in trainer_assignments[i].items()
                                      if p in available}
                trainer_ordered = reorder_team(
                    [p for p in ordered if p in available],
                    t_assign_available,
                    trainer_opp_keys[i],
                )
                opp_str = assemble_opponent_string(trainer_leads_raw[i], opp_dict)

                fainted_this_trainer: set[str] = set()
                trainer_ok = True

                # Build berry-substituted box for this trainer
                trainer_box = {}
                for p in trainer_ordered:
                    pipe = matchup_info.box[p]
                    fields = pipe.split('|')
                    if not fields[2] and berry_recs:
                        berry = berry_recs.get(p, {}).get(trainer_name, '')
                        if berry:
                            fields[2] = berry
                            pipe = '|'.join(fields)
                    trainer_box[p] = pipe

                for game_num in range(1, n_games + 1):
                    player_str = assemble_team_string(trainer_ordered, trainer_box)
                    winner, fainted = _play_full_game_e4(
                        ipc, search_proc, player_str, opp_str,
                        badge_boosts, trainer_name,
                    )
                    if winner != 'p1':
                        print(f"  [{trainer_name}] Game {game_num}: LOSS — moving to next team")
                        trainer_ok = False
                        success = False
                        break
                    fainted_this_trainer |= fainted
                    faint_str = f"  fainted: {fainted}" if fainted else ""
                    print(f"  [{trainer_name}] Game {game_num}: WIN{faint_str}")

                if not trainer_ok:
                    break

                available = [p for p in available if p not in fainted_this_trainer]
                print(f"  [{trainer_name}] Done. Cumulative faints: {fainted_this_trainer}. "
                      f"Remaining: {available}")

            if success:
                print(f"\n=== E4 Winning team: {', '.join(ordered)} ===")
                return score, team, trainer_assignments

        print("\nAll E4 teams failed.")
        return None
    finally:
        if search_proc is not None:
            search_proc.close()
        ipc.close()


# ─── Entry point ──────────────────────────────────────────────────────────────

NODE_SCRIPT = "/home/Fracture/WebstormProjects/pokemon-showdown-master/Connection.js"

if __name__ == "__main__":
    CACHE = "matchup_cache.pkl"

    if ELITE_FOUR_MODE:
        E4_CACHE       = "matchup_cache_e4_MOVES.pkl"
        E4_BERRY_CACHE = "matchup_cache_e4_berries_MOVES.pkl"

        if os.path.exists(E4_CACHE):
            print(f"Loading cached E4 matchup from {E4_CACHE}...")
            with open(E4_CACHE, "rb") as f:
                cached = pickle.load(f)
            # Support old 3-tuple format — strip berry_recs out so we recompute separately
            m, trainers = cached[:2]
        else:
            trainers = parse_opponent_file_e4()
            m, trainers = build_matchup_info_e4(NODE_SCRIPT, trainers)
            with open(E4_CACHE, "wb") as f:
                pickle.dump((m, trainers), f)
            print(f"Saved E4 matchup cache to {E4_CACHE}")

        if os.path.exists(E4_BERRY_CACHE):
            print(f"Loading cached berry recs from {E4_BERRY_CACHE}...")
            with open(E4_BERRY_CACHE, "rb") as f:
                berry_recs = pickle.load(f)
        else:
            berry_recs = compute_e4_berry_recs(NODE_SCRIPT, m, trainers)
            with open(E4_BERRY_CACHE, "wb") as f:
                pickle.dump(berry_recs, f)
            print(f"Saved berry recs cache to {E4_BERRY_CACHE}")

        find_best_surviving_team_e4(NODE_SCRIPT, m, trainers, berry_recs=berry_recs)
    else:
        if os.path.exists(CACHE):
            print(f"Loading cached matchup from {CACHE}...")
            with open(CACHE, "rb") as f:
                m = pickle.load(f)
        else:
            m = build_variant_matchup_info(NODE_SCRIPT)
            with open(CACHE, "wb") as f:
                pickle.dump(m, f)
            print(f"Saved matchup cache to {CACHE}")

        print(m)
        find_best_surviving_team(NODE_SCRIPT, m)
