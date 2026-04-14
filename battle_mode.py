"""
battle_mode.py

Live battle mode: reads team state from GBA memory, runs shallow search to
pick the best move, and injects the corresponding button sequence into the emulator.
"""

import json
import os
import re
import struct
import subprocess
import time
from dataclasses import dataclass, field
from typing import NamedTuple

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
from config import APPROVED_OPPONENT_ITEMS, OPP_ITEMS
from NodeIPC import NodeIPC
from battle_sim import parse_ipc_response, _rand_battle, select_move_with_ai_flags, _pick_opponent_action
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
    # to_showdown embeds gender as "(M)" or "(F)" at the end of the name; strip it out
    # and place it in the pipe gender field so @pkmn/sim receives it correctly.
    first = lines[0]
    if ' @ ' in first:
        name, item = first.split(' @ ', 1)
    else:
        name, item = first, ''

    gender = ''
    for suffix in (' (M)', ' (F)'):
        if name.endswith(suffix):
            gender = suffix[-2]   # 'M' or 'F'
            name = name[:-len(suffix)]
            break

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
    return f"{name}||{item_id}|{ability}|{moves_str}|{nature}|{evs_str}|{gender}|{ivs_str}||{level}|"


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
                             player_active_gba_slot: int | None = None,
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
            if attempt % 100 == 0:
                print(f'[battle_loop] Faint mismatch: {attempt} attempts so far...')
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
            print(f'[battle_loop] Reconcile OK after {attempt} attempt(s).')
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
                p2_sw = _pick_opponent_action(new_state) or new_state['p2_switches'][0]
            _apply_gba_switch(p2_gba_order, opp_active_gba_slot)
            resp      = ipc.send({'battle': _rand_battle(new_state['battle']), 'p2': p2_sw})
            new_state = parse_ipc_response(resp)
        # Check that active pokemon matches GBA ground truth.
        _active_mismatch = False
        if p2_gba_order and p2_gba_order[0] != opp_active_gba_slot:
            _active_mismatch = True
        if player_active_gba_slot is not None and p1_gba_order and p1_gba_order[0] != player_active_gba_slot:
            _active_mismatch = True
        if _active_mismatch:
            if not _mismatch_logged:
                print(f'[battle_loop] Active pokemon mismatch, retrying...')
                _mismatch_logged = True
            if attempt % 100 == 0:
                print(f'[battle_loop] Active mismatch: {attempt} attempts so far...')
            continue
        break
    _patch_ps_state(new_state, gba_player, 0, p1_gba_order, active_status2=player_status2)
    _patch_ps_state(new_state, gba_enemy,  1, p2_gba_order, active_status2=enemy_status2)
    return new_state


# ─── Autonomous battle loop ───────────────────────────────────────────────────

_A_PRESS_INTERVAL    = 15   # frames between auto A-presses when advancing between-turn text
_AT_MENU_STABLE      = 10   # consecutive frames battle_comm==2 required before treating as real menu
_FAINT_SCREEN_STABLE = 10   # consecutive frames battler_fainted & 1 required before handling faint
_REFRESH_AFTER       = 5    # reconcile attempts before re-checking GBA active index
_TEXT_STABLE_FRAMES  = 4    # consecutive stable frames before recording a battle text string

from search_process import ShallowSearchProcess


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


def _flags_for_trainer(name: str | None) -> list:
    """Return the AI flag list for a given trainer name.

    Winona (Gym Leader 6) uses flag 4 (AI_SCRIPT_RISKY).
    Sidney (Elite Four)   uses flag 3 (AI_SCRIPT_SETUP_FIRST_TURN).
    All trainers get flags 0, 1, 2 as a baseline.
    Unrecognised or None → baseline only.
    """
    if name is None:
        return [0, 1, 2]
    key = name.lower()
    if key == 'winona':
        print('[ai_flags] Winona detected — using flag 4 (AI_SCRIPT_RISKY)')
        return [0, 1, 2, 4]
    if key == 'sidney':
        print('[ai_flags] Sidney detected — using flag 3 (AI_SCRIPT_SETUP_FIRST_TURN)')
        return [0, 1, 2, 3]
    return [0, 1, 2]


