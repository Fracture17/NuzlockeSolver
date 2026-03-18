"""
battle_mode.py

F5 battle mode: reads live team state from GBA memory, runs MCTS to pick the
best move, and injects the corresponding button sequence into the emulator.
"""

import json
import os

from emerald_reader import (
    internal_species_name,
    read_player_team,
    read_enemy_team,
    to_showdown,
)
from NodeIPC import NodeIPC
from battle_sim import PokemonMCTS, parse_ipc_response

_HERE = os.path.dirname(os.path.abspath(__file__))

# ─── Button bitmasks (must match game_loop.py) ────────────────────────────────
BTN_A     = 1
BTN_RIGHT = 16
BTN_DOWN  = 128

# Hold/release frame counts for each injected button press
_HOLD_FRAMES    = 12
_RELEASE_FRAMES = 8


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _load_db(filename: str) -> dict:
    """Load a JSON file from the same directory as this module."""
    with open(os.path.join(_HERE, filename)) as f:
        return json.load(f)


def _parse_block(text: str) -> str:
    """Convert a Pokémon Showdown text export block to a PS pipe string.

    PS packed format:
        name|species|item|ability|moves|nature|evs|gender|ivs|shiny|level|happiness

    EVs / IVs are comma-separated HP,Atk,Def,SpA,SpD,Spe.
    Empty EV slot = 0; empty IV slot = 31 (PS convention).
    """
    _NATURES = {
        "Hardy", "Lonely", "Brave", "Adamant", "Naughty",
        "Bold", "Docile", "Relaxed", "Impish", "Lax",
        "Timid", "Hasty", "Serious", "Jolly", "Naive",
        "Modest", "Mild", "Quiet", "Bashful", "Rash",
        "Calm", "Gentle", "Sassy", "Careful", "Quirky",
    }

    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]

    # First line: "Name @ Item" or just "Name"
    first = lines[0]
    if ' @ ' in first:
        name, item = first.split(' @ ', 1)
    else:
        name, item = first, ''

    ability = ''
    level   = '100'
    nature  = ''
    evs     = {'HP': 0, 'Atk': 0, 'Def': 0, 'SpA': 0, 'SpD': 0, 'Spe': 0}
    ivs     = {'HP': 31, 'Atk': 31, 'Def': 31, 'SpA': 31, 'SpD': 31, 'Spe': 31}
    moves   = []

    for line in lines[1:]:
        if line.startswith('Ability:'):
            ability = line[8:].strip()
        elif line.startswith('Level:'):
            level = line[6:].strip()
        elif line.startswith('EVs:'):
            for part in line[4:].split('/'):
                tokens = part.strip().split()
                if len(tokens) == 2:
                    evs[tokens[1]] = int(tokens[0])
        elif line.startswith('IVs:'):
            for part in line[4:].split('/'):
                tokens = part.strip().split()
                if len(tokens) == 2:
                    ivs[tokens[1]] = int(tokens[0])
        elif line.startswith('- '):
            move_name = line[2:].strip()
            move_id = move_name.lower().replace(' ', '').replace('-', '').replace("'", '')
            moves.append(move_id)
        elif line in _NATURES:
            nature = line

    # Build EV string: HP,Atk,Def,SpA,SpD,Spe (empty = 0)
    ev_order = ['HP', 'Atk', 'Def', 'SpA', 'SpD', 'Spe']
    ev_vals = [evs[k] for k in ev_order]
    if any(v != 0 for v in ev_vals):
        evs_str = ','.join(str(v) if v != 0 else '' for v in ev_vals)
    else:
        evs_str = ''

    # Build IV string: HP,Atk,Def,SpA,SpD,Spe (empty = 31)
    iv_vals = [ivs[k] for k in ev_order]
    if any(v != 31 for v in iv_vals):
        ivs_str = ','.join(str(v) if v != 31 else '' for v in iv_vals)
    else:
        ivs_str = ''

    moves_str = ','.join(moves)

    # name|species|item|ability|moves|nature|evs|gender|ivs|shiny|level|happiness
    return f"{name}||{item}|{ability}|{moves_str}|{nature}|{evs_str}||{ivs_str}||{level}|"


def _gba_team_to_pipe_strings(team: list, species_db, moves_db, items_db, abilities_db) -> list:
    """Convert a list of GBA pkmn dicts to PS pipe strings."""
    pipe_strings = []
    for pkmn in team:
        text = to_showdown(pkmn, species_db, moves_db, items_db, abilities_db)
        pipe = _parse_block(text)
        pipe_strings.append(pipe)
    return pipe_strings


def _build_init_state(team: list) -> list:
    """Build the p1InitState / p2InitState array from a GBA team list."""
    result = []
    for pkmn in team:
        result.append({
            'hp':     pkmn.get('current_hp', 0) or 0,
            'pp':     list(pkmn.get('pp', [0, 0, 0, 0])),
            'status': pkmn.get('status', '') or '',
        })
    return result


