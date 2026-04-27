"""
Battle simulation utilities for Pokemon Showdown IPC battles.

Provides state parsing, turn simulation, team assembly, and AI move selection.
The opponent uses ai_flags for move selection; the shallow search subprocess
handles player action selection in battle_mode.py.
"""

import json
import random
from typing import Optional

from MatchupInfo import BOX, OPPONENT
from ai_flags import (
    BattleContext, PokemonState, MoveInfo, PokeType, Weather, MoveCategory,
    score_move, make_rng,
)
from gen3_data import get_move_info, TYPE_MAP
from ai_switch import select_switch_in


def _rand_battle(battle: dict) -> dict:
    """Return a shallow copy of the PS battle JSON with a fresh random PRNG seed.

    The PS 'prng' field is a top-level array of 4 × 16-bit integers encoding the
    LCG state.  Overwriting it before each IPC send ensures simulations from the
    same node explore different RNG outcomes (crits, accuracy rolls, secondary
    effects) rather than deterministically replaying the same trajectory every visit.
    """
    return {**battle, 'prng': [random.randint(0, 65535) for _ in range(4)]}


def parse_move_actions(moves_str: str) -> list:
    """Parse IPC p1Moves string ("1:2:3:4") into ["move 1", "move 2", ...].

    Indices in the IPC string are 0-based colon-separated integers.
    Falls back to ["move 1"] if no valid entries are parsed (all PP depleted),
    which causes the simulator to use Struggle.
    """
    actions = []
    for part in str(moves_str).split(':'):
        try:
            actions.append(f"move {int(part.strip()) + 1}")
        except ValueError:
            pass
    return actions if actions else ["move 1"]


def parse_switch_actions(switches_str: str) -> list:
    """Parse IPC p1Switches string ("2:3") into ["switch 2", "switch 3", ...].

    Indices are 1-based colon-separated integers.
    Returns an empty list if no valid entries are found.
    """
    actions = []
    for part in str(switches_str).split(':'):
        try:
            actions.append(f"switch {int(part.strip()) + 1}")
        except ValueError:
            pass
    return actions


def detect_winner(battle_state: dict) -> Optional[str]:
    """Determine the winner by checking which side still has Pokémon with HP > 0.

    Returns "p1", "p2", or None if the result is indeterminate.
    """
    sides = battle_state.get("sides", [])
    if len(sides) < 2:
        return None
    p1_alive = any(p.get("hp", 0) > 0 for p in sides[0].get("pokemon", []))
    p2_alive = any(p.get("hp", 0) > 0 for p in sides[1].get("pokemon", []))
    if p1_alive and not p2_alive:
        return "p1"
    if p2_alive and not p1_alive:
        return "p2"
    return None


def _filter_p1_moves(p1_moves: list, battle: dict) -> list:
    """Remove moves from p1_moves that cannot have any effect this turn.

    Currently handled:
      Fake Out — only works on the first turn the Pokémon is active (activeTurns == 1).
      After that it always fails, so it is removed to prevent the search from
      wasting a turn on it.  If it is the only remaining move it is kept so
      that the search/simulator can handle the edge case gracefully.
    """
    sides = battle.get("sides", [])
    if not sides:
        return p1_moves
    active = next((p for p in sides[0].get("pokemon", []) if p.get("isActive")), None)
    if active is None:
        return p1_moves

    active_turns = active.get("activeTurns", 1)
    if active_turns <= 1:
        return p1_moves  # first turn — Fake Out can work normally

    move_slots = active.get("moveSlots", [])
    fake_out_actions = {
        f"move {i + 1}"
        for i, slot in enumerate(move_slots)
        if slot.get("id") == "fakeout"
    }
    if not fake_out_actions:
        return p1_moves

    filtered = [m for m in p1_moves if m not in fake_out_actions]
    return filtered if filtered else p1_moves  # keep if it would empty the list


