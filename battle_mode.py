"""
battle_mode.py

F5 battle mode: reads live team state from GBA memory, runs MCTS to pick the
best move, and injects the corresponding button sequence into the emulator.
"""

import json
import os
import re
import struct
import subprocess

from emerald_reader import (
    internal_species_name,
    read_player_team,
    read_enemy_team,
    read_player_team_validated,
    read_enemy_team_validated,
    to_showdown,
    read_player_party_idx,
    read_opp_party_idx,
    read_battle_communication,
    read_battle_outcome,
    read_battle_mon_status2,
    read_last_used_item,
    read_battler_fainted,
    TRAINER_BATTLE_ITEM_IDS,
    TRAINER_BATTLE_ITEM_PS_IDS,
    STATUS2_CONFUSION,
    STATUS2_CURSED,
)
from NodeIPC import NodeIPC
from battle_sim import PokemonMCTS, parse_ipc_response, _rand_battle, select_move_with_ai_flags
from battle_record import BattleRecord
from battle_text_validator import parse_battle_texts, validate, MemoryState
from gen3_charset import read_battle_text

_HERE = os.path.dirname(os.path.abspath(__file__))

_WHAT_WILL_RE = re.compile(r'^What will .+? do\?', re.IGNORECASE)

# ─── Button bitmasks (must match game_loop.py) ────────────────────────────────
BTN_A     = 1
BTN_B     = 2
BTN_LEFT  = 32
BTN_RIGHT = 16
BTN_UP    = 64
BTN_DOWN  = 128