def _active_index(team: list) -> int:
    """Return the index of the first party slot with current_hp > 0."""
    for i, pkmn in enumerate(team):
        if (pkmn.get('current_hp') or 0) > 0:
            return i
    return 0


def _move_button_sequence(move_index: int) -> list:
    """Return a list of (bitmask, frames) tuples that press the given move slot.

    The GBA battle move grid:
        [0] Top-left    [1] Top-right
        [2] Bottom-left [3] Bottom-right

    Sequence: A (enter FIGHT), navigate to slot, A (confirm).
    Each button: hold _HOLD_FRAMES, then release for _RELEASE_FRAMES.
    """
    def press(bitmask):
        return [(bitmask, _HOLD_FRAMES), (0, _RELEASE_FRAMES)]

    sequence = press(BTN_A)  # open FIGHT menu

    if move_index == 0:
        pass                                          # top-left: no navigation
    elif move_index == 1:
        sequence += press(BTN_RIGHT)                  # top-right
    elif move_index == 2:
        sequence += press(BTN_DOWN)                   # bottom-left
    elif move_index == 3:
        sequence += press(BTN_RIGHT) + press(BTN_DOWN)  # bottom-right

    sequence += press(BTN_A)  # confirm move
    return sequence


# ─── Main entry point ────────────────────────────────────────────────────────

def suggest_and_queue_move(core, emu_lock, node_script_path, injected_keys,
                           mcts_iterations=200, num_workers=5):
    """Read live GBA state, run MCTS, and queue button presses for the best move.

    Args:
        core: mgba core object.
        emu_lock: threading.Lock guarding GBA memory access.
        node_script_path: Path to Connection.js.
        injected_keys: collections.deque of (bitmask, frames_remaining) tuples.
        mcts_iterations: MCTS search budget.
        num_workers: Number of parallel MCTS worker threads.
    """
    species_db   = _load_db('gen3_species.json')
    moves_db     = _load_db('gen3_move_names.json')
    items_db     = _load_db('gen3_items.json')
    abilities_db = _load_db('gen3_abilities.json')

    with emu_lock:
        player_team = read_player_team(core)
        enemy_team  = read_enemy_team(core)

    if not player_team or not enemy_team:
        print("[battle_mode] Warning: empty team — not in battle?")
        return

    # Reorder so active Pokemon leads
    p1_active = _active_index(player_team)
    p2_active = _active_index(enemy_team)

    p1_ordered = [player_team[p1_active]] + [p for i, p in enumerate(player_team) if i != p1_active]
    p2_ordered = [enemy_team[p2_active]]  + [p for i, p in enumerate(enemy_team)  if i != p2_active]

    p1_pipes = _gba_team_to_pipe_strings(p1_ordered, species_db, moves_db, items_db, abilities_db)
    p2_pipes = _gba_team_to_pipe_strings(p2_ordered, species_db, moves_db, items_db, abilities_db)
    p1_init  = _build_init_state(p1_ordered)
    p2_init  = _build_init_state(p2_ordered)

    p1_name = internal_species_name(p1_ordered[0]['species'], species_db)
    p2_name = internal_species_name(p2_ordered[0]['species'], species_db)
    print(f"[battle_mode] Active: {p1_name} (player) vs {p2_name} (opponent)")

    team1_str = ']'.join(p1_pipes)
    team2_str = ']'.join(p2_pipes)

    ipc = NodeIPC(node_script_path)
    try:
        response = ipc.send({
            'new':        True,
            'team1':      team1_str,
            'team2':      team2_str,
            'p1InitState': p1_init,
            'p2InitState': p2_init,
        })
        state = parse_ipc_response(response)

        mcts = PokemonMCTS(
            ipc,
            node_script_path=node_script_path,
            num_workers=num_workers,
        )
        best_action, root = mcts.search(state, mcts_iterations)

        # Print action stats sorted by visits
        if root.children:
            sorted_children = sorted(root.children, key=lambda c: c.visits, reverse=True)
            print("[battle_mode] MCTS action stats:")
            for c in sorted_children:
                avg = c.value / c.visits if c.visits > 0 else 0.0
                print(f"  {c.action:20s}  visits={c.visits:4d}  avg={avg:.3f}")

        if best_action is None:
            print("[battle_mode] Warning: MCTS returned no action")
            return

        if not best_action.startswith('move '):
            print(f"[battle_mode] Best action '{best_action}' is not a move — skipping injection")
            return

        move_index = int(best_action.split()[1]) - 1
        print(f"[battle_mode] Queuing: {best_action} (slot index {move_index})")
        injected_keys.extend(_move_button_sequence(move_index))

    finally:
        ipc.close()