def parse_ipc_response(response: dict) -> dict:
    """Convert a raw IPC response into a battle state dict.

    Terminal detection: battle is over when all four action keys (p1Moves,
    p1Switches, p2Moves, p2Switches) are absent. If only p2Switches is present
    (opponent forced switch), the battle is NOT over.

    Returns a dict with keys:
        battle        – raw battle state dict from IPC
        p1_moves      – list of "move N" action strings
        p1_switches   – list of "switch N" action strings
        p2_moves      – list of "move N" action strings for the opponent
        p2_switches   – list of "switch N" action strings for the opponent
        is_over       – True when the battle is finished
        winner        – "p1", "p2", or None
    """
    result = response["result"]
    battle = result["battle"]

    p1_moves_str = result.get("p1Moves")
    p1_switches_str = result.get("p1Switches")
    p2_moves_str = result.get("p2Moves")
    p2_switches_str = result.get("p2Switches")

    p1_moves = parse_move_actions(p1_moves_str) if p1_moves_str is not None else []
    p1_moves = _filter_p1_moves(p1_moves, battle)
    p1_switches = parse_switch_actions(p1_switches_str) if p1_switches_str is not None else []
    p2_moves = parse_move_actions(p2_moves_str) if p2_moves_str is not None else []
    p2_switches = parse_switch_actions(p2_switches_str) if p2_switches_str is not None else []

    is_over = (
        p1_moves_str is None and p1_switches_str is None and
        p2_moves_str is None and p2_switches_str is None
    )
    winner = detect_winner(battle) if is_over else None

    # Damage calculations added by Connection.js (raw HP damage per move action)
    p1_dmg_calcs = result.get("p1DmgCalcs") or {}
    p2_dmg_calcs = result.get("p2DmgCalcs") or {}

    # Crit damage calculations: max possible damage (crit hit) for active and bench
    p2_crit_dmg_calcs       = result.get("p2CritDmgCalcs") or {}
    p2_crit_dmg_calcs_bench = result.get("p2CritDmgCalcsBench") or {}

    # Per-move accuracy / secondary chance added by Connection.js
    p1_move_info = result.get("p1MoveInfo") or []
    p2_move_info = result.get("p2MoveInfo") or []

    # Lum Berry status absorption (added by Connection.js).
    # Each field is a status ID string (e.g. 'psn', 'confusion') or None.
    p1_lum_blocked = result.get("p1LumBlocked")
    p2_lum_blocked = result.get("p2LumBlocked")

    return {
        "battle": battle,
        "p1_moves": p1_moves,
        "p1_switches": p1_switches,
        "p2_moves": p2_moves,
        "p2_switches": p2_switches,
        "is_over": is_over,
        "winner": winner,
        "p1_dmg_calcs": p1_dmg_calcs,
        "p2_dmg_calcs": p2_dmg_calcs,
        "p2_crit_dmg_calcs": p2_crit_dmg_calcs,
        "p2_crit_dmg_calcs_bench": p2_crit_dmg_calcs_bench,
        "p1_move_info": p1_move_info,
        "p2_move_info": p2_move_info,
        "p1_lum_blocked": p1_lum_blocked,
        "p2_lum_blocked": p2_lum_blocked,
    }


# ---------------------------------------------------------------------------
# Battle context construction from IPC state
# ---------------------------------------------------------------------------

_PS_WEATHER_MAP: dict = {
    "RainDance": Weather.RAIN,
    "SunnyDay":  Weather.SUN,
    "sunnyday":  Weather.SUN,
    "Sandstorm": Weather.SAND,
    "Hail":      Weather.HAIL,
    "hail":      Weather.HAIL,
}

_PS_STATUS_MAP: dict = {
    "slp": "sleep",
    "par": "paralyze",
    "psn": "poison",
    "tox": "badly_poison",
    "brn": "burn",
    "frz": "freeze",
}