# ─── Loop state bundles ───────────────────────────────────────────────────────

class BattleResources(NamedTuple):
    """Read-only resources created once at battle start and passed to all handlers."""
    core:             object
    emu_lock:         object
    node_script_path: str
    ipc:              object
    species_db:       dict
    moves_db:         dict
    items_db:         dict
    abilities_db:     dict
    opp_last_move_id: object        # mutable [int] or None; shared with emu_loop
    badge_boosts:     dict | None
    test_actions:     list | None
    recorder:         object
    val_log:          object
    decision_fn:      object        # callable(ipc, state)->str
    opp_ai_flags:     list          # AI flag IDs active for the opponent trainer
    opp_items:        int  = 0      # number of healing items the opponent has
    opp_item_ps_id:   str  = ''     # PS item ID string (e.g. 'fullrestore', 'hyperpotion')


@dataclass
class BattleLoopState:
    """All mutable per-frame state for the battle loop."""
    ps_state:               dict | None = None
    last_p1_action:         str  | None = None
    last_p2_action:         str  | None = None
    p1_gba_order:           list = field(default_factory=list)
    p2_gba_order:           list = field(default_factory=list)
    prev_at_menu:           bool = False
    at_menu_frames:         int  = 0
    a_press_counter:        int  = 0
    opp_item_for_reconcile: int  = 0
    usedItem:               object = None
    faint_screen_frames:    int  = 0
    prev_at_faint_screen:   bool = False
    faintSetPreviously:     bool = False
    skip_next_reconcile:    bool = False
    test_action_idx:        int  = 0
    opp_faint_switched:     bool = False  # True when "{Trainer} sent out" seen this turn
    turn_number:            int  = 0
    txt_candidate:          str  = ''
    txt_candidate_frames:   int  = 0
    txt_last_recorded:      str  = ''
    pending_texts:          list = field(default_factory=list)


# ─── Leaf helpers ─────────────────────────────────────────────────────────────

def _press_a(injected_keys) -> None:
    injected_keys.extend([(BTN_B, _HOLD_FRAMES), (0, _RELEASE_FRAMES)])


def _resolve_p1_move_name(last_p1_action: str | None, ps_state: dict) -> str | None:
    """Resolve a 'move N' action to the actual move name via PS state moveSlots."""
    if not last_p1_action or not last_p1_action.startswith('move '):
        return None
    try:
        slot_idx = int(last_p1_action.split()[1]) - 1
        sides = ps_state['battle'].get('sides', [])
        if sides:
            slots = sides[0].get('pokemon', [{}])[0].get('moveSlots', [])
            if slot_idx < len(slots):
                return slots[slot_idx].get('move', last_p1_action)
    except (IndexError, ValueError, KeyError):
        pass
    return last_p1_action


def _build_p2_action(res: BattleResources, state: BattleLoopState,
                     opp_item_id: int, opp_move_id: int, log: bool = True) -> str:
    """Resolve opponent action: item > move-slot lookup > ai_flags fallback."""
    opp_item_name = TRAINER_BATTLE_ITEM_PS_IDS.get(opp_item_id, '') if opp_item_id else ''
    if opp_item_name:
        return f'item {opp_item_name}'
    if opp_move_id:
        action = _find_opp_ps_action(opp_move_id, state.ps_state, res.moves_db)
        if action:
            return action
        action = _pick_opponent_action(state.ps_state, res.opp_ai_flags) or 'move 1'
        if log:
            print(f'[battle_loop] Opp move not in PS slots; ai_flags fallback: {action}')
        return action
    action = _pick_opponent_action(state.ps_state, res.opp_ai_flags) or 'move 1'
    if log:
        print(f'[battle_loop] No opp action detected; ai_flags fallback: {action}')
    return action