# Hold/release frame counts for each injected button press
_HOLD_FRAMES        = 12
_RELEASE_FRAMES     = 8
_PARTY_SCREEN_WAIT  = 120  # frames to wait for party screen fade-in before navigating
_FIGHT_MENU_WAIT    = 10  # frames to wait after pressing A to open FIGHT before navigating


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
    item_id = item.lower().replace(' ', '').replace('-', '').replace("'", '').replace('.', '')

    # name|species|item|ability|moves|nature|evs|gender|ivs|shiny|level|happiness
    return f"{name}||{item_id}|{ability}|{moves_str}|{nature}|{evs_str}||{ivs_str}||{level}|"


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

    Sequence: A (enter FIGHT), UP+LEFT to reset cursor to top-left, navigate to
    slot, A (confirm).  Gen 3 move grids do not wrap at edges, so UP from the top
    row and LEFT from the left column are no-ops — this reset is always safe.
    Each button: hold _HOLD_FRAMES, then release for _RELEASE_FRAMES.
    """
    def press(bitmask):
        return [(bitmask, _HOLD_FRAMES), (0, _RELEASE_FRAMES)]

    sequence = press(BTN_A)                    # open FIGHT menu
    sequence += [(0, _FIGHT_MENU_WAIT)]        # wait for menu to register before navigating
    sequence += press(BTN_UP)                  # reset to top row   (no-op if already there)
    sequence += press(BTN_LEFT) # reset to left col  (no-op if already there)

    if move_index == 1:
        sequence += press(BTN_RIGHT)                  # top-right
    elif move_index == 2:
        sequence += press(BTN_DOWN)                   # bottom-left
    elif move_index == 3:
        sequence += press(BTN_RIGHT) + press(BTN_DOWN)  # bottom-right

    sequence += press(BTN_A)  # confirm move
    return sequence


def _switch_button_sequence(gba_idx: int, from_action_screen: bool) -> list:
    """Button sequence to switch to the Pokémon at GBA party index gba_idx.

    Args:
        gba_idx: 0-based GBA party slot of the Pokémon to switch in.
        from_action_screen: True if currently on the FIGHT/POKEMON/BAG/RUN action
            screen; False if the game has already jumped to the party screen (e.g.
            after a faint or Roar).
    """
    def press(b):
        return [(b, _HOLD_FRAMES), (0, _RELEASE_FRAMES)]

    sequence = []
    if from_action_screen:
        sequence += press(BTN_DOWN)            # FIGHT → POKEMON
        sequence += press(BTN_A)               # enter party screen (cursor at slot 0)

    #Always wait to be safe
    sequence += [(0, _PARTY_SCREEN_WAIT)]  # wait for fade-in before navigating

    for _ in range(gba_idx):
        sequence += press(BTN_DOWN)  # navigate to desired slot

    sequence += press(BTN_A)  # open action menu (SWITCH highlighted)
    sequence += press(BTN_A)  # confirm switch
    return sequence


def _print_team(label: str, team: list, species_db: dict, moves_db: dict) -> None:
    """Print a compact team summary to stdout."""
    print(f"[battle_mode] === {label} ===")
    for i, pkmn in enumerate(team, 1):
        name = internal_species_name(pkmn.get('species', 0), species_db)
        lv   = pkmn.get('level', '?')
        chp  = pkmn.get('current_hp', 0)
        mhp  = pkmn.get('max_hp', 0)
        move_names = [
            moves_db.get(str(mid), f'Move#{mid}')
            for mid in pkmn.get('moves', []) if mid
        ]
        moves_str = ' / '.join(move_names) if move_names else '—'
        print(f"  [{i}] Lv.{lv:3}  {name:<12}  HP {chp:3}/{mhp:3}   {moves_str}")


_prev_opp_party_idx = None  # party index seen on the previous F5 call; None on first call

# ─── Battle-loop helpers ──────────────────────────────────────────────────────

def _find_opp_ps_action(opp_move_id: int, ps_state: dict, moves_db: dict) -> str | None:
    """Convert a GBA move integer to a PS 'move N' action for the active opponent Pokémon.

    Looks up the move name, converts it to PS ID format, then finds the matching slot
    in the active opponent's moveSlots.  Returns None if the move isn't found.
    """
    if not opp_move_id:
        return None
    move_name = moves_db.get(str(opp_move_id), '')
    if not move_name:
        return None
    ps_id = move_name.lower().replace(' ', '').replace('-', '').replace("'", '')
    sides = ps_state['battle'].get('sides', [])
    if len(sides) < 2:
        return None
    opp_pokemon = sides[1].get('pokemon', [])
    active = next((p for p in opp_pokemon if p.get('isActive')), None)
    if active is None and opp_pokemon:
        active = opp_pokemon[0]
    if active is None:
        return None
    for i, slot in enumerate(active.get('moveSlots', [])):
        if slot.get('id') == ps_id:
            return f'move {i + 1}'
    return None


def _apply_gba_switch(gba_order: list, new_gba_idx: int) -> None:
    """Update gba_order in-place to reflect a switch-in of GBA party slot new_gba_idx.

    Swaps new_gba_idx's current PS slot with PS slot 0 (active position), mirroring
    how PS reorders the team list when a Pokémon switches in.
    """
    try:
        ps_slot = gba_order.index(new_gba_idx)
    except ValueError:
        return  # unknown slot — leave order unchanged
    gba_order[0], gba_order[ps_slot] = gba_order[ps_slot], gba_order[0]


def _opp_ps_switch_from_slot(opp_gba_slot: int, p2_gba_order: list) -> str | None:
    """Return PS 'switch N' action for the opponent's active Pokémon after a forced switch.

    opp_gba_slot: value from read_opp_party_idx(core) — current active GBA party index.
    Finds its PS team slot via p2_gba_order and returns the 1-based switch action string.
    Returns None if the slot cannot be found (caller should fall back to AI selection).
    """
    try:
        ps_slot = p2_gba_order.index(opp_gba_slot)
    except ValueError:
        return None
    return f'switch {ps_slot + 1}'


def _patch_ps_state(ps_state: dict, gba_team: list, side_idx: int,
                    gba_order: list, active_status2: int | None = None) -> None:
    """Overwrite HP, PP, status, and active volatile statuses with GBA values.

    Uses gba_order[ps_slot] to directly index into gba_team without species lookup.
    decrypt_pokemon() already converts the GBA status byte to PS abbreviations
    ('par', 'brn', 'frz', 'psn', 'tox', 'slp', ''), so no further translation is needed.

    active_status2: gBattleMons[battler].status2 for the active (ps_idx==0) Pokémon.
    When provided, confusion and curse volatiles are synced from GBA ground truth.
    """
    ps_pokemon = ps_state['battle']['sides'][side_idx].get('pokemon', [])
    for ps_idx, ps_pkmn in enumerate(ps_pokemon):
        if ps_idx >= len(gba_order):
            continue
        gba_idx  = gba_order[ps_idx]
        if gba_idx >= len(gba_team):
            continue
        gba_pkmn = gba_team[gba_idx]
        gba_hp = gba_pkmn.get('current_hp', 0) or 0
        if ps_pkmn.get('hp', 0) > 0 and gba_hp > 0:
            ps_pkmn['hp'] = gba_hp
        ps_pkmn['status'] = gba_pkmn.get('status', '') or ''
        gba_pp = gba_pkmn.get('pp', [])
        for move_idx, slot in enumerate(ps_pkmn.get('moveSlots', [])):
            if move_idx < len(gba_pp):
                slot['pp'] = gba_pp[move_idx]
        if ps_idx == 0 and active_status2 is not None:
            volatiles = dict(ps_pkmn.get('volatiles') or {})
            if active_status2 & STATUS2_CONFUSION:
                volatiles.setdefault('confusion', {})
            else:
                volatiles.pop('confusion', None)
            if active_status2 & STATUS2_CURSED:
                volatiles.setdefault('curse', {})
            else:
                volatiles.pop('curse', None)
            ps_pkmn['volatiles'] = volatiles


def _faint_diff(ps_state: dict, gba_player: list, gba_enemy: list,
                p1_gba_order: list, p2_gba_order: list) -> list:
    """Return a list of per-Pokémon faint mismatches between PS and GBA. Empty = no mismatch."""
    diffs = []
    sides = ps_state['battle'].get('sides', [])
    if len(sides) < 2:
        return diffs
    side_names = ['player', 'opponent']
    for side_idx, (gba_team, gba_order) in enumerate([(gba_player, p1_gba_order),
                                                       (gba_enemy,  p2_gba_order)]):
        ps_pokemon = sides[side_idx].get('pokemon', [])
        for ps_idx, ps_pkmn in enumerate(ps_pokemon):
            if ps_idx >= len(gba_order):
                continue
            gba_idx = gba_order[ps_idx]
            if gba_idx >= len(gba_team):
                continue
            ps_hp       = ps_pkmn.get('hp', 1)
            gba_hp      = gba_team[gba_idx].get('current_hp') or 0
            ps_fainted  = ps_hp <= 0
            gba_fainted = gba_hp == 0
            if ps_fainted != gba_fainted:
                diffs.append({
                    'side':        side_names[side_idx],
                    'ps_idx':      ps_idx,
                    'gba_idx':     gba_idx,
                    'ps_hp':       ps_hp,
                    'ps_fainted':  ps_fainted,
                    'gba_hp':      gba_hp,
                    'gba_fainted': gba_fainted,
                })
    return diffs


def _simulate_and_reconcile(ipc, ps_state: dict, p1_action: str, p2_action: str,
                             gba_player: list, gba_enemy: list,
                             p1_gba_order: list, p2_gba_order: list,
                             opp_active_gba_slot: int = 0,
                             core=None, emu_lock=None,
                             player_status2: int | None = None,
                             enemy_status2: int | None = None,
                             recorder=None, turn: int = 0) -> dict:
    """Simulate the completed turn in PS, retry on faint mismatch, then patch HP/PP/status.

    Each retry uses _rand_battle() to overwrite the PRNG seed, exploring different RNG
    outcomes (crits, accuracy rolls, secondary effects) until the faint pattern matches
    GBA ground truth.

    If the opponent's Pokémon fainted this turn, PS enters an intermediate state waiting
    for the opponent's forced switch-in.  The while loop advances through this using
    the actual GBA switch-in (opp_active_gba_slot) before the faint check is applied.

    p1_gba_order and p2_gba_order are mutated in-place when switches occur so the caller's
    mapping stays in sync with PS's team ordering for subsequent turns.

    HP, PP, and status are always overwritten with GBA ground truth after a match is found
    (or after all retries are exhausted).
    """
    # Update order lists for any switches that occurred this turn.
    if p2_action and p2_action.startswith('switch '):
        ps_slot = int(p2_action.split()[1]) - 1
        p2_gba_order[0], p2_gba_order[ps_slot] = p2_gba_order[ps_slot], p2_gba_order[0]
    if p1_action and p1_action.startswith('switch '):
        ps_slot = int(p1_action.split()[1]) - 1
        p1_gba_order[0], p1_gba_order[ps_slot] = p1_gba_order[ps_slot], p1_gba_order[0]

    pre_battle = ps_state['battle']
    new_state  = None
    attempt    = 0
    _mismatch_logged = False
    #Should try forever.  If the state is a mismatch that is catastrophic
    while True:
        attempt += 1
        response  = ipc.send({'battle': _rand_battle(pre_battle), 'p1': p1_action, 'p2': p2_action})
        new_state = parse_ipc_response(response)
        # Check faint match before advancing any forced switch, so a wrong simulation
        # (e.g. opponent survived when they should have fainted) never enters the switch loop.
        _diffs = _faint_diff(new_state, gba_player, gba_enemy, p1_gba_order, p2_gba_order)
        if _diffs:
            if not _mismatch_logged:
                print(f'[battle_loop] Faint mismatch detected, retrying...')
                _mismatch_logged = True
            if recorder:
                recorder.on_faint_mismatch(
                    turn=turn, attempt=attempt,
                    p1_action=p1_action, p2_action=p2_action,
                    pre_state=ps_state, ps_after=new_state,
                    gba_player=gba_player, gba_enemy=gba_enemy,
                    p1_gba_order=list(p1_gba_order), p2_gba_order=list(p2_gba_order),
                    diffs=_diffs,
                )
                recorder.on_state_update('mismatch_attempt', turn, new_state)
            if attempt % _REFRESH_AFTER == 0 and core is not None and emu_lock is not None:
                with emu_lock:
                    new_opp_idx = read_opp_party_idx(core)
                if new_opp_idx != opp_active_gba_slot:
                    print(f'[battle_loop] opp_party_idx corrected: {opp_active_gba_slot} → {new_opp_idx}')
                    _apply_gba_switch(p2_gba_order, new_opp_idx)
                    opp_active_gba_slot = new_opp_idx
            continue
        # Faint pattern confirmed — advance through forced opponent switch-in if present.
        if _mismatch_logged:
            print(f'[battle_loop] Faint mismatch resolved after {attempt} attempt(s).')
        while (not new_state['is_over']
               and not new_state['p1_moves']
               and not new_state['p1_switches']
               and new_state['p2_switches']):
            if core is not None and emu_lock is not None:
                with emu_lock:
                    new_opp_idx = read_opp_party_idx(core)
                if new_opp_idx != opp_active_gba_slot:
                    print(f'[battle_loop] opp_party_idx corrected in switch loop: {opp_active_gba_slot} → {new_opp_idx}')
                    opp_active_gba_slot = new_opp_idx
            p2_sw = _opp_ps_switch_from_slot(opp_active_gba_slot, p2_gba_order)
            if p2_sw is None:
                print('[battle_loop] Warning: cannot map opp slot to PS switch — using ai_flags')
                _tmp = PokemonMCTS(ipc, node_script_path=None)
                p2_sw = _tmp._pick_p2_move(new_state) or new_state['p2_switches'][0]
            _apply_gba_switch(p2_gba_order, opp_active_gba_slot)
            resp      = ipc.send({'battle': _rand_battle(new_state['battle']), 'p2': p2_sw})
            new_state = parse_ipc_response(resp)
        break
    _patch_ps_state(new_state, gba_player, 0, p1_gba_order, active_status2=player_status2)
    _patch_ps_state(new_state, gba_enemy,  1, p2_gba_order, active_status2=enemy_status2)
    return new_state


# ─── Autonomous battle loop ───────────────────────────────────────────────────

_A_PRESS_INTERVAL  = 15  # frames between auto A-presses when advancing between-turn text
_AT_MENU_STABLE    = 10  # consecutive frames battle_comm==2 required before treating as real menu
_FAINT_SCREEN_STABLE = 10  # consecutive frames battler_fainted & 1 required before handling faint
_REFRESH_AFTER     = 5   # reconcile attempts before re-checking GBA active index

_MCTS_PYTHON = os.path.join(_HERE, '.venv_t', 'bin', 'python3.14t')
_MCTS_SERVER = os.path.join(_HERE, 'mcts_server.py')


class MCTSProcess:
    """Wraps a long-lived .venv_t/bin/python3.14t mcts_server.py subprocess.

    Uses the same 4-byte big-endian length + JSON framing as NodeIPC.
    The subprocess runs with PYTHON_GIL=0 for true free-threaded parallelism.
    """

    def __init__(self, node_script_path: str, num_workers: int = 5):
        env = os.environ.copy()
        env['PYTHON_GIL'] = '0'
        self._proc = subprocess.Popen(
            [_MCTS_PYTHON, _MCTS_SERVER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            cwd=_HERE, env=env, bufsize=0,
        )
        self._node_script = node_script_path
        self._num_workers = num_workers

    def search(self, ps_state: dict, iterations: int):
        """Run MCTS in the subprocess. Returns (best_action, stats_list)."""
        req  = {'node_script': self._node_script, 'state': ps_state,
                'iterations': iterations, 'num_workers': self._num_workers}
        data = json.dumps(req).encode()
        self._proc.stdin.write(struct.pack('>I', len(data)) + data)
        self._proc.stdin.flush()
        length = struct.unpack('>I', self._read_exact(4))[0]
        resp   = json.loads(self._read_exact(length))
        return resp['action'], resp['stats']

    def _read_exact(self, n: int) -> bytes:
        buf = b''
        while len(buf) < n:
            chunk = self._proc.stdout.read(n - len(buf))
            if not chunk:
                raise EOFError('[MCTSProcess] subprocess stdout closed unexpectedly')
            buf += chunk
        return buf

    def close(self):
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        self._proc.terminate()
        self._proc.wait()


def _resolve_move_name(action: str, state: dict, side_idx: int) -> str | None:
    """Return the human-readable move name for 'move N' from ps_state moveSlots."""
    if not action or not action.startswith('move '):
        return None
    try:
        move_idx = int(action.split()[1]) - 1
        sides = state['battle'].get('sides', [])
        if side_idx < len(sides) and sides[side_idx]['pokemon']:
            slots = sides[side_idx]['pokemon'][0].get('moveSlots', [])
            if move_idx < len(slots):
                return slots[move_idx].get('move')
    except (IndexError, KeyError, ValueError):
        pass
    return None


def run_battle_loop(core, emu_lock, node_script_path, injected_keys,
                    opp_last_move_id=None,
                    badge_boosts: dict | None = None,
                    mcts_iterations: int = 1000, num_workers: int = 15,
                    test_actions: list | None = None,
                    recorder: 'BattleRecord | None' = None):
    """Run the full battle autonomously from the first menu detection until battle end.

    Polls gBattleCommunication[0] at ~30 Hz.  When not at the battle menu, injects
    periodic A presses to advance turn text.  When the menu is first detected each turn,
    runs MCTS and injects the best action.  After each subsequent menu appearance,
    simulates the previous turn in PS using the actual opponent move and reconciles
    HP/PP/status with GBA ground truth before running MCTS for the new turn.
    """
    import time
    from datetime import datetime
    from battle_sim import PokemonMCTS

    species_db   = _load_db('gen3_species.json')
    moves_db     = _load_db('gen3_move_names.json')
    items_db     = _load_db('gen3_items.json')
    abilities_db = _load_db('gen3_abilities.json')

    ipc       = NodeIPC(node_script_path)
    mcts_proc = MCTSProcess(node_script_path, num_workers=num_workers)

    _log_dir = os.path.join(_HERE, 'test_logs')
    os.makedirs(_log_dir, exist_ok=True)
    _val_log_path = os.path.join(
        _log_dir, f'validation_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
    _val_log = open(_val_log_path, 'w', buffering=1)   # line-buffered

    try:
        ps_state        = None
        last_p1_action  = None
        p1_gba_order    = []   # ps_slot → gba_party_idx for player
        p2_gba_order    = []   # ps_slot → gba_party_idx for opponent
        prev_at_menu          = False
        at_menu_frames        = 0     # consecutive frames where battle_comm == 2
        a_press_counter       = 0
        opp_item_for_reconcile = 0   # item opponent planned at previous menu open
        #Opponents can only use 1 type of item.  Once one is selected, can't allow another type
        usedItem = None
        faint_screen_frames   = 0
        prev_at_faint_screen  = False
        #battler fainted flag is 0 until opponent's first action.
        #Can't use until it has been set to 1 initially
        faintSetPreviously = False
        skip_next_reconcile   = False
        test_action_idx       = 0
        _turn_number          = 0

        # ── Battle text accumulation (for validation) ─────────────────────────
        _txt_candidate        = ''
        _txt_candidate_frames = 0
        _txt_last_recorded    = ''
        _pending_texts: list[str] = []
        _TEXT_STABLE_FRAMES   = 4

        def _press_a():
            injected_keys.extend([(BTN_B, _HOLD_FRAMES), (0, _RELEASE_FRAMES)])

        while True:
            with emu_lock:
                battle_comm     = read_battle_communication(core)
                battle_outcome  = read_battle_outcome(core)
                battler_fainted = read_battler_fainted(core)
                _raw_text       = read_battle_text(core)

            # ── Debounced battle-text accumulation ────────────────────────────
            if _raw_text != _txt_candidate:
                _txt_candidate        = _raw_text
                _txt_candidate_frames = 1
            else:
                _txt_candidate_frames += 1
            if (_txt_candidate_frames == _TEXT_STABLE_FRAMES
                    and _txt_candidate
                    and _txt_candidate != _txt_last_recorded):
                _pending_texts.append(_txt_candidate)
                _txt_last_recorded = _txt_candidate

            if battle_outcome != 0:
                outcome_names = {1: 'won', 2: 'lost', 3: 'ran', 4: 'caught', 5: 'draw'}
                label = outcome_names.get(battle_outcome, f'outcome={battle_outcome}')
                print(f'[battle_loop] Battle ended: {label}')
                if recorder:
                    recorder.on_end(label)
                break

            if battle_comm == 2:
                at_menu_frames += 1
                a_press_counter = 0
            else:
                at_menu_frames = 0
            at_menu = (at_menu_frames >= _AT_MENU_STABLE)

            if battler_fainted != 0:
                faintSetPreviously = True

            if battler_fainted == 0 and battle_comm != 2:
                faint_screen_frames += 1
            else:
                faint_screen_frames = 0
            at_faint_screen = (faint_screen_frames >= _FAINT_SCREEN_STABLE)

            # ── Player's Pokémon fainted — pick forced switch-in ──────────
            if at_faint_screen and not prev_at_faint_screen and ps_state is not None and faintSetPreviously:
                a_press_counter = 0

                _tr = 0
                while True:
                    with emu_lock:
                        player_team,   player_ok = read_player_team_validated(core)
                        enemy_team,    enemy_ok  = read_enemy_team_validated(core)
                        opp_party_idx  = read_opp_party_idx(core)
                        player_status2 = read_battle_mon_status2(core, 0)
                        enemy_status2  = read_battle_mon_status2(core, 1)
                    if player_ok and enemy_ok:
                        break
                    _tr += 1
                    print(f'[battle_loop] Faint screen team retry {_tr}...')
                    time.sleep(1 / 60)

                # Mini-reconcile: advance PS through the turn that caused the faint
                if last_p1_action is not None:
                    opp_move_id = opp_last_move_id[0] if opp_last_move_id else 0
                    opp_last_move_id[0] = 0
                    opp_item_id   = opp_item_for_reconcile
                    opp_item_name = TRAINER_BATTLE_ITEM_PS_IDS.get(opp_item_id, '') if opp_item_id else ''
                    if opp_item_name:
                        p2_action = f'item {opp_item_name}'
                    elif opp_move_id:
                        p2_action = _find_opp_ps_action(opp_move_id, ps_state, moves_db)
                        if p2_action is None:
                            _tmp = PokemonMCTS(ipc, node_script_path=node_script_path)
                            p2_action = _tmp._pick_p2_move(ps_state) or 'move 1'
                    else:
                        _tmp = PokemonMCTS(ipc, node_script_path=node_script_path)
                        p2_action = _tmp._pick_p2_move(ps_state) or 'move 1'

                    print(f'[battle_loop] Faint-screen mini-reconcile: p1={last_p1_action}  p2={p2_action}')
                    ps_state = _simulate_and_reconcile(
                        ipc, ps_state, last_p1_action, p2_action,
                        player_team, enemy_team, p1_gba_order, p2_gba_order,
                        opp_active_gba_slot=opp_party_idx,
                        core=core, emu_lock=emu_lock,
                        player_status2=player_status2,
                        enemy_status2=enemy_status2,
                        recorder=recorder, turn=_turn_number,
                    )
                    if recorder:
                        recorder.on_state_update('faint_mini_reconcile', _turn_number, ps_state)

                # Run MCTS on the intermediate state (p1_switches should be populated)
                available_switches = ps_state.get('p1_switches') or []
                if not available_switches:
                    print('[battle_loop] Warning: no p1_switches after faint mini-reconcile')
                    prev_at_faint_screen = at_faint_screen
                    time.sleep(1 / 30)
                    continue

                if test_actions:
                    best_switch = test_actions[test_action_idx % len(test_actions)]
                    test_action_idx += 1
                    print(f'[battle_loop] Faint-screen test action: {best_switch}')
                else:
                    best_switch, _ = mcts_proc.search(ps_state, mcts_iterations)
                if best_switch is None or not best_switch.startswith('switch '):
                    best_switch = available_switches[0]
                    print(f'[battle_loop] Faint-screen: invalid action "{best_switch}" — defaulting to {best_switch}')
                else:
                    if not test_actions:
                        print(f'[battle_loop] Faint-screen MCTS chose: {best_switch}')

                # Apply switch to PS state and update GBA order mapping
                ps_slot  = int(best_switch.split()[1])
                gba_idx = ps_slot - 1
                resp     = ipc.send({'battle': ps_state['battle'], 'p1': best_switch})
                ps_state = parse_ipc_response(resp)
                if recorder:
                    recorder.on_state_update('faint_switch', _turn_number, ps_state)
                p1_gba_order[0], p1_gba_order[ps_slot - 1] = (
                    p1_gba_order[ps_slot - 1], p1_gba_order[0])

                print(f'[battle_loop] Faint switch: {best_switch} (GBA slot {gba_idx})')
                injected_keys.extend(_switch_button_sequence(gba_idx, from_action_screen=False))
                if recorder:
                    recorder.on_faint(_turn_number, best_switch)
                last_p1_action      = None
                skip_next_reconcile = True

            # ── Arrived at battle action menu ─────────────────────────────
            if at_menu and not prev_at_menu and _WHAT_WILL_RE.match(_raw_text):
                _turn_number += 1
                prev_at_menu = True
                if recorder:
                    recorder.current_turn = _turn_number
                a_press_counter = 0  # stop auto-A while processing menu

                _tr = 0
                while True:
                    with emu_lock:
                        player_team,   player_ok = read_player_team_validated(core)
                        enemy_team,    enemy_ok  = read_enemy_team_validated(core)
                        p1_party_idx   = read_player_party_idx(core)
                        opp_party_idx  = read_opp_party_idx(core)
                        player_status2 = read_battle_mon_status2(core, 0)
                        enemy_status2  = read_battle_mon_status2(core, 1)
                        _raw_item      = read_last_used_item(core)
                        print("item", _raw_item)
                        opp_item_this_turn = _raw_item if _raw_item in TRAINER_BATTLE_ITEM_IDS else 0
                        #Prevents weird data that doesn't actually mean item usage
                        if usedItem is not None and opp_item_this_turn != usedItem:
                            opp_item_this_turn = 0
                        if opp_item_this_turn != 0:
                            usedItem = opp_item_this_turn
                    if player_ok and enemy_ok:
                        break
                    _tr += 1
                    print(f'[battle_loop] Team checksum mismatch on attempt {_tr}, retrying...')
                    time.sleep(1 / 60)

                if not player_team or not enemy_team:
                    print('[battle_loop] Warning: empty team after retries — will retry next frame')
                    # Do NOT set prev_at_menu here so the next frame re-triggers this block
                    time.sleep(1 / 30)
                    continue

                p1_active  = min(p1_party_idx, len(player_team) - 1)
                p2_active  = min(opp_party_idx, len(enemy_team) - 1)
                p1_ordered = ([player_team[p1_active]]
                              + [p for i, p in enumerate(player_team) if i != p1_active])
                p2_ordered = ([enemy_team[p2_active]]
                              + [p for i, p in enumerate(enemy_team)  if i != p2_active])
                is_forced_switch = bool(player_team) and player_team[p1_active].get('current_hp', 1) == 0

                # ── Text validation against memory state ──────────────────────
                _parsed = parse_battle_texts(_pending_texts)
                _pending_texts.clear()
                if ps_state is not None:   # skip on first turn (no previous turn to validate)
                    _raw_opp_move_id = opp_last_move_id[0] if opp_last_move_id else 0
                    _opp_move_name = moves_db.get(str(_raw_opp_move_id), '') if _raw_opp_move_id else ''
                    _p1_move_name = last_p1_action if (last_p1_action and
                                                       last_p1_action.startswith('move ')) else None
                    if _p1_move_name:
                        # Resolve slot index to actual move name via PS state
                        try:
                            _slot_idx = int(last_p1_action.split()[1]) - 1
                            _sides = ps_state['battle'].get('sides', [])
                            if _sides:
                                _p1_slots = _sides[0].get('pokemon', [{}])[0].get('moveSlots', [])
                                if _slot_idx < len(_p1_slots):
                                    _p1_move_name = _p1_slots[_slot_idx].get('move', _p1_move_name)
                        except (IndexError, ValueError, KeyError):
                            pass
                    _mem = MemoryState(
                        player_active_species=internal_species_name(
                            player_team[p1_active].get('species', 0), species_db),
                        opp_active_species=internal_species_name(
                            enemy_team[p2_active].get('species', 0), species_db),
                        opp_last_move_name=_opp_move_name or None,
                        last_p1_move_name=_p1_move_name,
                        player_fainted=player_team[p1_active].get('current_hp', 1) == 0,
                        opp_fainted=enemy_team[p2_active].get('current_hp', 1) == 0,
                        item_id=opp_item_this_turn,
                    )
                    _mismatches = validate(_parsed, _mem)
                    for _mm in _mismatches:
                        _line = f'[turn {_turn_number}] MISMATCH: {_mm}\n'
                        print(f'[battle_text_validator]{_line}', end='')
                        _val_log.write(_line)

                if ps_state is None:
                    # ── First turn: initialize PS battle ──────────────────
                    p1_gba_order = [p1_active] + [i for i in range(len(player_team)) if i != p1_active]
                    p2_gba_order = [p2_active] + [i for i in range(len(enemy_team))  if i != p2_active]

                    p1_pipes = _gba_team_to_pipe_strings(
                        p1_ordered, species_db, moves_db, items_db, abilities_db)
                    p2_pipes = _gba_team_to_pipe_strings(
                        p2_ordered, species_db, moves_db, items_db, abilities_db)
                    p1_init  = _build_init_state(p1_ordered)
                    p2_init  = _build_init_state(p2_ordered)

                    response = ipc.send({
                        'new':         True,
                        'team1':       ']'.join(p1_pipes),
                        'team2':       ']'.join(p2_pipes),
                        'p1InitState': p1_init,
                        'p2InitState': p2_init,
                        **(badge_boosts or {}),
                    })
                    ps_state = parse_ipc_response(response)
                    print('[battle_loop] PS battle initialized.')
                    if recorder:
                        recorder.on_state_update('init', _turn_number, ps_state)

                else:
                    # ── Subsequent turn: reconcile previous turn ──────────
                    if skip_next_reconcile:
                        skip_next_reconcile = False
                        print('[battle_loop] Skipping reconcile (faint switch already applied)')
                        if recorder:
                            recorder.on_reconcile(
                                _turn_number - 1, last_p1_action or '', '',
                                None, None, skipped=True)
                    else:
                        opp_move_id = opp_last_move_id[0] if opp_last_move_id else 0
                        opp_last_move_id[0] = 0  # reset; emu_loop will capture next turn's move
                        opp_item_id = opp_item_for_reconcile
                        opp_item_name = TRAINER_BATTLE_ITEM_PS_IDS.get(opp_item_id, '') if opp_item_id else ''
                        if opp_item_name:
                            opp_last_name = f'item:{opp_item_name}'
                        elif opp_move_id:
                            opp_last_name = moves_db.get(str(opp_move_id), f'id#{opp_move_id:#06x}')
                        else:
                            opp_last_name = '(none)'
                        print(f'[battle_loop] Opponent last move: {opp_last_name}')

                        if opp_item_name:
                            p2_action = f'item {opp_item_name}'
                        elif opp_move_id:
                            p2_action = _find_opp_ps_action(opp_move_id, ps_state, moves_db)
                            if p2_action is None:
                                _tmp = PokemonMCTS(ipc, node_script_path=node_script_path)
                                p2_action = _tmp._pick_p2_move(ps_state) or 'move 1'
                                print(f'[battle_loop] Opp move not in PS slots; ai_flags fallback: {p2_action}')
                        else:
                            _tmp = PokemonMCTS(ipc, node_script_path=node_script_path)
                            p2_action = _tmp._pick_p2_move(ps_state) or 'move 1'
                            print(f'[battle_loop] No opp action detected; ai_flags fallback: {p2_action}')

                        if recorder:
                            _p1_name = _resolve_move_name(last_p1_action or '', ps_state, 0)
                            _p2_name = _resolve_move_name(p2_action, ps_state, 1)
                            recorder.on_reconcile(
                                _turn_number - 1, last_p1_action or '', p2_action,
                                _p1_name, _p2_name)

                        print(f'[battle_loop] Reconciling: p1={last_p1_action}  p2={p2_action}')
                        ps_state = _simulate_and_reconcile(
                            ipc, ps_state, last_p1_action, p2_action,
                            player_team, enemy_team, p1_gba_order, p2_gba_order,
                            opp_active_gba_slot=opp_party_idx,
                            core=core, emu_lock=emu_lock,
                            player_status2=player_status2,
                            enemy_status2=enemy_status2,
                            recorder=recorder, turn=_turn_number - 1,
                        )
                        if recorder:
                            recorder.on_state_update('reconcile', _turn_number - 1, ps_state)
                        if ps_state['is_over']:
                            print(f"[battle_loop] Battle over — winner: {ps_state.get('winner', '?')}")
                            break

                opp_item_for_reconcile = opp_item_this_turn  # save for next turn's reconcile

                _print_team('Player team',   p1_ordered, species_db, moves_db)
                _print_team('Opponent team', p2_ordered, species_db, moves_db)

                # ── Action selection ───────────────────────────────────────
                _known_p2 = (f'item {TRAINER_BATTLE_ITEM_PS_IDS[opp_item_this_turn]}'
                             if opp_item_this_turn else None)
                if _known_p2:
                    print(f'[battle_loop] Opponent will use {_known_p2} this turn — injecting into MCTS')
                if test_actions:
                    best_action = test_actions[test_action_idx % len(test_actions)]
                    test_action_idx += 1
                    mcts_stats = []
                    print(f'[battle_loop] Test action [{test_action_idx - 1}]: {best_action}')
                else:
                    _mcts_state = {**ps_state, '_p2_forced': _known_p2} if _known_p2 else ps_state
                    best_action, mcts_stats = mcts_proc.search(_mcts_state, mcts_iterations)
                    if mcts_stats:
                        print('[battle_loop] MCTS action stats:')
                        for s in mcts_stats:
                            print(f'  {s["action"]:20s}  visits={s["visits"]:4d}  avg={s["avg"]:.3f}')

                # ── Bad-RNG worst-case penalty ─────────────────────────────
                bad_rng_penalties = {}
                all_p1_actions = (list(ps_state.get('p1_moves') or [])
                                  + list(ps_state.get('p1_switches') or []))
                if all_p1_actions:
                    p2_avail = ps_state.get('p2_moves') or ps_state.get('p2_switches') or []
                    p2_bad = (select_move_with_ai_flags(ps_state, p2_avail, player=2)
                              if p2_avail else None)
                    for p1_act in all_p1_actions:
                        payload = {'battle': ps_state['battle'], 'p1': p1_act,
                                   'forceBadRNG': True}
                        if p2_bad:
                            payload['p2'] = p2_bad
                        bad_state  = parse_ipc_response(ipc.send(payload))
                        sides      = bad_state['battle'].get('sides', [])
                        active_hp  = sides[0]['pokemon'][0].get('hp', 1) if sides else 1
                        if active_hp <= 0:
                            bad_rng_penalties[p1_act] = -3.0
                            print(f'[battle_loop] Bad-RNG penalty −3: {p1_act} → active fainted')
                if bad_rng_penalties and mcts_stats:
                    adj      = {s['action']: s['avg'] + bad_rng_penalties.get(s['action'], 0.0)
                                for s in mcts_stats}
                    new_best = max(adj, key=adj.get)
                    if new_best != best_action:
                        print(f'[battle_loop] Bad-RNG re-select: {best_action} → {new_best}')
                    best_action = new_best

                # ── AI-flag override ───────────────────────────────────────
                if mcts_stats and ps_state.get('p1_moves'):
                    stat_by_action = {s['action']: s['avg'] + bad_rng_penalties.get(s['action'], 0.0)
                                      for s in mcts_stats}
                    ai_action = select_move_with_ai_flags(ps_state, ps_state['p1_moves'], player=1)
                    print(f'[battle_loop] AI-flag suggestion: {ai_action}')
                    ai_avg = stat_by_action.get(ai_action)
                    if ai_avg is not None and ai_action != best_action:
                        min_avg  = min(stat_by_action.values())
                        top_norm = max(stat_by_action.values()) - min_avg
                        ai_norm  = ai_avg - min_avg
                        if ai_norm >= 0.9 * top_norm:
                            print(f'[battle_loop] AI-flag override: {best_action} → {ai_action} '
                                  f'(ai_norm={ai_norm:.3f}, top_norm={top_norm:.3f})')
                            best_action = ai_action

                if best_action is None:
                    print('[battle_loop] Warning: MCTS returned no action')
                    prev_at_menu = at_menu
                    time.sleep(1 / 30)
                    continue

                if recorder:
                    recorder.on_mcts_decision(_turn_number, best_action, mcts_stats)

                last_p1_action = best_action

                # ── Inject button sequence ────────────────────────────────
                if best_action.startswith('move '):
                    move_index = int(best_action.split()[1]) - 1
                    print(f'[battle_loop] Queuing: {best_action} (slot index {move_index})')
                    injected_keys.extend(_move_button_sequence(move_index))

                elif best_action.startswith('switch '):
                    ps_slot = int(best_action.split()[1])
                    #gba_idx only contains the original index, to tell each pokemon apart
                    #But to switch, you just need the current slot and that's it
                    #gba_idx = p1_gba_order[ps_slot - 1]
                    gba_idx = ps_slot - 1
                    print(f'[battle_loop] Queuing: {best_action} '
                          f'(GBA slot {gba_idx}, forced={is_forced_switch})')
                    injected_keys.extend(_switch_button_sequence(gba_idx, not is_forced_switch))
                    # Note: p1_gba_order is updated inside _simulate_and_reconcile on the
                    # next turn, so no update needed here.

                else:
                    print(f'[battle_loop] Unrecognized action "{best_action}" — skipping')

            # ── Not at menu: press A periodically to advance text ─────────
            elif not at_menu and at_menu_frames == 0 and not injected_keys:
                a_press_counter += 1
                if a_press_counter >= _A_PRESS_INTERVAL:
                    _press_a()
                    a_press_counter = 0

            if not at_menu:
                prev_at_menu     = False
            prev_at_faint_screen = at_faint_screen
            time.sleep(1 / 30)

    finally:
        mcts_proc.close()
        ipc.close()
        _val_log.close()
        print('[battle_loop] IPC closed.')


# ─── Single-turn entry point (legacy) ────────────────────────────────────────

def suggest_and_queue_move(core, emu_lock, node_script_path, injected_keys,
                           opp_last_move_id=None,
                           mcts_iterations=100, num_workers=5):
    """Read live GBA state, run MCTS, and queue button presses for the best move.

    Args:
        core: mgba core object.
        emu_lock: threading.Lock guarding GBA memory access.
        node_script_path: Path to Connection.js.
        injected_keys: collections.deque of (bitmask, frames_remaining) tuples.
        opp_last_move_id: mutable [int] container updated by emu_loop with the last
            non-zero gLastMoves[1] seen during turn resolution.
        mcts_iterations: MCTS search budget.
        num_workers: Number of parallel MCTS worker threads.
    """
    species_db   = _load_db('gen3_species.json')
    moves_db     = _load_db('gen3_move_names.json')
    items_db     = _load_db('gen3_items.json')
    abilities_db = _load_db('gen3_abilities.json')

    with emu_lock:
        player_team   = read_player_team(core)
        enemy_team    = read_enemy_team(core)
        opp_party_idx = read_opp_party_idx(core)

    global _prev_opp_party_idx

    # Report opponent's last move (captured during turn resolution by emu_loop)
    opp_last_id   = opp_last_move_id[0] if opp_last_move_id else 0
    opp_last_name = moves_db.get(str(opp_last_id), f"id#{opp_last_id:#06x}") if opp_last_id else "(none yet)"
    print(f"[battle_mode] Opponent's last move: {opp_last_name}")

    # Detect opponent switch since last F5 press
    if _prev_opp_party_idx is not None and opp_party_idx != _prev_opp_party_idx:
        switched_name = (
            internal_species_name(enemy_team[opp_party_idx]['species'], species_db)
            if opp_party_idx < len(enemy_team) else f"slot {opp_party_idx}"
        )
        print(f"[battle_mode] Opponent switched to party slot {opp_party_idx + 1}: {switched_name}")
    _prev_opp_party_idx = opp_party_idx

    if not player_team or not enemy_team:
        print("[battle_mode] Warning: empty team — not in battle?")
        return

    # Reorder so active Pokemon leads
    p1_active = _active_index(player_team)
    p2_active = _active_index(enemy_team)

    p1_ordered = [player_team[p1_active]] + [p for i, p in enumerate(player_team) if i != p1_active]
    p2_ordered = [enemy_team[p2_active]]  + [p for i, p in enumerate(enemy_team)  if i != p2_active]

    p1_gba_indices  = [p1_active] + [i for i in range(len(player_team)) if i != p1_active]
    is_forced_switch = bool(player_team) and player_team[0].get('current_hp', 1) == 0

    _print_team("Player team",   p1_ordered, species_db, moves_db)
    _print_team("Opponent team", p2_ordered, species_db, moves_db)

    p1_pipes = _gba_team_to_pipe_strings(p1_ordered, species_db, moves_db, items_db, abilities_db)
    p2_pipes = _gba_team_to_pipe_strings(p2_ordered, species_db, moves_db, items_db, abilities_db)
    p1_init  = _build_init_state(p1_ordered)
    p2_init  = _build_init_state(p2_ordered)

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

        if best_action.startswith('move '):
            move_index = int(best_action.split()[1]) - 1
            print(f"[battle_mode] Queuing: {best_action} (slot index {move_index})")
            injected_keys.extend(_move_button_sequence(move_index))

        elif best_action.startswith('switch '):
            ps_slot = int(best_action.split()[1])          # 1-based PS ordering
            gba_idx = p1_gba_indices[ps_slot - 1]          # 0-based GBA party slot
            print(f"[battle_mode] Queuing: {best_action} "
                  f"(GBA slot {gba_idx}, forced={is_forced_switch})")
            injected_keys.extend(_switch_button_sequence(gba_idx, not is_forced_switch))

        else:
            print(f"[battle_mode] Best action '{best_action}' is unrecognized — skipping injection")

    finally:
        ipc.close()
