"""Gen 3 Emerald AI switch-in logic.

Implements the two-section algorithm used when the AI's Pokémon faints:
  Section 1: pick the AI Pokémon with the highest type-score against the player
             that also has a super-effective move on the player.
  Section 2: pick the AI Pokémon whose best move deals the most damage,
             using the just-fainted Pokémon's typing for STAB.

Faithful to the Gen 3 Emerald source, including:
  - Integer-truncated score multiplication in both sections
  - NORMAL→GHOST and FIGHTING→GHOST treated as neutral (known AI bug)
  - Modulo-256 best-damage overflow in Section 2
"""

from __future__ import annotations

from typing import Optional

from ai_flags import PokeType, MoveCategory, MoveEffect, type_effectiveness
from gen3_data import get_move_info, _TYPE_MAP

T = PokeType

# ---------------------------------------------------------------------------
# Ordered type interaction table (from document)
# 108 active entries; NORMAL→GHOST and FIGHTING→GHOST appear at the end of the
# original list but are **skipped** due to the Gen 3 AI bug treating them as
# neutral — they are simply omitted here.
# ---------------------------------------------------------------------------

_ORDERED_INTERACTIONS: list[tuple[PokeType, PokeType, float]] = [
    (T.NORMAL,   T.ROCK,     0.5),
    (T.NORMAL,   T.STEEL,    0.5),
    (T.FIRE,     T.FIRE,     0.5),
    (T.FIRE,     T.WATER,    0.5),
    (T.FIRE,     T.GRASS,    2.0),
    (T.FIRE,     T.ICE,      2.0),
    (T.FIRE,     T.BUG,      2.0),
    (T.FIRE,     T.ROCK,     0.5),
    (T.FIRE,     T.DRAGON,   0.5),
    (T.FIRE,     T.STEEL,    2.0),
    (T.WATER,    T.FIRE,     2.0),
    (T.WATER,    T.WATER,    0.5),
    (T.WATER,    T.GRASS,    0.5),
    (T.WATER,    T.GROUND,   2.0),
    (T.WATER,    T.ROCK,     2.0),
    (T.WATER,    T.DRAGON,   0.5),
    (T.ELECTRIC, T.WATER,    2.0),
    (T.ELECTRIC, T.ELECTRIC, 0.5),
    (T.ELECTRIC, T.GRASS,    0.5),
    (T.ELECTRIC, T.GROUND,   0.0),
    (T.ELECTRIC, T.FLYING,   2.0),
    (T.ELECTRIC, T.DRAGON,   0.5),
    (T.GRASS,    T.FIRE,     0.5),
    (T.GRASS,    T.WATER,    2.0),
    (T.GRASS,    T.GRASS,    0.5),
    (T.GRASS,    T.POISON,   0.5),
    (T.GRASS,    T.GROUND,   2.0),
    (T.GRASS,    T.FLYING,   0.5),
    (T.GRASS,    T.BUG,      0.5),
    (T.GRASS,    T.ROCK,     2.0),
    (T.GRASS,    T.DRAGON,   0.5),
    (T.GRASS,    T.STEEL,    0.5),
    (T.ICE,      T.WATER,    0.5),
    (T.ICE,      T.GRASS,    2.0),
    (T.ICE,      T.ICE,      0.5),
    (T.ICE,      T.GROUND,   2.0),
    (T.ICE,      T.FLYING,   2.0),
    (T.ICE,      T.DRAGON,   2.0),
    (T.ICE,      T.STEEL,    0.5),
    (T.ICE,      T.FIRE,     0.5),
    (T.FIGHTING, T.NORMAL,   2.0),
    (T.FIGHTING, T.ICE,      2.0),
    (T.FIGHTING, T.POISON,   0.5),
    (T.FIGHTING, T.FLYING,   0.5),
    (T.FIGHTING, T.PSYCHIC,  0.5),
    (T.FIGHTING, T.BUG,      0.5),
    (T.FIGHTING, T.ROCK,     2.0),
    (T.FIGHTING, T.DARK,     2.0),
    (T.FIGHTING, T.STEEL,    2.0),
    (T.POISON,   T.GRASS,    2.0),
    (T.POISON,   T.POISON,   0.5),
    (T.POISON,   T.GROUND,   0.5),
    (T.POISON,   T.ROCK,     0.5),
    (T.POISON,   T.GHOST,    0.5),
    (T.POISON,   T.STEEL,    0.0),
    (T.GROUND,   T.FIRE,     2.0),
    (T.GROUND,   T.ELECTRIC, 2.0),
    (T.GROUND,   T.GRASS,    0.5),
    (T.GROUND,   T.POISON,   2.0),
    (T.GROUND,   T.FLYING,   0.0),
    (T.GROUND,   T.BUG,      0.5),
    (T.GROUND,   T.ROCK,     2.0),
    (T.GROUND,   T.STEEL,    2.0),
    (T.FLYING,   T.ELECTRIC, 0.5),
    (T.FLYING,   T.GRASS,    2.0),
    (T.FLYING,   T.FIGHTING, 2.0),
    (T.FLYING,   T.BUG,      2.0),
    (T.FLYING,   T.ROCK,     0.5),
    (T.FLYING,   T.STEEL,    0.5),
    (T.PSYCHIC,  T.FIGHTING, 2.0),
    (T.PSYCHIC,  T.POISON,   2.0),
    (T.PSYCHIC,  T.PSYCHIC,  0.5),
    (T.PSYCHIC,  T.DARK,     0.0),
    (T.PSYCHIC,  T.STEEL,    0.5),
    (T.BUG,      T.FIRE,     0.5),
    (T.BUG,      T.GRASS,    2.0),
    (T.BUG,      T.FIGHTING, 0.5),
    (T.BUG,      T.POISON,   0.5),
    (T.BUG,      T.FLYING,   0.5),
    (T.BUG,      T.PSYCHIC,  2.0),
    (T.BUG,      T.GHOST,    0.5),
    (T.BUG,      T.DARK,     2.0),
    (T.BUG,      T.STEEL,    0.5),
    (T.ROCK,     T.FIRE,     2.0),
    (T.ROCK,     T.ICE,      2.0),
    (T.ROCK,     T.FIGHTING, 0.5),
    (T.ROCK,     T.GROUND,   0.5),
    (T.ROCK,     T.FLYING,   2.0),
    (T.ROCK,     T.BUG,      2.0),
    (T.ROCK,     T.STEEL,    0.5),
    (T.GHOST,    T.NORMAL,   0.0),
    (T.GHOST,    T.PSYCHIC,  2.0),
    (T.GHOST,    T.DARK,     0.5),
    (T.GHOST,    T.STEEL,    0.5),
    (T.GHOST,    T.GHOST,    2.0),
    (T.DRAGON,   T.DRAGON,   2.0),
    (T.DRAGON,   T.STEEL,    0.5),
    (T.DARK,     T.FIGHTING, 0.5),
    (T.DARK,     T.PSYCHIC,  2.0),
    (T.DARK,     T.GHOST,    2.0),
    (T.DARK,     T.DARK,     0.5),
    (T.DARK,     T.STEEL,    0.5),
    (T.STEEL,    T.FIRE,     0.5),
    (T.STEEL,    T.WATER,    0.5),
    (T.STEEL,    T.ELECTRIC, 0.5),
    (T.STEEL,    T.ICE,      2.0),
    (T.STEEL,    T.ROCK,     2.0),
    (T.STEEL,    T.STEEL,    0.5),
    # NORMAL → GHOST (0.0) — SKIPPED (AI bug: treated as neutral)
    # FIGHTING → GHOST (0.0) — SKIPPED (AI bug: treated as neutral)
]