def _run_decision_fn(res: BattleResources, state: BattleLoopState,
                     known_p2: str | None, label: str = 'decision_fn') -> str | None:
    """Call decision_fn with timing and print. Shared by normal and faint-switch paths."""
    state.ps_state["known_p2"] = known_p2
    _t0 = time.monotonic()
    action = res.decision_fn(res.ipc, state.ps_state)
    print(f'[battle_loop] {label} chose {action!r} in {time.monotonic() - _t0:.2f}s')
    return action


def _select_best_action(res: BattleResources, state: BattleLoopState,
                        known_p2: str | None) -> tuple:
    """Run decision_fn or pull from test_actions. Returns (best_action, [])."""
    if res.test_actions:
        action = res.test_actions[state.test_action_idx % len(res.test_actions)]
        state.test_action_idx += 1
        print(f'[battle_loop] Test action [{state.test_action_idx - 1}]: {action}')
        return action, []

    action = _run_decision_fn(res, state, known_p2)
    return action, []


def _inject_action_keys(injected_keys, best_action: str, is_forced_switch: bool) -> None:
    """Inject GBA button sequence for a move or switch action."""
    if best_action.startswith('move '):
        move_index = int(best_action.split()[1]) - 1
        print(f'[battle_loop] Queuing: {best_action} (slot index {move_index})')
        injected_keys.extend(_move_button_sequence(move_index))
    elif best_action.startswith('switch '):
        gba_idx = int(best_action.split()[1]) - 1
        print(f'[battle_loop] Queuing: {best_action} (GBA slot {gba_idx}, forced={is_forced_switch})')
        injected_keys.extend(_switch_button_sequence(gba_idx, not is_forced_switch))
    else:
        print(f'[battle_loop] Unrecognized action "{best_action}" — skipping')


# ─── Level 3: branch sub-operations ──────────────────────────────────────────

def _read_team_state_faint(res: BattleResources) -> tuple:
    """Retry-loop: read teams + party indices + status2 for the faint-screen branch."""
    _tr = 0
    while True:
        with res.emu_lock:
            player_team,   player_ok = read_player_team_validated(res.core)
            enemy_team,    enemy_ok  = read_enemy_team_validated(res.core)
            p1_party_idx   = read_player_party_idx(res.core)
            opp_party_idx  = read_opp_party_idx(res.core)
            player_status2 = read_battle_mon_status2(res.core, 0)
            enemy_status2  = read_battle_mon_status2(res.core, 1)
        if player_ok and enemy_ok:
            return player_team, enemy_team, p1_party_idx, opp_party_idx, player_status2, enemy_status2
        _tr += 1
        print(f'[battle_loop] Faint screen team retry {_tr}...')
        time.sleep(1 / 60)


def _read_team_state_menu(res: BattleResources, state: BattleLoopState) -> tuple:
    """Retry-loop: read teams + party indices + status2 + item for the menu branch."""
    _tr = 0
    while True:
        with res.emu_lock:
            player_team,   player_ok = read_player_team_validated(res.core)
            enemy_team,    enemy_ok  = read_enemy_team_validated(res.core)
            p1_party_idx   = read_player_party_idx(res.core)
            opp_party_idx  = read_opp_party_idx(res.core)
            player_status2 = read_battle_mon_status2(res.core, 0)
            enemy_status2  = read_battle_mon_status2(res.core, 1)
            raw_item       = read_last_used_item(res.core)
            print('item', raw_item)
            opp_item = raw_item if raw_item in TRAINER_BATTLE_ITEM_IDS else 0
            if opp_item != 0 and TRAINER_BATTLE_ITEM_PS_IDS.get(opp_item) != APPROVED_OPPONENT_ITEMS:
                opp_item = 0
            if state.usedItem is not None and opp_item != state.usedItem:
                opp_item = 0
            if opp_item != 0:
                state.usedItem = opp_item
        if player_ok and enemy_ok:
            return player_team, enemy_team, p1_party_idx, opp_party_idx, player_status2, enemy_status2, opp_item
        _tr += 1
        print(f'[battle_loop] Team checksum mismatch on attempt {_tr}, retrying...')
        time.sleep(1 / 60)


