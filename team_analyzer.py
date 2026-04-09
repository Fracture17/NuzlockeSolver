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

from MatchupInfo import MatchupInfo
from NodeIPC import NodeIPC
from config import BADGE_BOOST_ATK, BADGE_BOOST_DEF, BADGE_BOOST_SP, BADGE_BOOST_SPE

_HERE         = os.path.dirname(os.path.abspath(__file__))
PLAYER_FILE   = os.path.join(_HERE, "PlayerPokemon.txt")
OPPONENT_FILE = os.path.join(_HERE, "OpponentTeam.txt")

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

    ev_str    = _format_stat_field(evs, skip_val=0)
    iv_str    = _format_stat_field(ivs, skip_val=31)
    moves_str = ','.join(moves)
    ability   = ability   or ''
    nature    = nature    or ''
    level_str = level_str or '100'

    # PS packed format: nickname|species|item|ability|moves|nature|evs|gender|ivs|shiny|level|
    # species left empty — PS infers from nickname; gender/shiny omitted
    return f"{name}||{item}|{ability}|{moves_str}|{nature}|{ev_str}||{iv_str}||{level_str}|"


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


# ─── MatchupInfo builder ──────────────────────────────────────────────────────

def build_matchup_info(node_script_path: str, level_multiplier: float = 1.0,
                       num_workers: int = 15, mcts_iterations: int = 100,
                       num_runs: int = 3) -> MatchupInfo:
    """Parse the capture files and run MCTS for all player vs opponent pairings.

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
        mcts_iterations=mcts_iterations,
        num_runs=num_runs,
        box_raw=box_raw,
        opp_raw=opp_raw,
        badge_boosts=badge_boosts,
        use_mcts=False,
    )


# ─── Team selector ────────────────────────────────────────────────────────────

def find_best_surviving_team(node_script_path: str, matchup_info,
                              n_games: int = 3,
                              mcts_iterations: int = 100,
                              num_workers: int = 15):
    """Try teams in ranked order; return first that wins n_games without any Pokémon fainting.

    Teams with the same lead and same set of non-lead members are considered identical
    and skipped on subsequent encounters.

    Prints progress as teams are tried, games are played, and results are determined.

    Returns (score, team_tuple, assignment_dict) for the winning team, or None if all fail.
    """
    from battle_sim import (
        PokemonMCTS, play_game, assemble_team_string,
        assemble_opponent_string, reorder_team,
    )
    from test2 import build_scores, select_best_team

    raw_scores, _, _, opponents = build_scores(matchup_info.prunedMatchupInfo)
    all_teams = select_best_team(matchup_info, n=10 ** 9)
    opp_first = next(iter(matchup_info.opp))

    ipc = NodeIPC(node_script_path)
    try:
        mcts = PokemonMCTS(ipc, node_script_path=node_script_path,
                           simulation_depth_limit=10, num_workers=num_workers)

        tried = set()
        rank = 0
        for score, team, assignment in all_teams:
            ordered = reorder_team(list(team), assignment, opponents)
            dedup_key = (ordered[0], frozenset(ordered[1:]))
            if dedup_key in tried:
                continue
            tried.add(dedup_key)
            rank += 1

            print(f"\n=== Team #{rank}  score={score[0]:.4f},{score[1]:.4f} ===")
            print(f"  Members (lead first): {', '.join(ordered)}")
            print("  Matchup data:")
            for p in ordered:
                scores_str = "  ".join(
                    f"{opp}: {raw_scores[p][opp]:+.3f}" for opp in opponents
                )
                print(f"    {p:<12}  {scores_str}")

            player_str = assemble_team_string(ordered, matchup_info.box)
            opp_str = assemble_opponent_string(opp_first, matchup_info.opp)

            team_ok = True
            for game_num in range(1, n_games + 1):
                result = play_game(ipc, player_str, opp_str, mcts, mcts_iterations)
                p1_fainted = any('|faint|p1' in line for line in result.log)
                if p1_fainted or result.winner != 'p1':
                    reason = "faint" if p1_fainted else "loss"
                    print(f"  Game {game_num}: FAILED ({reason}) — moving to next team")
                    team_ok = False
                    break
                print(f"  Game {game_num}: WIN — no faints")

            if team_ok:
                print(f"\n=== Selected team: {', '.join(ordered)} ===")
                return score, team, assignment

        print("\nAll teams failed.")
        return None
    finally:
        ipc.close()


# ─── Entry point ──────────────────────────────────────────────────────────────

NODE_SCRIPT = "/home/Fracture/WebstormProjects/pokemon-showdown-master/Connection.js"

if __name__ == "__main__":
    CACHE = "matchup_cache.pkl"
    if os.path.exists(CACHE):
        print(f"Loading cached matchup from {CACHE}...")
        with open(CACHE, "rb") as f:
            m = pickle.load(f)
    else:
        m = build_matchup_info(NODE_SCRIPT)
        with open(CACHE, "wb") as f:
            pickle.dump(m, f)
        print(f"Saved matchup cache to {CACHE}")

    print(m)
    find_best_surviving_team(NODE_SCRIPT, m)
