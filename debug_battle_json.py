#!/usr/bin/env python3
"""
Inspect the battle JSON from @pkmn/sim to verify field locations needed
for ai_flags BattleContext construction.

Run: python debug_battle_json.py [path/to/Connection.js]
"""
import pprint
import sys

from NodeIPC import NodeIPC
from MatchupInfo import BOX, OPPONENT

from config import NODE_SCRIPT


def first_active(side):
    """Return the first active Pokémon dict from a side dict."""
    for p in side['pokemon']:
        if p.get('isActive'):
            return p
    return side['pokemon'][0]


def inspect(label, value):
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print('=' * 60)
    pprint.pprint(value, depth=6)


FIELDS_OF_INTEREST = [
    'moveSlots', 'storedStats', 'types', 'status',
    'boosts', 'volatiles', 'ability', 'baseAbility',
    'item', 'lastItem', 'ateBerry',
    'lastMove', 'lastMoveUsed',
    'gender', 'level', 'hp', 'maxhp',
    'fainted', 'isActive', 'trapped',
]

ipc = NodeIPC(NODE_SCRIPT)
try:
    # Build minimal team strings (just the first Pokémon from each side)
    p1_name = next(iter(BOX))
    p2_name = next(iter(OPPONENT))
    team1 = BOX[p1_name]
    team2 = OPPONENT[p2_name]

    print(f"Starting battle: {p1_name} vs {p2_name}")

    # ------------------------------------------------------------------ #
    # Initial state
    # ------------------------------------------------------------------ #
    resp0 = ipc.send({"new": True, "team1": team1, "team2": team2})
    battle0 = resp0["result"]["battle"]
    p1_0 = first_active(battle0['sides'][0])

    inspect("battle.keys()", list(battle0.keys()))
    inspect("battle['field']", battle0.get('field'))
    inspect("sides[0].keys()", list(battle0['sides'][0].keys()))
    inspect("p1 pokemon keys (initial)", list(p1_0.keys()))

    print("\n\n### Per-field inspection (initial state) ###")
    for fn in FIELDS_OF_INTEREST:
        if fn in p1_0:
            inspect(f"p1['{fn}'] — initial", p1_0[fn])
        else:
            print(f"\n  *** '{fn}' NOT FOUND in initial pokemon dict ***")

    # ------------------------------------------------------------------ #
    # After one turn
    # ------------------------------------------------------------------ #
    print("\n\n### After one turn (move 1 vs move 1) ###")
    resp1 = ipc.send({"battle": battle0, "p1": "move 1", "p2": "move 1"})
    result1 = resp1["result"]
    battle1 = result1["battle"]
    p1_1 = first_active(battle1['sides'][0])

    for fn in ['lastMove', 'lastMoveUsed', 'boosts', 'volatiles', 'status', 'hp', 'storedStats']:
        if fn in p1_1:
            inspect(f"p1['{fn}'] — after turn 1", p1_1[fn])
        else:
            print(f"\n  *** '{fn}' NOT FOUND after turn ***")

    # Damage calcs (added by user to Connection.js)
    if "p1DmgCalcs" in result1:
        inspect("p1DmgCalcs", result1["p1DmgCalcs"])
    else:
        print("\n  *** p1DmgCalcs not yet in IPC response ***")

    if "p2DmgCalcs" in result1:
        inspect("p2DmgCalcs", result1["p2DmgCalcs"])

finally:
    ipc.close()