def _build_ordered_teams(player_team: list, enemy_team: list,
                         p1_party_idx: int, opp_party_idx: int) -> tuple:
    """Build active-first team orderings and derive is_forced_switch. Pure function."""
    p1_active = min(p1_party_idx, len(player_team) - 1)
    p2_active = min(opp_party_idx, len(enemy_team) - 1)
    p1_ordered = ([player_team[p1_active]]
                  + [p for i, p in enumerate(player_team) if i != p1_active])
    p2_ordered = ([enemy_team[p2_active]]
                  + [p for i, p in enumerate(enemy_team)  if i != p2_active])
    is_forced_switch = bool(player_team) and player_team[p1_active].get('current_hp', 1) == 0
    return p1_active, p2_active, p1_ordered, p2_ordered, is_forced_switch


def _run_text_validation(res: BattleResources, state: BattleLoopState,
                         player_team: list, enemy_team: list,
                         p1_active: int, p2_active: int, opp_item: int) -> None:
    """Parse accumulated texts, build MemoryState, validate, and log mismatches."""
    parsed = parse_battle_texts(state.pending_texts)
    state.pending_texts.clear()
    if state.ps_state is None:
        return
    raw_opp_id    = res.opp_last_move_id[0] if res.opp_last_move_id else 0
    opp_move_name = res.moves_db.get(str(raw_opp_id), '') if raw_opp_id else ''
    mem = MemoryState(
        player_active_species=internal_species_name(player_team[p1_active].get('species', 0), res.species_db),
        opp_active_species=internal_species_name(enemy_team[p2_active].get('species', 0), res.species_db),
        opp_last_move_name=opp_move_name or None,
        last_p1_move_name=_resolve_p1_move_name(state.last_p1_action, state.ps_state),
        player_fainted=player_team[p1_active].get('current_hp', 1) == 0,
        opp_fainted=enemy_team[p2_active].get('current_hp', 1) == 0,
        item_id=opp_item,
    )
    for mm in validate(parsed, mem):
        line = f'[turn {state.turn_number}] MISMATCH: {mm}\n'
        print(f'[battle_text_validator]{line}', end='')
        res.val_log.write(line)


def _init_ps_battle(res: BattleResources, state: BattleLoopState,
                    p1_ordered: list, p2_ordered: list,
                    p1_active: int, p2_active: int) -> None:
    """First-turn: build gba_orders, convert teams to pipe strings, init PS battle."""
    state.p1_gba_order = [p1_active] + [i for i in range(len(p1_ordered)) if i != p1_active]
    state.p2_gba_order = [p2_active] + [i for i in range(len(p2_ordered)) if i != p2_active]
    p1_pipes = _gba_team_to_pipe_strings(p1_ordered, res.species_db, res.moves_db, res.items_db, res.abilities_db)
    p2_pipes = _gba_team_to_pipe_strings(p2_ordered, res.species_db, res.moves_db, res.items_db, res.abilities_db)
    resp = res.ipc.send({
        'new': True, 'team1': ']'.join(p1_pipes), 'team2': ']'.join(p2_pipes),
        'p1InitState': _build_init_state(p1_ordered),
        'p2InitState': _build_init_state(p2_ordered),
        **(res.badge_boosts or {}),
    })
    state.ps_state = parse_ipc_response(resp)
    if res.opp_items and res.opp_item_ps_id:
        state.ps_state['opp_items_remaining'] = res.opp_items
        state.ps_state['opp_items_initial']   = res.opp_items
        state.ps_state['opp_item_ps_id']      = res.opp_item_ps_id
    print('[battle_loop] PS battle initialized.')
    if res.recorder:
        res.recorder.on_state_update('init', state.turn_number, state.ps_state)


def _log_opp_last_action(res: BattleResources, opp_item_id: int, opp_move_id: int) -> None:
    """Print a human-readable label for what the opponent did last turn."""
    opp_item_name = TRAINER_BATTLE_ITEM_PS_IDS.get(opp_item_id, '') if opp_item_id else ''
    if opp_item_name:
        label = f'item:{opp_item_name}'
    elif opp_move_id:
        label = res.moves_db.get(str(opp_move_id), f'id#{opp_move_id:#06x}')
    else:
        label = '(none)'
    print(f'[battle_loop] Opponent last move: {label}')