# Lowercase PS ability ID → display name used in ai_flags checks
_ABILITY_NAME_MAP: dict = {
    "levitate":    "Levitate",
    "immunity":    "Immunity",
    "insomnia":    "Insomnia",
    "vitalspirit": "Vital Spirit",
    "owntempo":    "Own Tempo",
    "limber":      "Limber",
    "waterveil":   "Water Veil",
    "soundproof":  "Soundproof",
    "clearbody":   "Clear Body",
    "damp":        "Damp",
    "sturdy":      "Sturdy",
    "wonderguard": "Wonder Guard",
    "voltabsorb":  "Volt Absorb",
    "waterabsorb": "Water Absorb",
    "flashfire":   "Flash Fire",
    "oblivious":   "Oblivious",
}


def _get_active_pokemon(side: dict) -> Optional[dict]:
    """Return the first active Pokémon dict from a side dict."""
    for p in side.get("pokemon", []):
        if p.get("isActive"):
            return p
    pokes = side.get("pokemon", [])
    return pokes[0] if pokes else None


def _parse_last_move(pokemon: dict) -> Optional[MoveInfo]:
    """Extract the last move used as a MoveInfo (or None)."""
    last = pokemon.get("lastMove") or pokemon.get("lastMoveUsed")
    if not last:
        return None
    # PS format: {'move': '[Move:return]', ...} or just a move ID string
    raw = last if isinstance(last, str) else last.get("move", "")
    # Strip PS wrapper like '[Move:return]'
    if raw.startswith("[Move:"):
        raw = raw[6:-1]
    return get_move_info(raw.lower().replace(" ", ""))


def _parse_hp_pct(pokemon: dict) -> int:
    hp = pokemon.get("hp", 1)
    maxhp = pokemon.get("maxhp", 1) or 1
    return int(hp / maxhp * 100)


def _parse_types(pokemon: dict) -> list:
    return [TYPE_MAP[t] for t in pokemon.get("types", ["Normal"])
            if t in TYPE_MAP]


def _parse_ability(pokemon: dict) -> str:
    ability_id = pokemon.get("ability", "")
    return _ABILITY_NAME_MAP.get(ability_id, ability_id.title())


def _parse_status(pokemon: dict) -> Optional[str]:
    status_raw = pokemon.get("status", "") or ""
    return _PS_STATUS_MAP.get(status_raw) if status_raw else None


def _parse_moves(pokemon: dict) -> list:
    moves = []
    for slot in pokemon.get("moveSlots", []):
        mi = get_move_info(slot.get("id", ""))
        if mi is not None:
            moves.append(mi)
    return moves


def _parse_gender(pokemon: dict) -> str:
    gender_raw = pokemon.get("gender", "") or ""
    if gender_raw == "M":
        return "male"
    if gender_raw == "F":
        return "female"
    return "genderless"


def _parse_boosts(pokemon: dict) -> dict:
    boosts = pokemon.get("boosts", {})
    return {
        "atk_stage": boosts.get("atk", 0),
        "def_stage": boosts.get("def", 0),
        "spa_stage": boosts.get("spa", 0),
        "spd_stage": boosts.get("spd", 0),
        "spe_stage": boosts.get("spe", 0),
        "acc_stage": boosts.get("accuracy", 0),
        "eva_stage": boosts.get("evasion", 0),
    }


