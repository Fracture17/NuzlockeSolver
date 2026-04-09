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
from gen3_data import get_move_info
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
    }


# ---------------------------------------------------------------------------
# Battle context construction from IPC state
# ---------------------------------------------------------------------------

_PS_TYPE_MAP: dict = {
    "Normal":   PokeType.NORMAL,
    "Fire":     PokeType.FIRE,
    "Water":    PokeType.WATER,
    "Grass":    PokeType.GRASS,
    "Electric": PokeType.ELECTRIC,
    "Ice":      PokeType.ICE,
    "Fighting": PokeType.FIGHTING,
    "Poison":   PokeType.POISON,
    "Ground":   PokeType.GROUND,
    "Flying":   PokeType.FLYING,
    "Psychic":  PokeType.PSYCHIC,
    "Bug":      PokeType.BUG,
    "Rock":     PokeType.ROCK,
    "Ghost":    PokeType.GHOST,
    "Dragon":   PokeType.DRAGON,
    "Dark":     PokeType.DARK,
    "Steel":    PokeType.STEEL,
}

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


def _build_pokemon_state(
    pokemon: dict,
    side: dict,
    side_conditions: dict,
) -> PokemonState:
    """Convert an IPC pokemon dict + side conditions into a PokemonState."""
    hp = pokemon.get("hp", 1)
    maxhp = pokemon.get("maxhp", 1) or 1
    hp_pct = int(hp / maxhp * 100)

    types = [_PS_TYPE_MAP[t] for t in pokemon.get("types", ["Normal"])
             if t in _PS_TYPE_MAP]

    ability_id = pokemon.get("ability", "")
    ability = _ABILITY_NAME_MAP.get(ability_id, ability_id.title())

    status_raw = pokemon.get("status", "") or ""
    status = _PS_STATUS_MAP.get(status_raw) if status_raw else None

    move_slots = pokemon.get("moveSlots", [])
    moves = []
    for slot in move_slots:
        mi = get_move_info(slot.get("id", ""))
        if mi is not None:
            moves.append(mi)

    item = pokemon.get("item") or None
    last_item = pokemon.get("lastItem") or None

    gender_raw = pokemon.get("gender", "") or ""
    if gender_raw == "M":
        gender = "male"
    elif gender_raw == "F":
        gender = "female"
    else:
        gender = "genderless"

    level = pokemon.get("set", {}).get("level", 50) if pokemon.get("set") else 50
    speed = pokemon.get("speed", 100)

    is_first_turn = pokemon.get("activeTurns", 1) == 1

    boosts = pokemon.get("boosts", {})
    atk_s  = boosts.get("atk", 0)
    def_s  = boosts.get("def", 0)
    spa_s  = boosts.get("spa", 0)
    spd_s  = boosts.get("spd", 0)
    spe_s  = boosts.get("spe", 0)
    acc_s  = boosts.get("accuracy", 0)
    eva_s  = boosts.get("evasion", 0)

    volatiles = pokemon.get("volatiles", {}) or {}
    stockpile_entry = volatiles.get("stockpile")
    stockpile_count = stockpile_entry.get("level", 0) if isinstance(stockpile_entry, dict) else (1 if stockpile_entry else 0)

    # Side conditions (reflect/lightscreen/safeguard/mist on this pokemon's side)
    sc = side_conditions
    under_reflect      = "reflect" in sc
    under_light_screen = "lightscreen" in sc
    under_safeguard    = "safeguard" in sc
    under_mist         = "mist" in sc

    # Protect consecutive: check if 'stall' volatile is present
    protected_consecutive = 1 if ("stall" in volatiles or "protect" in volatiles) else 0

    has_other_pokemon = any(
        p.get("hp", 0) > 0 and not p.get("isActive")
        for p in side.get("pokemon", [])
    )

    last_move = _parse_last_move(pokemon)

    return PokemonState(
        hp_pct=hp_pct,
        types=types,
        ability=ability,
        status=status,
        moves=moves,
        held_item=item,
        used_item=last_item,
        level=level,
        gender=gender,
        speed=speed,
        is_first_turn=is_first_turn,
        atk_stage=atk_s,
        def_stage=def_s,
        spa_stage=spa_s,
        spd_stage=spd_s,
        spe_stage=spe_s,
        acc_stage=acc_s,
        eva_stage=eva_s,
        confused="confusion" in volatiles,
        infatuated="attract" in volatiles,
        cursed="curse" in volatiles,
        stockpile_count=stockpile_count,
        protected_consecutive=protected_consecutive,
        under_safeguard=under_safeguard,
        under_reflect=under_reflect,
        under_light_screen=under_light_screen,
        under_substitute="substitute" in volatiles,
        under_ingrain="ingrain" in volatiles,
        under_focus_energy="focusenergy" in volatiles,
        under_mist=under_mist,
        under_nightmare="nightmare" in volatiles,
        under_perish_song="perishsong" in volatiles,
        under_leech_seed="leechseed" in volatiles,
        under_disable="disable" in volatiles,
        under_encore="encore" in volatiles,
        under_torment="torment" in volatiles,
        under_foresight=("foresight" in volatiles or "odorsleuth" in volatiles),
        under_mean_look=("meanlook" in volatiles or "spiderweb" in volatiles),
        under_lock_on=("lockon" in volatiles or "mindreader" in volatiles),
        under_attract="attract" in volatiles,
        under_yawn="yawn" in volatiles,
        under_future_sight="futuresight" in volatiles,
        imprison_active="imprison" in volatiles,
        taunted="taunt" in volatiles,
        has_other_pokemon=has_other_pokemon,
        last_move_used=last_move,
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


def _pick_opponent_action(state: dict) -> Optional[str]:
    """Select the best action for the opponent (p2) using ai_flags or switch logic.

    Uses ai_flags scoring for moves, select_switch_in for switches.
    Returns None if neither moves nor switches are available.
    """
    if state["p2_moves"]:
        scored = select_move_with_ai_flags(state, state["p2_moves"], player=2)
        return scored if scored is not None else state["p2_moves"][0]
    if state["p2_switches"]:
        return select_switch_in(state, state["p2_switches"], player=2)
    return None


def can_player_switch(state: dict) -> bool:
    """Return True if the player is allowed to voluntarily switch this turn.

    Switching is permitted when either:
    - The opponent's active Pokémon just switched in (activeTurns == 1), or
    - The opponent can one-shot the player's active Pokémon (any p2_dmg_calcs
      value >= the player's current active HP).

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
        dmg_calcs = state.get("p2_dmg_calcs", {})
        if dmg_calcs and max(dmg_calcs.values(), default=0) >= p1_hp:
            return True

    return False


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