def _record_reconcile(res: BattleResources, state: BattleLoopState, p2_action: str) -> None:
    if not res.recorder:
        return
    p1_name = _resolve_move_name(state.last_p1_action or '', state.ps_state, 0)
    p2_name = _resolve_move_name(p2_action, state.ps_state, 1)
    res.recorder.on_reconcile(state.turn_number - 1, state.last_p1_action or '',
                               p2_action, p1_name, p2_name)


def _skip_reconcile(res: BattleResources, state: BattleLoopState) -> None:
    state.skip_next_reconcile = False
    print('[battle_loop] Skipping reconcile (faint switch already applied)')
    if res.recorder:
        res.recorder.on_reconcile(state.turn_number - 1, state.last_p1_action or '',
                                  '', None, None, skipped=True)


def _do_reconcile(res: BattleResources, state: BattleLoopState,
                  player_team: list, enemy_team: list,
                  opp_party_idx: int, player_status2: int, enemy_status2: int,
                  p1_party_idx: int | None = None) -> bool:
    """Simulate and reconcile the previous turn. Returns True if battle ended."""
    opp_move_id = res.opp_last_move_id[0] if res.opp_last_move_id else 0
    if res.opp_last_move_id:
        res.opp_last_move_id[0] = 0
    opp_item_id = state.opp_item_for_reconcile
    _log_opp_last_action(res, opp_item_id, opp_move_id)
    p2_action = _build_p2_action(res, state, opp_item_id, opp_move_id)
    state.last_p2_action = p2_action
    _record_reconcile(res, state, p2_action)
    print(f'[battle_loop] Reconciling: p1={state.last_p1_action}  p2={p2_action}')
    state.ps_state = _simulate_and_reconcile(
        res.ipc, state.ps_state, state.last_p1_action, p2_action,
        player_team, enemy_team, state.p1_gba_order, state.p2_gba_order,
        opp_active_gba_slot=opp_party_idx,
        player_active_gba_slot=p1_party_idx,
        core=res.core, emu_lock=res.emu_lock,
        player_status2=player_status2, enemy_status2=enemy_status2,
        recorder=res.recorder, turn=state.turn_number - 1,
    )
    if res.recorder:
        res.recorder.on_state_update('reconcile', state.turn_number - 1, state.ps_state)
    if state.ps_state['is_over']:
        print(f"[battle_loop] Battle over — winner: {state.ps_state.get('winner', '?')}")
        return True
    return False


def _reconcile_turn(res: BattleResources, state: BattleLoopState,
                    player_team: list, enemy_team: list,
                    opp_party_idx: int, player_status2: int, enemy_status2: int,
                    p1_party_idx: int | None = None) -> bool:
    """Skip or perform reconcile for the previous turn. Returns True if battle ended."""
    if state.skip_next_reconcile:
        _skip_reconcile(res, state)
        return False
    return _do_reconcile(res, state, player_team, enemy_team,
                         opp_party_idx, player_status2, enemy_status2,
                         p1_party_idx=p1_party_idx)


def _select_faint_switch(res: BattleResources, state: BattleLoopState) -> str:
    """Pick the best forced switch-in via decision_fn or test actions."""
    available = state.ps_state.get('p1_switches') or []
    if res.test_actions:
        result = res.test_actions[state.test_action_idx % len(res.test_actions)]
        state.test_action_idx += 1
        print(f'[battle_loop] Faint-screen test action: {result}')
    else:
        result = _run_decision_fn(res, state, known_p2=None, label='Faint-switch')
    if result is None or not result.startswith('switch '):
        result = available[0]
        print(f'[battle_loop] Faint-screen: invalid action — defaulting to {result}')
    return result