def _parse_volatiles(pokemon: dict, side: dict, side_conditions: dict) -> dict:
    volatiles = pokemon.get("volatiles", {}) or {}
    stockpile_entry = volatiles.get("stockpile")
    stockpile_count = (
        stockpile_entry.get("level", 0) if isinstance(stockpile_entry, dict)
        else (1 if stockpile_entry else 0)
    )

    sc = side_conditions
    protected_consecutive = 1 if ("stall" in volatiles or "protect" in volatiles) else 0
    has_other_pokemon = any(
        p.get("hp", 0) > 0 and not p.get("isActive")
        for p in side.get("pokemon", [])
    )

    return {
        "confused":              "confusion" in volatiles,
        "infatuated":            "attract" in volatiles,
        "cursed":                "curse" in volatiles,
        "stockpile_count":       stockpile_count,
        "protected_consecutive": protected_consecutive,
        "under_safeguard":       "safeguard" in sc,
        "under_reflect":         "reflect" in sc,
        "under_light_screen":    "lightscreen" in sc,
        "under_substitute":      "substitute" in volatiles,
        "under_ingrain":         "ingrain" in volatiles,
        "under_focus_energy":    "focusenergy" in volatiles,
        "under_mist":            "mist" in sc,
        "under_nightmare":       "nightmare" in volatiles,
        "under_perish_song":     "perishsong" in volatiles,
        "under_leech_seed":      "leechseed" in volatiles,
        "under_disable":         "disable" in volatiles,
        "under_encore":          "encore" in volatiles,
        "under_torment":         "torment" in volatiles,
        "under_foresight":       ("foresight" in volatiles or "odorsleuth" in volatiles),
        "under_mean_look":       ("meanlook" in volatiles or "spiderweb" in volatiles),
        "under_lock_on":         ("lockon" in volatiles or "mindreader" in volatiles),
        "under_attract":         "attract" in volatiles,
        "under_yawn":            "yawn" in volatiles,
        "under_future_sight":    "futuresight" in volatiles,
        "imprison_active":       "imprison" in volatiles,
        "taunted":               "taunt" in volatiles,
        "has_other_pokemon":     has_other_pokemon,
    }


def _build_pokemon_state(
    pokemon: dict,
    side: dict,
    side_conditions: dict,
) -> PokemonState:
    """Convert an IPC pokemon dict + side conditions into a PokemonState."""
    boosts    = _parse_boosts(pokemon)
    volatiles = _parse_volatiles(pokemon, side, side_conditions)
    return PokemonState(
        hp_pct         = _parse_hp_pct(pokemon),
        types          = _parse_types(pokemon),
        ability        = _parse_ability(pokemon),
        status         = _parse_status(pokemon),
        moves          = _parse_moves(pokemon),
        held_item      = pokemon.get("item") or None,
        used_item      = pokemon.get("lastItem") or None,
        level          = pokemon.get("set", {}).get("level", 50) if pokemon.get("set") else 50,
        gender         = _parse_gender(pokemon),
        speed          = pokemon.get("speed", 100),
        is_first_turn  = pokemon.get("activeTurns", 1) == 1,
        last_move_used = _parse_last_move(pokemon),
        **boosts,
        **volatiles,
    )