# ---------------------------------------------------------------------------
# Section 2 exclusion sets (non-standard damage)
# ---------------------------------------------------------------------------

_SECTION2_EXCLUDED_EFFECTS = frozenset({
    MoveEffect.OHKO,
    MoveEffect.FLAIL,        # Flail / Reversal
    MoveEffect.ERUPTION,     # Eruption / Water Spout
    MoveEffect.MAGNITUDE,
    MoveEffect.PRESENT,
    MoveEffect.COUNTER,
    MoveEffect.MIRROR_COAT,
    MoveEffect.SUPER_FANG,
    MoveEffect.SONICBOOM,
    MoveEffect.LEVEL_DAMAGE,  # Night Shade / Seismic Toss
    MoveEffect.PSYWAVE,
    MoveEffect.PAIN_SPLIT,
    MoveEffect.ENDEAVOR,
    MoveEffect.BIDE,
})

# Normalized (lowercase, no spaces) display names to exclude.
# Dragon Rage (fixed 40 damage) and Hidden Power (type-dependent) are excluded
# by name since they have no special MoveEffect in the database.
_SECTION2_EXCLUDED_NAMES = frozenset({
    'return', 'frustration', 'lowkick', 'dragonrage', 'hiddenpower',
})

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _active_poke(side: dict) -> Optional[dict]:
    """Return the active Pokémon dict from a side dict."""
    for p in side.get("pokemon", []):
        if p.get("isActive"):
            return p
    pokes = side.get("pokemon", [])
    return pokes[0] if pokes else None