def _apply_faint_switch(res: BattleResources, state: BattleLoopState,
                        injected_keys, best_switch: str) -> None:
    """Apply a forced faint switch to PS state and inject GBA button sequence."""
    ps_slot = int(best_switch.split()[1])
    gba_idx = ps_slot - 1
    resp = res.ipc.send({'battle': state.ps_state['battle'], 'p1': best_switch})
    state.ps_state = parse_ipc_response(resp)
    if res.recorder:
        res.recorder.on_state_update('faint_switch', state.turn_number, state.ps_state)
    state.p1_gba_order[0], state.p1_gba_order[ps_slot - 1] = (
        state.p1_gba_order[ps_slot - 1], state.p1_gba_order[0])
    print(f'[battle_loop] Faint switch: {best_switch} (GBA slot {gba_idx})')
    injected_keys.extend(_switch_button_sequence(gba_idx, from_action_screen=False))
    if res.recorder:
        res.recorder.on_faint(state.turn_number, best_switch)
    state.last_p1_action   = None
    state.skip_next_reconcile = True


def _faint_mini_reconcile(res: BattleResources, state: BattleLoopState,
                          player_team: list, enemy_team: list,
                          opp_party_idx: int, player_status2: int, enemy_status2: int,
                          p1_party_idx: int | None = None) -> None:
    """Advance PS through the turn that caused the player's Pokémon to faint."""
    opp_move_id = res.opp_last_move_id[0] if res.opp_last_move_id else 0
    if res.opp_last_move_id:
        res.opp_last_move_id[0] = 0
    p2_action = _build_p2_action(res, state, state.opp_item_for_reconcile, opp_move_id, log=False)
    print(f'[battle_loop] Faint-screen mini-reconcile: p1={state.last_p1_action}  p2={p2_action}')
    state.ps_state = _simulate_and_reconcile(
        res.ipc, state.ps_state, state.last_p1_action, p2_action,
        player_team, enemy_team, state.p1_gba_order, state.p2_gba_order,
        opp_active_gba_slot=opp_party_idx,
        player_active_gba_slot=p1_party_idx,
        core=res.core, emu_lock=res.emu_lock,
        player_status2=player_status2, enemy_status2=enemy_status2,
        recorder=res.recorder, turn=state.turn_number,
    )
    if res.recorder:
        res.recorder.on_state_update('faint_mini_reconcile', state.turn_number, state.ps_state)


def _select_and_inject_action(res: BattleResources, state: BattleLoopState,
                              injected_keys, is_forced_switch: bool,
                              opp_item_this_turn: int) -> bool:
    """Select best action via decision_fn; inject keys. Returns False if no action."""
    known_p2 = (f'item {TRAINER_BATTLE_ITEM_PS_IDS[opp_item_this_turn]}'
                if opp_item_this_turn else None)
    if known_p2:
        print(f'[battle_loop] Opponent will use {known_p2} this turn')
    best_action, _stats = _select_best_action(res, state, known_p2)
    if best_action is None:
        print('[battle_loop] Warning: search returned no action')
        return False
    if res.recorder:
        res.recorder.on_mcts_decision(state.turn_number, best_action, _stats)
    state.last_p1_action = best_action
    _inject_action_keys(injected_keys, best_action, is_forced_switch)
    return True


# ─── Level 2: per-frame handlers ─────────────────────────────────────────────

def _read_gba_frame(res: BattleResources) -> tuple:
    """Read all relevant GBA memory values for this frame under emu_lock."""
    with res.emu_lock:
        battle_comm     = read_battle_communication(res.core)
        battle_outcome  = read_battle_outcome(res.core)
        battler_fainted = read_battler_fainted(res.core)
        raw_text        = read_battle_text(res.core)
    return battle_comm, battle_outcome, battler_fainted, raw_text


def _update_text_debounce(state: BattleLoopState, raw_text: str) -> None:
    """Accumulate stable battle text into pending_texts (4-frame debounce)."""
    if raw_text != state.txt_candidate:
        state.txt_candidate        = raw_text
        state.txt_candidate_frames = 1
    else:
        state.txt_candidate_frames += 1
    if (state.txt_candidate_frames == _TEXT_STABLE_FRAMES
            and state.txt_candidate
            and state.txt_candidate != state.txt_last_recorded):
        state.pending_texts.append(state.txt_candidate)
        state.txt_last_recorded = state.txt_candidate