def build_battle_context(
    state: dict,
    move_action: str,
    player: int = 1,
) -> Optional[BattleContext]:
    """Build a BattleContext for scoring one move with ai_flags.score_move().

    Args:
        state: dict returned by parse_ipc_response (with p1_dmg_calcs).
        move_action: "move N" string (1-based slot index).
        player: 1 = p1 is the user, 2 = p2 is the user.

    Returns:
        BattleContext, or None if the move cannot be resolved.
    """
    battle = state["battle"]
    sides = battle.get("sides", [])
    if len(sides) < 2:
        return None

    user_side_idx   = 0 if player == 1 else 1
    target_side_idx = 1 - user_side_idx

    user_pokemon   = _get_active_pokemon(sides[user_side_idx])
    target_pokemon = _get_active_pokemon(sides[target_side_idx])
    if user_pokemon is None or target_pokemon is None:
        return None

    # Resolve move slot
    parts = move_action.split()
    if len(parts) != 2 or parts[0] != "move":

        return None
    slot_idx = int(parts[1]) - 1
    move_slots = user_pokemon.get("moveSlots", [])
    if slot_idx < 0 or slot_idx >= len(move_slots):
        return None

    move_id = move_slots[slot_idx].get("id", "")
    move_info = get_move_info(move_id)
    if move_info is None:
        return None

    # Side conditions for each side
    def _sc(side):
        return side.get("sideConditions", {}) or {}

    user_state   = _build_pokemon_state(
        user_pokemon,   sides[user_side_idx],   _sc(sides[user_side_idx]))
    target_state = _build_pokemon_state(
        target_pokemon, sides[target_side_idx], _sc(sides[target_side_idx]))

    # Weather
    weather_str = battle.get("field", {}).get("weather", "") or ""
    weather = _PS_WEATHER_MAP.get(weather_str, Weather.NONE)

    # Damage percentages: convert raw HP damage to % of target's current HP
    calcs_key = "p1_dmg_calcs" if player == 1 else "p2_dmg_calcs"
    raw_calcs = state.get(calcs_key, {})
    target_maxhp = target_pokemon.get("maxhp", 1) or 1
    move_damage_pcts: dict = {}
    for all_slots in user_pokemon.get("moveSlots", []):
        slot_id = all_slots.get("id", "")
        slot_mi = get_move_info(slot_id)
        if slot_mi is None:
            continue
        # Find the matching action key (e.g., "move 1")
        for i, s in enumerate(user_pokemon.get("moveSlots", []), 1):
            if s.get("id") == slot_id:
                raw = raw_calcs.get(f"move {i}", 0) or 0
                roll = random.randint(85, 100) / 100  # Emerald: 100 - (Random() % 16)
                move_damage_pcts[slot_mi.name] = raw * roll / target_maxhp * 100
                break

    is_first_battle_turn = battle.get("turn", 1) <= 1

    return BattleContext(
        user=user_state,
        target=target_state,
        move=move_info,
        weather=weather,
        is_first_battle_turn=is_first_battle_turn,
        move_damage_pcts=move_damage_pcts,
    )


def select_move_with_ai_flags(
    state: dict,
    move_actions: list,
    player: int = 1,
    active_flags: list = None,
) -> str:
    """Score all move_actions with ai_flags and return the highest-scoring one.

    Falls back to the first action if no scores can be computed.

    Args:
        state: parse_ipc_response state dict.
        move_actions: list of "move N" action strings.
        player: 1 = p1, 2 = p2.
        active_flags: AI flags to apply (defaults to [0, 1, 2]).
    """
    if active_flags is None:
        active_flags = [0, 1, 2]

    if not move_actions:
        return None

    rng = make_rng()
    scores: dict[str, int] = {}

    for action in move_actions:
        ctx = build_battle_context(state, action, player)
        if ctx is None:
            continue
        scores[action] = score_move(ctx, active_flags, rng)

    if not scores:
        return move_actions[0]

    best_score = max(scores.values())
    tied = [a for a, s in scores.items() if s == best_score]
    return random.choice(tied)


def get_p2_move_candidates(
    state: dict,
    move_actions: list,
    player: int = 2,
    active_flags: list = None,
    log: bool = False,
    n_samples: int = 200,
) -> list:
    """Return all opponent moves that could be selected, with their true probabilities.

    Samples N independent RNG trials. Each trial scores all moves with a shared
    make_rng() (matching the game's continuous RNG state), then distributes 1/k weight
    equally among the k tied-max moves (matching the game's Random() % numOfBestMoves).
    Returns all actions with probability > 0, sorted descending.

    Args:
        state: parse_ipc_response state dict.
        move_actions: list of "move N" (or "switch N") action strings for player.
        player: which player's perspective (default 2 for opponent).
        active_flags: AI flags to apply (defaults to [0, 1, 2]).
        log: if True, print per-move probabilities to stdout.
        n_samples: number of RNG trials (default 200).

    Returns:
        list of (action, probability) tuples summing to 1.0, sorted by probability desc.
    """
    if active_flags is None:
        active_flags = [0, 1, 2]

    if not move_actions:
        return []

    # Build contexts once — avoids rebuilding on every trial
    ctxs: dict[str, object] = {}
    move_names: dict[str, str] = {}
    for action in move_actions:
        ctx = build_battle_context(state, action, player)
        if ctx is not None:
            ctxs[action] = ctx
            move_names[action] = ctx.move.name

    if not ctxs:
        return [(move_actions[0], 1.0)]

    # Sample N trials
    tallies: dict[str, float] = {a: 0.0 for a in ctxs}
    for _ in range(n_samples):
        rng = make_rng()
        trial_scores = {a: score_move(ctx, active_flags, rng) for a, ctx in ctxs.items()}
        best = max(trial_scores.values())
        winners = [a for a, s in trial_scores.items() if s == best]
        weight = 1.0 / len(winners)
        for a in winners:
            tallies[a] += weight

    candidates = [(a, tallies[a] / n_samples)
                  for a in ctxs if tallies[a] > 0]
    candidates.sort(key=lambda x: x[1], reverse=True)

    if log:
        print('[battle_loop] Opp move probabilities:')
        for action in move_actions:
            if action not in ctxs:
                continue
            name = move_names.get(action, action)
            prob = tallies[action] / n_samples
            tag  = f'  prob={prob:.2f}' if prob > 0 else '  prob=0.00'
            print(f'  {action} ({name:<18s}){tag}')

    return candidates