def _poke_types(pokemon: dict) -> list[PokeType]:
    """Extract PokeType list from a Pokémon dict."""
    return [_TYPE_MAP[t] for t in pokemon.get("types", ["Normal"]) if t in _TYPE_MAP]


def _parse_move_id(raw) -> str:
    """Normalize a lastMove value (string or dict) to a bare lowercase move ID."""
    if isinstance(raw, dict):
        raw = raw.get("move", "")
    raw = raw or ""
    if raw.startswith("[Move:"):
        raw = raw[6:-1]
    return raw.lower().replace(" ", "")


# ---------------------------------------------------------------------------
# Section 1
# ---------------------------------------------------------------------------

def _section1_type_score(
    player_eff_types: list[PokeType],
    ai_types: list[PokeType],
) -> int:
    """Compute the Section 1 type score (player attacks AI candidate).

    Starts at 10 and applies integer-truncated multiplications from the ordered
    interaction list.  NORMAL→GHOST and FIGHTING→GHOST are already excluded from
    the list (AI bug).

    Args:
        player_eff_types: player's effective types (already expanded if single-typed).
        ai_types: AI candidate's types (no expansion).
    """
    score = 10
    for atk, df, mult in _ORDERED_INTERACTIONS:
        count = player_eff_types.count(atk)
        if count > 0 and df in ai_types:
            for _ in range(count):
                score = int(score * mult)
    return score


def _has_se_move(
    move_infos: list,
    player_types: list[PokeType],
    player_has_levitate: bool,
) -> bool:
    """Return True if any non-STATUS move in move_infos is super-effective on the player.

    Uses standard type_effectiveness (not the ordered chart).
    Ground-type moves are skipped if the player has Levitate.
    """
    for mi in move_infos:
        if mi.category == MoveCategory.STATUS:
            continue
        if player_has_levitate and mi.type == PokeType.GROUND:
            continue
        if type_effectiveness(mi.type, player_types) >= 2.0:
            return True
    return False


def _section1_select(
    state: dict, switch_actions: list, player: int
) -> Optional[str]:
    """Section 1: return the best switch action by type score + SE move, or None."""
    battle = state["battle"]
    sides = battle.get("sides", [])
    if len(sides) < 2:
        return None

    ai_side_idx  = player - 1   # e.g. player=2 → ai_side_idx=1
    opp_side_idx = 2 - player   # e.g. player=2 → opp_side_idx=0

    player_poke = _active_poke(sides[opp_side_idx])
    if player_poke is None:
        return None

    player_types = _poke_types(player_poke)
    player_has_levitate = player_poke.get("ability", "") == "levitate"

    # Single-typed player Pokémon: internally stored as dual with the same type twice.
    player_eff_types = player_types if len(player_types) == 2 else player_types * 2

    ai_pokemon_list = sides[ai_side_idx].get("pokemon", [])
    candidates = []

    for action in switch_actions:
        parts = action.split()
        if len(parts) != 2 or parts[0] != "switch":
            continue
        slot_idx = int(parts[1]) - 1
        if slot_idx < 0 or slot_idx >= len(ai_pokemon_list):
            continue
        candidate = ai_pokemon_list[slot_idx]
        if candidate.get("hp", 0) <= 0:
            continue

        ai_types = _poke_types(candidate)
        score = _section1_type_score(player_eff_types, ai_types)

        move_infos = [
            get_move_info(s.get("id", ""))
            for s in candidate.get("moveSlots", [])
        ]
        move_infos = [mi for mi in move_infos if mi is not None]
        candidates.append((score, action, move_infos))

    # Sort descending; Python's stable sort preserves party order on ties.
    candidates.sort(key=lambda x: x[0], reverse=True)

    for score, action, move_infos in candidates:
        if score <= 0:
            break
        if _has_se_move(move_infos, player_types, player_has_levitate):
            return action

    return None


# ---------------------------------------------------------------------------
# Section 2
# ---------------------------------------------------------------------------

