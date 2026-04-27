"""
Gen 3 move data lookup table.

Loads gen3_moves.json (extracted from @pkmn/sim via extract_gen3_moves.js)
and converts each move into a MoveInfo for use with ai_flags.score_move().
"""

from __future__ import annotations

import json
import os
from typing import Optional

from ai_flags import MoveInfo, MoveCategory, MoveEffect, PokeType

_JSON_PATH = os.path.join(os.path.dirname(__file__), "gen3_moves.json")

# ---------------------------------------------------------------------------
# String → enum converters
# ---------------------------------------------------------------------------

TYPE_MAP: dict[str, PokeType] = {
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

_CAT_MAP: dict[str, MoveCategory] = {
    "Physical": MoveCategory.PHYSICAL,
    "Special":  MoveCategory.SPECIAL,
    "Status":   MoveCategory.STATUS,
}

# ---------------------------------------------------------------------------
# MoveEffect: hardcoded by move ID (highest priority)
# ---------------------------------------------------------------------------

_NAME_EFFECTS: dict[str, MoveEffect] = {
    # --- Counter / mirror ---
    "counter":      MoveEffect.COUNTER,
    "mirrorcoat":   MoveEffect.MIRROR_COAT,

    # --- Variable / unpredictable damage ---
    "superfang":    MoveEffect.SUPER_FANG,
    "sonicboom":    MoveEffect.SONICBOOM,
    "psywave":      MoveEffect.PSYWAVE,
    "nightshade":   MoveEffect.LEVEL_DAMAGE,
    "seismictoss":  MoveEffect.LEVEL_DAMAGE,
    "painsplit":    MoveEffect.PAIN_SPLIT,
    "present":      MoveEffect.PRESENT,
    "magnitude":    MoveEffect.MAGNITUDE,
    "bide":         MoveEffect.BIDE,

    # --- HP-based damage ---
    "flail":        MoveEffect.FLAIL,
    "reversal":     MoveEffect.FLAIL,
    "eruption":     MoveEffect.ERUPTION,
    "waterspout":   MoveEffect.ERUPTION,

    # --- Self-debuff after hitting ---
    "overheat":     MoveEffect.OVERHEAT,
    "superpower":   MoveEffect.SUPERPOWER,

    # --- Two-turn charged ---
    "skullbash":    MoveEffect.SKULL_BASH,
    "razorwind":    MoveEffect.RAZOR_WIND,
    "skyattack":    MoveEffect.SKY_ATTACK,
    "solarbeam":    MoveEffect.SOLAR_BEAM,
    "fly":          MoveEffect.FLY,
    "dig":          MoveEffect.DIG,
    "dive":         MoveEffect.DIVE,
    "bounce":       MoveEffect.BOUNCE,

    # --- Dream Eater (draining + requires sleep) ---
    "dreameater":   MoveEffect.DREAM_EATER,

    # --- Focus Punch ---
    "focuspunch":   MoveEffect.FOCUS_PUNCH,

    # --- Endeavor ---
    "endeavor":     MoveEffect.ENDEAVOR,

    # --- Omniboosting secondary (10% all stats) ---
    "ancientpower": MoveEffect.OMNIBOOSTING,
    "silverwind":   MoveEffect.OMNIBOOSTING,
    "ominouswind":  MoveEffect.OMNIBOOSTING,

    # --- Recharge after use ---
    "hyperbeam":    MoveEffect.RECHARGE,
    "blastburn":    MoveEffect.RECHARGE,
    "frenzyplant":  MoveEffect.RECHARGE,
    "hydrocannon":  MoveEffect.RECHARGE,

    # --- Status: sleep (primary) ---
    "spore":        MoveEffect.SLEEP,
    "sleeppowder":  MoveEffect.SLEEP,
    "hypnosis":     MoveEffect.SLEEP,
    "sing":         MoveEffect.SLEEP,
    "lovelykiss":   MoveEffect.SLEEP,
    "grasswhistle": MoveEffect.SLEEP,

    # --- Status: paralyze (primary) ---
    "thunderwave":  MoveEffect.PARALYZE,
    "stunspore":    MoveEffect.PARALYZE,
    "glare":        MoveEffect.PARALYZE,

    # --- Status: poison (primary) ---
    "poisongas":    MoveEffect.POISON,
    "poisonpowder": MoveEffect.POISON,

    # --- Status: toxic (primary) ---
    "toxic":        MoveEffect.TOXIC,

    # --- Status: burn (primary) ---
    "willowisp":    MoveEffect.WILL_O_WISP,

    # --- Confusion (primary) ---
    "confuseray":   MoveEffect.CONFUSE,
    "supersonic":   MoveEffect.CONFUSE,

    # --- Confusion + stat modifier ---
    "swagger":      MoveEffect.SWAGGER,
    "flatter":      MoveEffect.FLATTER,
    "teeterdance":  MoveEffect.TEETER_DANCE,

    # --- Double-stage single-stat boosts ---
    "swordsdance":  MoveEffect.ATK_UP_2,
    "agility":      MoveEffect.SPE_UP_2,
    "barrier":      MoveEffect.DEF_UP_2,
    "irondefense":  MoveEffect.DEF_UP_2,
    "amnesia":      MoveEffect.SPD_UP_2,
    "tailglow":     MoveEffect.SPA_UP_2,
    "doubleteam":   MoveEffect.EVA_UP_2,

    # --- Standalone volatile/type effects ---
    "minimize":     MoveEffect.MINIMIZE,
    "defensecurl":  MoveEffect.DEFENSE_CURL,
    "camouflage":   MoveEffect.CAMOUFLAGE,

    # --- Multi-stat raise ---
    "bulkup":       MoveEffect.BULK_UP,
    "calmmind":     MoveEffect.CALM_MIND,
    "cosmicpower":  MoveEffect.COSMIC_POWER,
    "dragondance":  MoveEffect.DRAGON_DANCE,
    "curse":        MoveEffect.CURSE,

    # --- HP restore ---
    "rest":         MoveEffect.REST,
    "moonlight":    MoveEffect.WEATHER_HEAL,
    "morningsun":   MoveEffect.WEATHER_HEAL,
    "synthesis":    MoveEffect.WEATHER_HEAL,
    "swallow":      MoveEffect.SWALLOW,

    # --- Roar / force switch ---
    "roar":         MoveEffect.ROAR,
    "whirlwind":    MoveEffect.ROAR,

    # --- Baton Pass ---
    "batonpass":    MoveEffect.BATON_PASS,

    # --- Misc battle effects ---
    "haze":         MoveEffect.HAZE,
    "psychup":      MoveEffect.PSYCH_UP,
    "substitute":   MoveEffect.SUBSTITUTE,
    "leechseed":    MoveEffect.LEECH_SEED,
    "futuresight":  MoveEffect.FUTURE_SIGHT,
    "doomdesire":   MoveEffect.FUTURE_SIGHT,
    "focusenergy":  MoveEffect.FOCUS_ENERGY,
    "attract":      MoveEffect.ATTRACT,
    "nightmare":    MoveEffect.NIGHTMARE,
    "perishsong":   MoveEffect.PERISH_SONG,
    "torment":      MoveEffect.TORMENT,
    "encore":       MoveEffect.ENCORE,
    "disable":      MoveEffect.DISABLE,
    "foresight":    MoveEffect.FORESIGHT,
    "odorsleuth":   MoveEffect.FORESIGHT,
    "ingrain":      MoveEffect.INGRAIN,
    "imprison":     MoveEffect.IMPRISON,
    "refresh":      MoveEffect.REFRESH,
    "recycle":      MoveEffect.RECYCLE,
    "mudsport":     MoveEffect.MUD_SPORT,
    "watersport":   MoveEffect.WATER_SPORT,
    "helpinghand":  MoveEffect.HELPING_HAND,
    "magiccoat":    MoveEffect.MAGIC_COAT,
    "snatch":       MoveEffect.SNATCH,
    "yawn":         MoveEffect.YAWN,
    "meanlook":     MoveEffect.MEAN_LOOK,
    "spiderweb":    MoveEffect.MEAN_LOOK,
    "lockon":       MoveEffect.LOCK_ON,
    "mindreader":   MoveEffect.LOCK_ON,
    "destinybond":  MoveEffect.DESTINY_BOND,
    "trick":        MoveEffect.TRICK,
    "knockoff":     MoveEffect.KNOCK_OFF,
    "thief":        MoveEffect.THIEF,
    "skillswap":    MoveEffect.SKILL_SWAP,
    "roleplay":     MoveEffect.ROLE_PLAY,
    "mirrormove":   MoveEffect.MIRROR_MOVE,
    "pursuit":      MoveEffect.PURSUIT,
    "healbell":     MoveEffect.HEAL_BELL,
    "aromatherapy": MoveEffect.HEAL_BELL,
    "fakeout":      MoveEffect.FAKE_OUT,
    "stockpile":    MoveEffect.STOCKPILE,
    "spitup":       MoveEffect.SPIT_UP,
    "bellydrum":    MoveEffect.BELLY_DRUM,
    "endure":       MoveEffect.ENDURE,
    "protect":      MoveEffect.PROTECT,
    "detect":       MoveEffect.PROTECT,
    "conversion":   MoveEffect.CONVERSION,
    "conversion2":  MoveEffect.CONVERSION,
    "teleport":     MoveEffect.TELEPORT,
    "snore":        MoveEffect.SNORE,
    "sleeptalk":    MoveEffect.SLEEP_TALK,
    "facade":       MoveEffect.FACADE,
    "smellingsalt": MoveEffect.SMELLING_SALT,
    "vitalthrow":   MoveEffect.VITAL_THROW,
    "revenge":      MoveEffect.REVENGE,
    "brickbreak":   MoveEffect.BRICK_BREAK,
    "metronome":    MoveEffect.METRONOME,
    # Never-miss damaging moves
    "aerialace":    MoveEffect.ALWAYS_HIT,
    "shockwave":    MoveEffect.ALWAYS_HIT,
    "swift":        MoveEffect.ALWAYS_HIT,
    "shadowpunch":  MoveEffect.ALWAYS_HIT,
}

# ---------------------------------------------------------------------------
# Single-stat boost/drop → MoveEffect
# ---------------------------------------------------------------------------

_SINGLE_RAISE_MAP: dict[str, MoveEffect] = {
    "atk":      MoveEffect.ATK_UP,
    "def":      MoveEffect.DEF_UP,
    "spa":      MoveEffect.SPA_UP,
    "spd":      MoveEffect.SPD_UP,
    "spe":      MoveEffect.SPE_UP,
    "accuracy": MoveEffect.ACC_UP,
    "evasion":  MoveEffect.EVA_UP,
}

_DOUBLE_RAISE_MAP: dict[str, MoveEffect] = {
    "atk":      MoveEffect.ATK_UP_2,
    "def":      MoveEffect.DEF_UP_2,
    "spa":      MoveEffect.SPA_UP_2,
    "spd":      MoveEffect.SPD_UP_2,
    "spe":      MoveEffect.SPE_UP_2,
    "accuracy": MoveEffect.ACC_UP_2,
    "evasion":  MoveEffect.EVA_UP_2,
}

_SINGLE_LOWER_MAP: dict[str, MoveEffect] = {
    "atk":      MoveEffect.ATK_DOWN,
    "def":      MoveEffect.DEF_DOWN,
    "spa":      MoveEffect.SPA_DOWN,
    "spd":      MoveEffect.SPD_DOWN,
    "spe":      MoveEffect.SPE_DOWN,
    "accuracy": MoveEffect.ACC_DOWN,
    "evasion":  MoveEffect.EVA_DOWN,
}

_DOUBLE_LOWER_MAP: dict[str, MoveEffect] = {
    "atk":      MoveEffect.ATK_DOWN_2,
    "def":      MoveEffect.DEF_DOWN_2,
    "spa":      MoveEffect.SPA_DOWN_2,
    "spd":      MoveEffect.SPD_DOWN_2,
    "spe":      MoveEffect.SPE_DOWN_2,
    "accuracy": MoveEffect.ACC_DOWN_2,
    "evasion":  MoveEffect.EVA_DOWN_2,
}

# ---------------------------------------------------------------------------
# Field-based MoveEffect determination
# ---------------------------------------------------------------------------

def _effect_from_fields(mid: str, d: dict) -> MoveEffect:
    """Derive MoveEffect from PS move fields when the name table has no entry.

    Priority order: OHKO → selfdestruct → forceSwitch → selfSwitch → recharge
    → weather → sideCondition → status → heal → volatileStatus → boosts → NONE.
    """
    # OHKO moves (Fissure, Guillotine, etc.)
    if d.get("ohko"):
        return MoveEffect.OHKO

    # Selfdestruct / Memento — distinguish "always" explode from "ifHit" stat-drop
    sd = d.get("selfdestruct")
    if sd == "always":
        return MoveEffect.EXPLODE
    if sd == "ifHit":
        return MoveEffect.MEMENTO

    # Force switch (roar/whirlwind already in name table, just in case)
    if d.get("forceSwitch"):
        return MoveEffect.ROAR

    # Baton Pass (selfSwitch='copyvolatile')
    if d.get("selfSwitch") == "copyvolatile":
        return MoveEffect.BATON_PASS

    # Recharge after use
    if "recharge" in d.get("flags", {}):
        return MoveEffect.RECHARGE

    # Weather-setting moves (Rain Dance, Sunny Day, Sandstorm, Hail)
    weather = d.get("weather")
    if weather:
        wmap = {
            "RainDance": MoveEffect.RAIN_DANCE,
            "sunnyday":  MoveEffect.SUNNY_DAY,
            "Sandstorm": MoveEffect.SANDSTORM,
            "hail":      MoveEffect.HAIL,
        }
        if weather in wmap:
            return wmap[weather]

    # Side conditions (Reflect, Light Screen, Safeguard, Mist, Spikes)
    sc = d.get("sideCondition")
    if sc:
        scmap = {
            "reflect":     MoveEffect.REFLECT,
            "lightscreen": MoveEffect.LIGHT_SCREEN,
            "safeguard":   MoveEffect.SAFEGUARD,
            "mist":        MoveEffect.MIST,
            "spikes":      MoveEffect.SPIKES,
        }
        if sc in scmap:
            return scmap[sc]

    # Primary status condition (Status-category moves)
    status = d.get("status")
    if status and d.get("category") == "Status":
        smap = {
            "slp": MoveEffect.SLEEP,
            "par": MoveEffect.PARALYZE,
            "psn": MoveEffect.POISON,
            "tox": MoveEffect.TOXIC,
            "brn": MoveEffect.WILL_O_WISP,
        }
        if status in smap:
            return smap[status]

    # HP-restore (heal flag, Status category)
    if "heal" in d.get("flags", {}) and d.get("category") == "Status":
        heal = d.get("heal")
        if heal:  # [1, 2] = 50% heal
            return MoveEffect.RECOVER
        # Else: heal flag without heal field = weather-dependent heal
        return MoveEffect.WEATHER_HEAL

    # Volatile status conditions (confusion, substitute, ingrain, etc.)
    vs = d.get("volatileStatus")
    if vs:
        vsmap = {
            "confusion":       MoveEffect.CONFUSE,
            "focusenergy":     MoveEffect.FOCUS_ENERGY,
            "substitute":      MoveEffect.SUBSTITUTE,
            "ingrain":         MoveEffect.INGRAIN,
            "imprison":        MoveEffect.IMPRISON,
            "encore":          MoveEffect.ENCORE,
            "torment":         MoveEffect.TORMENT,
            "disable":         MoveEffect.DISABLE,
            "foresight":       MoveEffect.FORESIGHT,
            "leechseed":       MoveEffect.LEECH_SEED,
            "nightmare":       MoveEffect.NIGHTMARE,
            "attract":         MoveEffect.ATTRACT,
            "yawn":            MoveEffect.YAWN,
            "destinybond":     MoveEffect.DESTINY_BOND,
            "helpinghand":     MoveEffect.HELPING_HAND,
            "magiccoat":       MoveEffect.MAGIC_COAT,
            "snatch":          MoveEffect.SNATCH,
            "endure":          MoveEffect.ENDURE,
            "protect":         MoveEffect.PROTECT,
            "stockpile":       MoveEffect.STOCKPILE,
            "mudsport":        MoveEffect.MUD_SPORT,
            "watersport":      MoveEffect.WATER_SPORT,
            "bide":            MoveEffect.BIDE,
            "curse":           MoveEffect.CURSE,
        }
        if vs in vsmap:
            return vsmap[vs]

    # Stat-boost/drop Status moves (single or multi-stat changes)
    boosts = d.get("boosts")
    if boosts and d.get("category") == "Status":
        boost_keys = set(boosts.keys())
        boost_vals = list(boosts.values())
        all_positive = all(v > 0 for v in boost_vals)
        all_negative = all(v < 0 for v in boost_vals)

        # Multi-stat raises
        if all_positive and boost_keys == {"atk", "def"}:
            return MoveEffect.BULK_UP
        if all_positive and boost_keys == {"spa", "spd"}:
            return MoveEffect.CALM_MIND
        if all_positive and boost_keys == {"def", "spd"}:
            return MoveEffect.COSMIC_POWER
        if all_positive and boost_keys == {"atk", "spe"}:
            return MoveEffect.DRAGON_DANCE

        # Multi-stat drops
        if all_negative and boost_keys == {"atk", "def"}:
            return MoveEffect.TICKLE
        if all_negative and boost_keys == {"atk", "spa"}:
            return MoveEffect.MEMENTO  # memento is handled by name, but just in case

        # Single-stat changes — distinguish ±1 (single stage) from ±2 (double stage)
        if len(boost_keys) == 1:
            stat = next(iter(boost_keys))
            val = boosts[stat]
            if val >= 2:
                return _DOUBLE_RAISE_MAP.get(stat, MoveEffect.NONE)
            elif val == 1:
                return _SINGLE_RAISE_MAP.get(stat, MoveEffect.NONE)
            elif val == -1:
                return _SINGLE_LOWER_MAP.get(stat, MoveEffect.NONE)
            else:  # val <= -2
                return _DOUBLE_LOWER_MAP.get(stat, MoveEffect.NONE)

    return MoveEffect.NONE


# ---------------------------------------------------------------------------
# Build MoveInfo from PS move data dict
# ---------------------------------------------------------------------------

def _build_move_info(mid: str, d: dict) -> MoveInfo:
    poke_type = TYPE_MAP.get(d.get("type", "Normal"), PokeType.NORMAL)
    category = _CAT_MAP.get(d.get("category", "Physical"), MoveCategory.PHYSICAL)
    power = d.get("basePower", 0)
    priority = d.get("priority", 0)
    flags = d.get("flags", {})

    # MoveEffect: name table takes precedence, then field-based
    effect = _NAME_EFFECTS.get(mid) or _effect_from_fields(mid, d)

    is_sound = "sound" in flags
    is_high_crit = d.get("critRatio", 1) >= 2
    is_draining = d.get("drain") is not None
    # Trapping: partiallytrapped volatile status
    is_trapping = d.get("volatileStatus") == "partiallytrapped"

    return MoveInfo(
        name=d.get("name", mid),
        type=poke_type,
        category=category,
        power=power,
        effect=effect,
        priority=priority,
        is_sound=is_sound,
        is_high_crit=is_high_crit,
        is_draining=is_draining,
        is_trapping=is_trapping,
    )


# ---------------------------------------------------------------------------
# Module-level lookup table (loaded once at import)
# ---------------------------------------------------------------------------

_MOVE_DB: dict[str, MoveInfo] = {}


def _load() -> None:
    with open(_JSON_PATH) as f:
        raw = json.load(f)
    for mid, d in raw.items():
        _MOVE_DB[mid] = _build_move_info(mid, d)


_load()


def get_move_info(move_id: str) -> Optional[MoveInfo]:
    """Return MoveInfo for the given Pokemon Showdown move ID (lowercase, no spaces).

    Returns None if the move is not found in the Gen 3 database.
    """
    return _MOVE_DB.get(move_id)


def get_move_info_by_name(move_name: str) -> Optional[MoveInfo]:
    """Look up MoveInfo by display name (e.g. 'Ice Beam').

    Converts to lowercase without spaces and delegates to get_move_info().
    """
    move_id = move_name.lower().replace(" ", "").replace("-", "")
    return get_move_info(move_id)