def _pick_opponent_action(state: dict, active_flags: list = None) -> Optional[str]:
    """Select the best action for the opponent (p2) using ai_flags or switch logic.

    Uses ai_flags scoring for moves, select_switch_in for switches.
    active_flags defaults to [0, 1, 2] (flags 0/1/2 only) when not specified.
    Returns None if neither moves nor switches are available.
    """
    if state["p2_moves"]:
        scored = select_move_with_ai_flags(state, state["p2_moves"], player=2,
                                           active_flags=active_flags)
        return scored if scored is not None else state["p2_moves"][0]
    if state["p2_switches"]:
        return select_switch_in(state, state["p2_switches"], player=2)
    return None


def can_player_switch(state: dict) -> bool:
    """Return True if the player is allowed to voluntarily switch this turn.

    Switching is permitted when any of the following hold:
    - The opponent's active Pokémon just switched in (activeTurns == 1), or
    - The opponent can one-shot the player's active Pokémon with a normal hit
      (any p2_dmg_calcs value >= the player's current active HP), or
    - The opponent can one-shot the player's active Pokémon with a critical hit
      (p2_crit_dmg_calcs_bench is present, implying a crit KO is possible).

    Use get_voluntary_switches() to also filter out crit-unsafe switch targets.
    Intended for use in shallow_search.py where p1_switched_last history is
    not tracked.
    """
    battle = state.get("battle", {})
    sides = battle.get("sides", [])
    if len(sides) < 2:
        return False

    opp_active = _get_active_pokemon(sides[1])
    if opp_active is not None and opp_active.get("activeTurns", 2) == 1:
        return True

    p1_active = _get_active_pokemon(sides[0])
    if p1_active is not None:
        p1_hp = p1_active.get("hp", 0)
        if state.get("p2_dmg_calcs") and max(state["p2_dmg_calcs"].values(), default=0) >= p1_hp:
            return True

    if state.get("p2_crit_dmg_calcs_bench"):
        return True

    return False