def _section2_move_damage(
    move_info,
    base_dmg: int,
    fainted_types: list[PokeType],
    player_types: list[PokeType],
) -> int:
    """Compute Section 2 effective damage for one candidate move.

    Uses the ordered interaction chart with the candidate as attacker and the
    player as defender.  Levitate does NOT grant immunity here (AI limitation).
    Returns the real (non-modulo) damage value for comparison.

    Args:
        move_info: MoveInfo of the candidate's move.
        base_dmg: base damage from fainted Pokémon's last move.
        fainted_types: types of the just-fainted AI Pokémon (for STAB).
        player_types: types of the player's active Pokémon (for effectiveness).
    """
    dmg = 3 if move_info.category == MoveCategory.STATUS else base_dmg

    # STAB: uses the fainted AI Pokémon's types (not the candidate's)
    if move_info.type in fainted_types:
        dmg = int(dmg * 3 / 2)

    # Type effectiveness via ordered chart (candidate attacks player)
    for atk, df, mult in _ORDERED_INTERACTIONS:
        if atk == move_info.type and df in player_types:
            dmg = int(dmg * mult)

    return dmg


def _section2_select(
    state: dict, switch_actions: list, player: int
) -> Optional[str]:
    """Section 2: pick the switch whose best move deals most damage, or None.

    Returns None when the player is immune to every valid move across all
    switch candidates (per-document fallback to party order).
    """
    battle = state["battle"]
    sides = battle.get("sides", [])
    if len(sides) < 2:
        return None

    ai_side_idx  = player - 1
    opp_side_idx = 2 - player

    ai_pokemon_list = sides[ai_side_idx].get("pokemon", [])

    # Find the fainted AI Pokémon with the most active turns
    fainted = [p for p in ai_pokemon_list if p.get("hp", 0) <= 0]
    if not fainted:
        return None
    fainted_poke = max(fainted, key=lambda p: p.get("activeTurns", 0))
    fainted_types = _poke_types(fainted_poke)

    # Base damage from the fainted Pokémon's last move (fallback: 100)
    last_raw = fainted_poke.get("lastMove") or fainted_poke.get("lastMoveUsed") or ""
    last_id  = _parse_move_id(last_raw)
    fainted_last = get_move_info(last_id) if last_id else None
    base_dmg = (fainted_last.power if fainted_last and fainted_last.power else 100)

    # Player's active Pokémon types
    player_poke = _active_poke(sides[opp_side_idx])
    if player_poke is None:
        return None
    player_types = _poke_types(player_poke)

    # Walk candidates in switch_actions order (party order)
    best_stored = 0   # modulo-256 stored best (the AI overflow bug)
    best_action = None
    any_nonzero = False

    for action in switch_actions:
        parts = action.split()
        if len(parts) != 2 or parts[0] != "switch":
            continue
        slot_idx = int(parts[1]) - 1
        if slot_idx < 0 or slot_idx >= len(ai_pokemon_list):
            continue
        candidate = ai_pokemon_list[slot_idx]
        if candidate.get("hp", 0) <= 0:
            continue

        for slot in candidate.get("moveSlots", []):
            mi = get_move_info(slot.get("id", ""))
            if mi is None:
                continue
            if mi.effect in _SECTION2_EXCLUDED_EFFECTS:
                continue
            name_key = mi.name.lower().replace(" ", "")
            if name_key in _SECTION2_EXCLUDED_NAMES:
                continue

            real_dmg = _section2_move_damage(mi, base_dmg, fainted_types, player_types)

            if real_dmg > 0:
                any_nonzero = True

            # Compare against the stored (possibly wrapped) best value
            if real_dmg > best_stored:
                best_stored = real_dmg % 256   # overflow bug: store modulo 256
                best_action = action

    # If every valid move is immune, fall through to party order
    if not any_nonzero:
        return None
    return best_action


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_switch_in(state: dict, switch_actions: list, player: int = 2) -> str:
    """Select the AI switch-in action using the Gen 3 Emerald two-section algorithm.

    Section 1 — type matchup: pick the AI Pokémon with the best defensive typing
    against the player that also has a super-effective move on the player.

    Section 2 — highest damage: pick the AI Pokémon whose best move deals the
    most calculated damage, using STAB from the just-fainted Pokémon's types.

    Fallback: first available switch action.

    Args:
        state: dict from parse_ipc_response.
        switch_actions: list of "switch N" action strings.
        player: which side the AI is on (1 or 2).

    Returns:
        The selected "switch N" action string, or None if switch_actions is empty.
    """
    if not switch_actions:
        return None

    result = _section1_select(state, switch_actions, player)
    if result is not None:
        return result

    result = _section2_select(state, switch_actions, player)
    if result is not None:
        return result

    return switch_actions[0]