def _check_battle_outcome(res: BattleResources, battle_outcome: int) -> bool:
    """Return True (and notify recorder) if the battle has ended."""
    if battle_outcome == 0:
        return False
    outcome_names = {1: 'won', 2: 'lost', 3: 'ran', 4: 'caught', 5: 'draw'}
    label = outcome_names.get(battle_outcome, f'outcome={battle_outcome}')
    print(f'[battle_loop] Battle ended: {label}')
    if res.recorder:
        res.recorder.on_end(label)
    return True


def _update_menu_debounce(state: BattleLoopState, battle_comm: int) -> None:
    """Increment or reset at_menu_frames; reset a_press_counter on menu entry."""
    if battle_comm == 2:
        state.at_menu_frames  += 1
        state.a_press_counter  = 0
    else:
        state.at_menu_frames   = 0


def _update_faint_debounce(state: BattleLoopState,
                           battler_fainted: int, battle_comm: int) -> None:
    """Track faint flag and increment or reset faint_screen_frames."""
    if battler_fainted != 0:
        state.faintSetPreviously = True
    if battler_fainted == 0 and battle_comm != 2:
        state.faint_screen_frames += 1
    else:
        state.faint_screen_frames  = 0


def _handle_faint_screen(res: BattleResources, state: BattleLoopState,
                         injected_keys, at_faint_screen: bool) -> None:
    """Handle the forced switch-in when the player's active Pokémon has fainted."""
    if not (at_faint_screen and not state.prev_at_faint_screen
            and state.ps_state is not None and state.faintSetPreviously):
        return
    state.a_press_counter = 0
    player_team, enemy_team, p1_idx, opp_idx, p1_s2, p2_s2 = _read_team_state_faint(res)
    if state.last_p1_action is not None:
        _faint_mini_reconcile(res, state, player_team, enemy_team, opp_idx, p1_s2, p2_s2,
                              p1_party_idx=p1_idx)
    if not (state.ps_state.get('p1_switches') or []):
        print('[battle_loop] Warning: no p1_switches after faint mini-reconcile')
        return
    _apply_faint_switch(res, state, injected_keys, _select_faint_switch(res, state))


def _handle_menu(res: BattleResources, state: BattleLoopState,
                 injected_keys, raw_text: str, at_menu: bool) -> str | None:
    """Handle the action-selection menu. Returns 'break', 'continue', or None."""
    if not (at_menu and not state.prev_at_menu and _WHAT_WILL_RE.match(raw_text)):
        return None
    state.turn_number += 1
    state.prev_at_menu = True
    if res.recorder:
        res.recorder.current_turn = state.turn_number
    state.a_press_counter = 0
    player_team, enemy_team, p1_idx, opp_idx, p1_s2, p2_s2, opp_item = _read_team_state_menu(res, state)
    if not player_team or not enemy_team:
        print('[battle_loop] Warning: empty team after retries — will retry next frame')
        return 'continue'
    p1_active, p2_active, p1_ordered, p2_ordered, is_forced = _build_ordered_teams(
        player_team, enemy_team, p1_idx, opp_idx)
    state.opp_faint_switched = any('sent out' in t.lower() for t in state.pending_texts)
    _run_text_validation(res, state, player_team, enemy_team, p1_active, p2_active, opp_item)
    if state.ps_state is None:
        _init_ps_battle(res, state, p1_ordered, p2_ordered, p1_active, p2_active)
    elif _reconcile_turn(res, state, player_team, enemy_team, opp_idx, p1_s2, p2_s2,
                         p1_party_idx=p1_idx):
        return 'break'
    state.opp_item_for_reconcile = opp_item
    _print_team('Player team',   p1_ordered, res.species_db, res.moves_db)
    _print_team('Opponent team', p2_ordered, res.species_db, res.moves_db)
    if not _select_and_inject_action(res, state, injected_keys, is_forced, opp_item):
        return 'continue'
    return None