def get_voluntary_switches(state: dict, p1_switches: list) -> list:
    """Return the subset of p1_switches that are safe to make voluntarily this turn.

    Three cases:
    - Opponent just switched in, or normal-hit KO threat: all switches are allowed.
    - Crit-hit KO threat only (p2_crit_dmg_calcs_bench present): allow switches to
      bench Pokémon whose current HP exceeds the maximum crit damage any opponent
      move can deal to them.  Bench slot key = str(slot_index) where slot_index =
      int("switch N".split()[1]) - 1, matching the 0-based party array index.
    - No threat: returns an empty list.
    """
    if not p1_switches:
        return []

    battle = state.get("battle", {})
    sides = battle.get("sides", [])
    if len(sides) < 2:
        return []

    # Opp just switched in → unrestricted
    opp_active = _get_active_pokemon(sides[1])
    if opp_active is not None and opp_active.get("activeTurns", 2) == 1:
        return list(p1_switches)

    # Normal-hit KO threat → unrestricted
    p1_active = _get_active_pokemon(sides[0])
    if p1_active is not None:
        p1_hp = p1_active.get("hp", 0)
        if state.get("p2_dmg_calcs") and max(state["p2_dmg_calcs"].values(), default=0) >= p1_hp:
            return list(p1_switches)

    # Crit-hit KO threat → filter to bench pokemon outside crit KO range
    bench_calcs = state.get("p2_crit_dmg_calcs_bench")
    if bench_calcs:
        bench_pokemon = sides[0].get("pokemon", [])
        safe = []
        for switch in p1_switches:
            slot = int(switch.split()[1]) - 1        # "switch N" → 0-based party index
            key  = str(slot)
            if key not in bench_calcs:
                safe.append(switch)                  # no crit data → not in KO range
            else:
                poke_hp      = bench_pokemon[slot].get("hp", 0) if slot < len(bench_pokemon) else 0
                max_crit_dmg = max(bench_calcs[key].values(), default=0)
                if poke_hp > max_crit_dmg:
                    safe.append(switch)
        return safe

    return []


def simulate_turn(ipc, state: dict, p1_action: str, p2_action: Optional[str],
                  flags: dict = None) -> dict:
    """Simulate one battle turn via IPC and return the resulting state.

    Randomizes the PRNG on every call to ensure diverse RNG outcomes.
    Automatically advances through any forced opponent switches after a faint.

    Args:
        ipc: NodeIPC instance.
        state: Current battle state dict (from parse_ipc_response).
        p1_action: Player's action string ("move N" or "switch N").
        p2_action: Opponent's action string, or None if the opponent has no action.
        flags: Optional dict of simulator flags (e.g. P1QuantizedRNG, P1ForceCrit).
               Applied to the attack turn only, not to forced-switch follow-ups.

    Returns:
        New battle state dict from parse_ipc_response, with added keys:
        - p1_crit_chance: float or None — crit probability for p1's move this turn.
        - p2_crit_chance: float or None — crit probability for p2's move this turn.
        - p1_accuracy_chance: float or None — hit probability for p1's move this turn.
        - p2_accuracy_chance: float or None — hit probability for p2's move this turn.
        - p1_secondary_chance: float or None — secondary-effect probability for p1 this turn.
        - p2_secondary_chance: float or None — secondary-effect probability for p2 this turn.
    """
    data = {"battle": _rand_battle(state["battle"]), "p1": p1_action}
    if p2_action is not None:
        data["p2"] = p2_action
    if flags:
        data.update(flags)

    raw = ipc.send(data)
    result = raw["result"]
    p1_crit_chance     = result.get("p1CritChance")
    p2_crit_chance     = result.get("p2CritChance")
    p1_accuracy_chance = result.get("p1AccuracyChance")
    p2_accuracy_chance = result.get("p2AccuracyChance")
    p1_secondary_chance = result.get("p1SecondaryChance")
    p2_secondary_chance = result.get("p2SecondaryChance")
    new_state = parse_ipc_response(raw)

    # Auto-advance forced opponent switches (e.g. after a faint)
    # Flags are intentionally omitted — no attack occurs during forced switches.
    while (not new_state["is_over"]
           and not new_state["p1_moves"]
           and not new_state["p1_switches"]):
        p2 = _pick_opponent_action(new_state)
        if p2 is None:
            break
        data = {"battle": _rand_battle(new_state["battle"]), "p2": p2}
        new_state = parse_ipc_response(ipc.send(data))

    new_state["p1_crit_chance"]     = p1_crit_chance
    new_state["p2_crit_chance"]     = p2_crit_chance
    new_state["p1_accuracy_chance"] = p1_accuracy_chance
    new_state["p2_accuracy_chance"] = p2_accuracy_chance
    new_state["p1_secondary_chance"] = p1_secondary_chance
    new_state["p2_secondary_chance"] = p2_secondary_chance

    # Propagate opponent item tracking fields from the parent state.
    # If p2 used an item this turn, decrement the remaining count.
    for _k in ('opp_items_remaining', 'opp_items_initial', 'opp_item_ps_id', 'opp_ai_flags',
               'move_lock'):
        if _k in state:
            new_state[_k] = state[_k]
    if p2_action and str(p2_action).startswith('item ') and 'opp_items_remaining' in new_state:
        new_state['opp_items_remaining'] = max(0, new_state['opp_items_remaining'] - 1)

    return new_state


def assemble_team_string(ordered_names: list, box: dict) -> str:
    """Build an IPC team string from an ordered list of Pokémon names."""
    return "]".join(box[p] for p in ordered_names)


def assemble_opponent_string(opp_first: str, opp_dict: dict) -> str:
    """Build an IPC opponent team string with opp_first as the lead Pokémon."""
    others = [p for p in opp_dict if p != opp_first]
    return "]".join(opp_dict[p] for p in [opp_first] + others)


def reorder_team(team, assignment: dict, opponents: list) -> list:
    """Reorder team so position i counters opponents[i].

    Team members not in assignment (flex slots) follow in original order.
    """
    inv = {opp: p for p, opp in assignment.items()}
    ordered = [inv[opp] for opp in opponents if opp in inv]
    assigned_set = set(ordered)
    ordered += [p for p in team if p not in assigned_set]
    return ordered


def _snapshot_party(side: dict) -> list:
    """Return a list of {name, hp, maxhp, hp_pct, fainted} dicts for all Pokémon on a side."""
    result = []
    for p in side.get("pokemon", []):
        hp = p.get("hp", 0)
        maxhp = p.get("maxhp", 1) or 1
        name = p.get("name") or p.get("species", "?")
        result.append({
            "name": name,
            "hp": hp,
            "maxhp": maxhp,
            "hp_pct": hp / maxhp * 100,
            "fainted": hp <= 0,
        })
    return result


def _snapshot_team_info(side: dict) -> list:
    """Capture full team data from the initial IPC battle state.

    Called once at battle start to record level, ability, item, moves, and stats.
    """
    result = []
    for p in side.get("pokemon", []):
        name = p.get("name") or p.get("species", "?")
        level = p.get("set", {}).get("level", "?")
        ability_id = p.get("ability", "")
        ability = _ABILITY_NAME_MAP.get(ability_id, ability_id)
        item = p.get("item", "")
        moves = [slot.get("move", "") for slot in p.get("moveSlots", [])]
        maxhp = p.get("maxhp", 0)
        stats = p.get("storedStats", {})
        result.append({
            "name": name,
            "level": level,
            "ability": ability,
            "item": item,
            "moves": moves,
            "maxhp": maxhp,
            "atk": stats.get("atk", 0),
            "def": stats.get("def", 0),
            "spa": stats.get("spa", 0),
            "spd": stats.get("spd", 0),
            "spe": stats.get("spe", 0),
        })
    return result


def _resolve_action_name(action: str, state: dict, player: int = 1) -> str:
    """Resolve an action string to a human-readable display name.

    "move N"   → move display name (e.g., "Surf")
    "switch N" → "→ PokemonName"
    """
    battle = state.get("battle", {})
    sides = battle.get("sides", [])
    if len(sides) < player:
        return action
    side = sides[player - 1]
    parts = action.split()
    if len(parts) != 2:
        return action
    kind, n_str = parts
    try:
        n = int(n_str)
    except ValueError:
        return action
    if kind == "move":
        active = _get_active_pokemon(side)
        if active is None:
            return action
        move_slots = active.get("moveSlots", [])
        if 1 <= n <= len(move_slots):
            return move_slots[n - 1].get("move", action)
        return action
    if kind == "switch":
        pokemon_list = side.get("pokemon", [])
        if 1 <= n <= len(pokemon_list):
            p = pokemon_list[n - 1]
            name = p.get("name") or p.get("species", action)
            return f"→ {name}"
        return action
    return action