def _handle_auto_a(state: BattleLoopState, injected_keys, at_menu: bool) -> None:
    """Periodically press A to advance battle text when not at menu."""
    if at_menu or state.at_menu_frames != 0 or injected_keys:
        return
    state.a_press_counter += 1
    if state.a_press_counter >= _A_PRESS_INTERVAL:
        _press_a(injected_keys)
        state.a_press_counter = 0


# ─── Level 1: loop driver ─────────────────────────────────────────────────────

def _run_loop(res: BattleResources, state: BattleLoopState, injected_keys) -> None:
    """Main per-frame battle loop."""
    while True:
        battle_comm, battle_outcome, battler_fainted, raw_text = _read_gba_frame(res)
        _update_text_debounce(state, raw_text)
        if _check_battle_outcome(res, battle_outcome):
            break
        _update_menu_debounce(state, battle_comm)
        _update_faint_debounce(state, battler_fainted, battle_comm)
        at_menu         = state.at_menu_frames       >= _AT_MENU_STABLE
        at_faint_screen = state.faint_screen_frames  >= _FAINT_SCREEN_STABLE
        _handle_faint_screen(res, state, injected_keys, at_faint_screen)
        menu_result = _handle_menu(res, state, injected_keys, raw_text, at_menu)
        if menu_result == 'break':
            break
        if menu_result is None:
            _handle_auto_a(state, injected_keys, at_menu)
        if not at_menu:
            state.prev_at_menu = False
        state.prev_at_faint_screen = at_faint_screen
        time.sleep(1 / 30)


# ─── Level 0: public entry point ─────────────────────────────────────────────

def run_battle_loop(core, emu_lock, node_script_path, injected_keys,
                    opp_last_move_id=None,
                    badge_boosts: dict | None = None,
                    shallow_workers: int = 30,
                    test_actions: list | None = None,
                    recorder: 'BattleRecord | None' = None,
                    decision_fn=None,
                    matchup_cache_path: str | None = None,
                    trainer_name: str | None = None):
    """Run the full battle autonomously from the first menu detection until battle end.

    Polls gBattleCommunication[0] at ~30 Hz.  When not at the battle menu, injects
    periodic A presses to advance turn text.  When the menu is first detected each turn,
    runs shallow search and injects the best action.  After each subsequent menu appearance,
    simulates the previous turn in PS using the actual opponent move and reconciles
    HP/PP/status with GBA ground truth before running search for the new turn.
    """
    from datetime import datetime
    opp_ai_flags = _flags_for_trainer(trainer_name)
    print(f'[battle_loop] Trainer: {trainer_name or "unknown"} → opponent AI flags {opp_ai_flags}')
    species_db   = _load_db('gen3_species.json')
    moves_db     = _load_db('gen3_move_names.json')
    items_db     = _load_db('gen3_items.json')
    abilities_db = _load_db('gen3_abilities.json')
    ipc          = NodeIPC(node_script_path)
    shallow_proc = ShallowSearchProcess(node_script_path, num_workers=shallow_workers,
                                        matchup_cache_path=matchup_cache_path)
    if decision_fn is None:
        decision_fn = lambda _ipc, state: shallow_proc.search(state)
    log_dir      = os.path.join(_HERE, 'test_logs')
    os.makedirs(log_dir, exist_ok=True)
    val_log_path = os.path.join(log_dir, f'validation_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
    val_log      = open(val_log_path, 'w', buffering=1)
    res = BattleResources(
        core=core, emu_lock=emu_lock, node_script_path=node_script_path,
        ipc=ipc,
        species_db=species_db, moves_db=moves_db, items_db=items_db, abilities_db=abilities_db,
        opp_last_move_id=opp_last_move_id, badge_boosts=badge_boosts,
        test_actions=test_actions,
        recorder=recorder, val_log=val_log, decision_fn=decision_fn,
        opp_ai_flags=opp_ai_flags,
        opp_items=OPP_ITEMS,
        opp_item_ps_id=APPROVED_OPPONENT_ITEMS or '',
    )
    try:
        _run_loop(res, BattleLoopState(), injected_keys)
    finally:
        shallow_proc.close()
        ipc.close()
        val_log.close()
        print('[battle_loop] IPC closed.')


