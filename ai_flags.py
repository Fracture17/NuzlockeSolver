"""
Pokemon Emerald trainer AI flag logic (flags 0, 1, 2, 3, 4, 7).

Each flag function takes a BattleContext and an rng callable, and returns
an integer score delta. Use score_move() to run multiple flags.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PokeType(Enum):
    NORMAL   = auto()
    FIRE     = auto()
    WATER    = auto()
    GRASS    = auto()
    ELECTRIC = auto()
    ICE      = auto()
    FIGHTING = auto()
    POISON   = auto()
    GROUND   = auto()
    FLYING   = auto()
    PSYCHIC  = auto()
    BUG      = auto()
    ROCK     = auto()
    GHOST    = auto()
    DRAGON   = auto()
    DARK     = auto()
    STEEL    = auto()


class Weather(Enum):
    NONE = auto()
    RAIN = auto()
    SUN  = auto()
    SAND = auto()
    HAIL = auto()


class MoveCategory(Enum):
    PHYSICAL = auto()
    SPECIAL  = auto()
    STATUS   = auto()


class MoveEffect(Enum):
    # No special effect (regular damaging)
    NONE = auto()

    # Status conditions
    SLEEP        = auto()
    PARALYZE     = auto()
    POISON       = auto()
    TOXIC        = auto()
    WILL_O_WISP  = auto()

    # Confusion
    CONFUSE      = auto()
    SWAGGER      = auto()
    FLATTER      = auto()
    TEETER_DANCE = auto()

    # Single-stat boosts (move's sole effect)
    ATK_UP  = auto()
    DEF_UP  = auto()
    SPA_UP  = auto()
    SPD_UP  = auto()
    SPE_UP  = auto()
    ACC_UP  = auto()
    EVA_UP  = auto()

    # Multi-stat boosts
    BULK_UP      = auto()
    CALM_MIND    = auto()
    COSMIC_POWER = auto()
    DRAGON_DANCE = auto()
    CURSE        = auto()

    # Single-stat drops
    ATK_DOWN  = auto()
    DEF_DOWN  = auto()
    SPA_DOWN  = auto()
    SPD_DOWN  = auto()
    SPE_DOWN  = auto()
    ACC_DOWN  = auto()
    EVA_DOWN  = auto()

    # Multi-stat drops
    TICKLE = auto()

    # Screens / field
    REFLECT      = auto()
    LIGHT_SCREEN = auto()
    SAFEGUARD    = auto()
    MIST         = auto()
    SPIKES       = auto()

    # Weather
    RAIN_DANCE = auto()
    SUNNY_DAY  = auto()
    SANDSTORM  = auto()
    HAIL       = auto()

    # HP restore
    RECOVER      = auto()
    SOFTBOILED   = auto()
    REST         = auto()
    WEATHER_HEAL = auto()
    SWALLOW      = auto()

    # Damaging specials
    DREAM_EATER  = auto()
    BIDE         = auto()
    COUNTER      = auto()
    MIRROR_COAT  = auto()
    SUPER_FANG   = auto()
    OHKO         = auto()
    EXPLODE      = auto()
    MEMENTO      = auto()
    FLAIL        = auto()
    ERUPTION     = auto()
    SUPERPOWER   = auto()
    OVERHEAT     = auto()
    FOCUS_PUNCH  = auto()
    ENDEAVOR     = auto()
    PRESENT      = auto()
    MAGNITUDE    = auto()
    PSYWAVE      = auto()
    SONICBOOM    = auto()
    LEVEL_DAMAGE = auto()
    PAIN_SPLIT   = auto()

    # Two-turn / semi-invulnerable
    SKULL_BASH  = auto()
    RAZOR_WIND  = auto()
    SKY_ATTACK  = auto()
    SOLAR_BEAM  = auto()
    FLY         = auto()
    DIG         = auto()
    DIVE        = auto()
    BOUNCE      = auto()

    # Recharge
    RECHARGE = auto()

    # Misc battle effects
    ROAR          = auto()
    HAZE          = auto()
    PSYCH_UP      = auto()
    SUBSTITUTE    = auto()
    BATON_PASS    = auto()
    LEECH_SEED    = auto()
    FUTURE_SIGHT  = auto()
    FOCUS_ENERGY  = auto()
    ATTRACT       = auto()
    NIGHTMARE     = auto()
    PERISH_SONG   = auto()
    TORMENT       = auto()
    ENCORE        = auto()
    DISABLE       = auto()
    FORESIGHT     = auto()
    INGRAIN       = auto()
    IMPRISON      = auto()
    REFRESH       = auto()
    RECYCLE       = auto()
    MUD_SPORT     = auto()
    WATER_SPORT   = auto()
    HELPING_HAND  = auto()
    MAGIC_COAT    = auto()
    SNATCH        = auto()
    YAWN          = auto()
    MEAN_LOOK     = auto()
    LOCK_ON       = auto()
    DESTINY_BOND  = auto()
    TRICK         = auto()
    KNOCK_OFF     = auto()
    THIEF         = auto()
    SKILL_SWAP    = auto()
    ROLE_PLAY     = auto()
    MIRROR_MOVE   = auto()
    PURSUIT       = auto()
    HEAL_BELL     = auto()
    FAKE_OUT      = auto()
    STOCKPILE     = auto()
    SPIT_UP       = auto()
    BELLY_DRUM    = auto()
    ENDURE        = auto()
    PROTECT       = auto()
    CONVERSION    = auto()
    TELEPORT      = auto()
    SNORE         = auto()
    SLEEP_TALK    = auto()
    FACADE        = auto()
    SMELLING_SALT = auto()
    VITAL_THROW   = auto()
    REVENGE       = auto()
    BRICK_BREAK   = auto()
    OMNIBOOSTING  = auto()
    METRONOME     = auto()
    ALWAYS_HIT    = auto()


# ---------------------------------------------------------------------------
# Effect sets
# ---------------------------------------------------------------------------

# "Power-1 class": moves whose damage output is too variable / unpredictable
POWER1_EFFECTS: frozenset = frozenset({
    MoveEffect.OHKO,
    MoveEffect.BIDE,
    MoveEffect.COUNTER,
    MoveEffect.MIRROR_COAT,
    MoveEffect.SUPER_FANG,
    MoveEffect.SONICBOOM,
    MoveEffect.LEVEL_DAMAGE,
    MoveEffect.PSYWAVE,
    MoveEffect.PAIN_SPLIT,
    MoveEffect.PRESENT,
    MoveEffect.MAGNITUDE,
})

# Moves the AI is "discouraged" from using (charged, recoil, self-debuffing, etc.)
DISCOURAGED_EFFECTS: frozenset = frozenset({
    MoveEffect.EXPLODE,
    MoveEffect.MEMENTO,
    MoveEffect.SKULL_BASH,
    MoveEffect.RAZOR_WIND,
    MoveEffect.SKY_ATTACK,
    MoveEffect.SOLAR_BEAM,
    MoveEffect.FLY,
    MoveEffect.DIG,
    MoveEffect.DIVE,
    MoveEffect.BOUNCE,
    MoveEffect.RECHARGE,
    MoveEffect.DREAM_EATER,
    MoveEffect.SPIT_UP,
    MoveEffect.FOCUS_PUNCH,
    MoveEffect.SUPERPOWER,
    MoveEffect.OVERHEAT,
    MoveEffect.FLAIL,
    MoveEffect.ERUPTION,
    MoveEffect.ENDEAVOR,
})

# Moves that set up on the first turn of battle (Flag 3)
SETUP_FIRST_TURN_EFFECTS: frozenset = frozenset({
    MoveEffect.ATK_UP,
    MoveEffect.DEF_UP,
    MoveEffect.SPA_UP,
    MoveEffect.SPD_UP,
    MoveEffect.SPE_UP,
    MoveEffect.ACC_UP,
    MoveEffect.EVA_UP,
    MoveEffect.BULK_UP,
    MoveEffect.CALM_MIND,
    MoveEffect.COSMIC_POWER,
    MoveEffect.DRAGON_DANCE,
    MoveEffect.CURSE,
    MoveEffect.SUBSTITUTE,
    MoveEffect.FOCUS_ENERGY,
    MoveEffect.INGRAIN,
    MoveEffect.BELLY_DRUM,
    MoveEffect.STOCKPILE,
})

# Risky moves (Flag 4)
RISKY_EFFECTS: frozenset = frozenset({
    MoveEffect.EXPLODE,
    MoveEffect.MEMENTO,
    MoveEffect.FLAIL,
    MoveEffect.ERUPTION,
    MoveEffect.ENDEAVOR,
    MoveEffect.FOCUS_PUNCH,
    MoveEffect.SUPERPOWER,
    MoveEffect.OVERHEAT,
    MoveEffect.COUNTER,
    MoveEffect.MIRROR_COAT,
    MoveEffect.DESTINY_BOND,
    MoveEffect.PERISH_SONG,
    MoveEffect.BELLY_DRUM,
    MoveEffect.PAIN_SPLIT,
    MoveEffect.SKULL_BASH,
    MoveEffect.RAZOR_WIND,
    MoveEffect.SKY_ATTACK,
    MoveEffect.SOLAR_BEAM,
    MoveEffect.FLY,
    MoveEffect.DIG,
    MoveEffect.DIVE,
    MoveEffect.BOUNCE,
    MoveEffect.BIDE,
})

# Stat-lowering move effects (single-stat)
_STAT_LOWER_EFFECTS: frozenset = frozenset({
    MoveEffect.ATK_DOWN,
    MoveEffect.DEF_DOWN,
    MoveEffect.SPA_DOWN,
    MoveEffect.SPD_DOWN,
    MoveEffect.SPE_DOWN,
    MoveEffect.ACC_DOWN,
    MoveEffect.EVA_DOWN,
    MoveEffect.TICKLE,
})

# Stat-raising move effects (single-stat, for Flag 0 / Flag 2)
_STAT_RAISE_EFFECTS: frozenset = frozenset({
    MoveEffect.ATK_UP,
    MoveEffect.DEF_UP,
    MoveEffect.SPA_UP,
    MoveEffect.SPD_UP,
    MoveEffect.SPE_UP,
    MoveEffect.ACC_UP,
    MoveEffect.EVA_UP,
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MoveInfo:
    name: str
    type: PokeType
    category: MoveCategory
    power: int              # 0 = status; base power otherwise
    effect: MoveEffect
    priority: int = 0
    is_sound: bool = False
    is_high_crit: bool = False
    is_draining: bool = False
    is_trapping: bool = False

    @property
    def is_power1(self) -> bool:
        return self.effect in POWER1_EFFECTS

    @property
    def is_discouraged(self) -> bool:
        return self.effect in DISCOURAGED_EFFECTS


@dataclass
class PokemonState:
    hp_pct: int                        # 0–100 (rounded down)
    types: list                        # list[PokeType]
    ability: str                       # e.g. "Levitate"
    status: Optional[str]              # None | "sleep" | "poison" | "badly_poison" | "burn" | "paralyze" | "freeze"
    moves: list                        # list[MoveInfo] — the pokemon's known moves
    held_item: Optional[str] = None
    used_item: Optional[str] = None    # item consumed (for Recycle)
    level: int = 50
    gender: str = "male"               # "male" | "female" | "genderless"
    speed: int = 100
    is_first_turn: bool = False
    # Stat stages (-6 to +6)
    atk_stage: int = 0
    def_stage: int = 0
    spa_stage: int = 0
    spd_stage: int = 0
    spe_stage: int = 0
    acc_stage: int = 0
    eva_stage: int = 0
    # Volatile / field conditions
    confused: bool = False
    infatuated: bool = False
    cursed: bool = False
    stockpile_count: int = 0
    protected_consecutive: int = 0
    under_safeguard: bool = False
    under_reflect: bool = False
    under_light_screen: bool = False
    under_substitute: bool = False
    under_ingrain: bool = False
    under_focus_energy: bool = False
    under_mist: bool = False
    under_nightmare: bool = False
    under_perish_song: bool = False
    under_leech_seed: bool = False
    under_disable: bool = False
    under_encore: bool = False
    under_torment: bool = False
    under_foresight: bool = False
    under_mean_look: bool = False
    under_lock_on: bool = False
    under_attract: bool = False
    under_yawn: bool = False
    under_future_sight: bool = False
    imprison_active: bool = False
    taunted: bool = False
    has_other_pokemon: bool = False    # party has benched pokemon
    last_move_used: Optional[MoveInfo] = None


@dataclass
class BattleContext:
    user: PokemonState
    target: PokemonState
    move: MoveInfo
    weather: Weather = Weather.NONE
    targeting_ally: bool = False
    is_double_battle: bool = False
    is_first_battle_turn: bool = False
    move_damage_pcts: dict = field(default_factory=dict)  # move name -> % of target HP dealt


# ---------------------------------------------------------------------------
# RNG
# ---------------------------------------------------------------------------

def make_rng():
    """Return a GBA-style RNG: callable(threshold) -> bool where threshold is 0-255."""
    return lambda threshold: random.randint(0, 255) < threshold


# ---------------------------------------------------------------------------
# Type chart (Gen 3)
# ---------------------------------------------------------------------------

# _TYPE_CHART[attacking][defending] = multiplier encoded as:
#   0 = immune (0×), 1 = not very effective (½×), 2 = normal (1×), 3 = super effective (2×)
# We use ints to avoid floats; convert with _MULT_TABLE when needed.
_MULT_TABLE = {0: 0.0, 1: 0.5, 2: 1.0, 3: 2.0}

T = PokeType
_RAW: dict = {
    T.NORMAL:   {T.ROCK: 1, T.GHOST: 0, T.STEEL: 1},
    T.FIRE:     {T.FIRE: 1, T.WATER: 1, T.GRASS: 3, T.ICE: 3, T.BUG: 3,
                 T.ROCK: 1, T.DRAGON: 1, T.STEEL: 3},
    T.WATER:    {T.FIRE: 3, T.WATER: 1, T.GRASS: 1, T.GROUND: 3, T.ROCK: 3, T.DRAGON: 1},
    T.GRASS:    {T.FIRE: 1, T.WATER: 3, T.GRASS: 1, T.POISON: 1, T.GROUND: 3,
                 T.FLYING: 1, T.BUG: 1, T.ROCK: 3, T.DRAGON: 1, T.STEEL: 1},
    T.ELECTRIC: {T.WATER: 3, T.GRASS: 1, T.ELECTRIC: 1, T.GROUND: 0,
                 T.FLYING: 3, T.DRAGON: 1},
    T.ICE:      {T.WATER: 1, T.GRASS: 3, T.ICE: 1, T.GROUND: 3,
                 T.FLYING: 3, T.DRAGON: 3, T.STEEL: 1},
    T.FIGHTING: {T.NORMAL: 3, T.ICE: 3, T.POISON: 1, T.FLYING: 1, T.PSYCHIC: 1,
                 T.BUG: 1, T.ROCK: 3, T.GHOST: 0, T.DARK: 3, T.STEEL: 3},
    T.POISON:   {T.GRASS: 3, T.POISON: 1, T.GROUND: 1, T.ROCK: 1,
                 T.GHOST: 1, T.STEEL: 0},
    T.GROUND:   {T.FIRE: 3, T.GRASS: 1, T.ELECTRIC: 3, T.POISON: 3, T.FLYING: 0,
                 T.BUG: 1, T.ROCK: 3, T.STEEL: 3},
    T.FLYING:   {T.GRASS: 3, T.ELECTRIC: 1, T.FIGHTING: 3, T.BUG: 3,
                 T.ROCK: 1, T.STEEL: 1},
    T.PSYCHIC:  {T.FIGHTING: 3, T.POISON: 3, T.PSYCHIC: 1, T.DARK: 0, T.STEEL: 1},
    T.BUG:      {T.FIRE: 1, T.GRASS: 3, T.FIGHTING: 1, T.FLYING: 1, T.PSYCHIC: 3,
                 T.GHOST: 1, T.DARK: 3, T.STEEL: 1},
    T.ROCK:     {T.FIRE: 3, T.ICE: 3, T.FIGHTING: 1, T.GROUND: 1, T.FLYING: 3,
                 T.BUG: 3, T.STEEL: 1},
    T.GHOST:    {T.NORMAL: 0, T.PSYCHIC: 3, T.GHOST: 3, T.DARK: 1},
    T.DRAGON:   {T.DRAGON: 3, T.STEEL: 1},
    T.DARK:     {T.FIGHTING: 1, T.PSYCHIC: 3, T.GHOST: 3, T.DARK: 1, T.STEEL: 1},
    T.STEEL:    {T.FIRE: 1, T.WATER: 1, T.ELECTRIC: 1, T.ICE: 3, T.ROCK: 3,
                 T.STEEL: 1},
}


def type_effectiveness(move_type: PokeType, target_types: list) -> float:
    """Return the Gen 3 type-effectiveness multiplier (0, 0.25, 0.5, 1, 2, or 4)."""
    chart = _RAW.get(move_type, {})
    result = 1.0
    for t in target_types:
        raw = chart.get(t, 2)   # default = 2 (normal)
        result *= _MULT_TABLE[raw]
    return result


def is_type_immune(move_type: PokeType, target: PokemonState) -> bool:
    """Return True if target is fully immune to move_type via typing (0× from type chart).

    Does NOT check Volt Absorb, Water Absorb, or Flash Fire — use has_absorb_ability().
    """
    if type_effectiveness(move_type, target.types) == 0.0:
        return True
    if move_type == PokeType.GROUND and target.ability == "Levitate":
        return True
    return False


def has_absorb_ability(move_type: PokeType, target: PokemonState) -> bool:
    """Return True if target has an absorb ability matching the move type."""
    ability = target.ability
    if ability == "Volt Absorb" and move_type == PokeType.ELECTRIC:
        return True
    if ability == "Water Absorb" and move_type == PokeType.WATER:
        return True
    if ability == "Flash Fire" and move_type == PokeType.FIRE:
        return True
    return False


def is_user_faster(ctx: BattleContext) -> bool:
    """Return True if user is faster than target. Ties resolve to True."""
    return ctx.user.speed >= ctx.target.speed


# ---------------------------------------------------------------------------
# Flag 0 — Check Bad Move
# ---------------------------------------------------------------------------

def _has_status_move_raising(moves: list, effect: MoveEffect) -> bool:
    return any(m.effect == effect for m in moves)


def apply_flag0(ctx: BattleContext, rng) -> int:  # noqa: C901  (complexity expected)
    """Score adjustment from Flag 0 (bad-move filter).

    Returns a negative delta when the move is clearly useless, 0 otherwise,
    or +5 for Future Sight when not already active.
    """
    if ctx.targeting_ally:
        return 0

    move, user, target = ctx.move, ctx.user, ctx.target

    # --- Status / power1 / discouraged branch ---
    if (move.category == MoveCategory.STATUS
            or move.is_power1
            or move.is_discouraged):

        if move.is_sound and target.ability == "Soundproof":
            return -10

        if move.effect == MoveEffect.SLEEP:
            if (target.ability in ("Insomnia", "Vital Spirit")
                    or target.status is not None
                    or target.under_safeguard):
                return -10

        if move.effect in (MoveEffect.POISON, MoveEffect.TOXIC):
            if (PokeType.POISON in target.types
                    or PokeType.STEEL in target.types
                    or target.ability == "Immunity"
                    or target.under_safeguard):
                return -10

        if move.effect == MoveEffect.PARALYZE:
            if (PokeType.ELECTRIC in target.types
                    or target.ability == "Limber"
                    or target.status is not None
                    or target.under_safeguard):
                return -10

        if move.effect == MoveEffect.WILL_O_WISP:
            if (PokeType.FIRE in target.types
                    or target.ability == "Water Veil"
                    or target.status is not None
                    or target.under_safeguard):
                return -10

        if move.effect == MoveEffect.DREAM_EATER:
            if target.status != "sleep":
                return -8
            if is_type_immune(move.type, target):
                return -10

        if move.effect == MoveEffect.OHKO:
            if target.ability == "Sturdy":
                return -10
            if target.level > user.level:
                return -10

        if move.effect in (MoveEffect.CONFUSE, MoveEffect.SWAGGER,
                           MoveEffect.FLATTER, MoveEffect.TEETER_DANCE):
            if target.confused:
                return -5
            if target.ability == "Own Tempo":
                return -10

        if move.effect == MoveEffect.MIST:
            if user.under_mist:
                return -8

        if move.effect == MoveEffect.FOCUS_ENERGY:
            if user.under_focus_energy:
                return -10

        if move.effect == MoveEffect.LEECH_SEED:
            if PokeType.GRASS in target.types:
                return -10
            if target.under_leech_seed:
                return -8

        if move.effect == MoveEffect.FUTURE_SIGHT:
            if user.under_future_sight:
                return -12
            return 5

        if move.effect == MoveEffect.HAZE:
            # Useless if no meaningful stat changes on either side
            no_changes = (
                user.atk_stage == 0 and user.def_stage == 0 and
                user.spa_stage == 0 and user.spd_stage == 0 and
                user.spe_stage == 0 and
                target.atk_stage == 0 and target.def_stage == 0 and
                target.spa_stage == 0 and target.spd_stage == 0 and
                target.spe_stage == 0
            )
            if no_changes:
                return -10

        if move.effect == MoveEffect.PSYCH_UP:
            no_changes = (
                target.atk_stage == 0 and target.def_stage == 0 and
                target.spa_stage == 0 and target.spd_stage == 0 and
                target.spe_stage == 0
            )
            if no_changes:
                return -10

        if move.effect in _STAT_RAISE_EFFECTS:
            # Check if the corresponding stage is already maxed
            stage = _get_user_stage(user, move.effect)
            if stage >= 6:
                return -10

        if move.effect == MoveEffect.BULK_UP:
            if user.atk_stage >= 6 and user.def_stage >= 6:
                return -10

        if move.effect == MoveEffect.CALM_MIND:
            if user.spa_stage >= 6 and user.spd_stage >= 6:
                return -10

        if move.effect == MoveEffect.COSMIC_POWER:
            if user.def_stage >= 6 and user.spd_stage >= 6:
                return -10

        if move.effect == MoveEffect.DRAGON_DANCE:
            if user.atk_stage >= 6 and user.spe_stage >= 6:
                return -10

        if move.effect in _STAT_LOWER_EFFECTS:
            stage = _get_target_stage(target, move.effect)
            if stage <= -6:
                return -10
            if target.ability == "Clear Body":
                return -10
            if target.under_mist:
                return -10

        if move.effect == MoveEffect.TICKLE:
            if target.atk_stage <= -6 and target.def_stage <= -6:
                return -10
            if target.ability == "Clear Body":
                return -10
            if target.under_mist:
                return -10

        if move.effect == MoveEffect.EXPLODE:
            if target.ability == "Damp":
                return -10
            if is_type_immune(move.type, target):
                return -10
            # -1 if user has no other pokemon and target has no other pokemon
            if not user.has_other_pokemon and not target.has_other_pokemon:
                return -1

        if move.effect == MoveEffect.MAGNITUDE:
            if is_type_immune(PokeType.GROUND, target):
                return -10

        if move.effect == MoveEffect.ATTRACT:
            if (user.gender == "genderless" or target.gender == "genderless"
                    or user.gender == target.gender):
                return -10
            if target.ability == "Oblivious":
                return -10
            if target.under_attract:
                return -5

        if move.effect == MoveEffect.NIGHTMARE:
            if target.status != "sleep":
                return -10
            if target.under_nightmare:
                return -10

        if move.effect == MoveEffect.INGRAIN:
            if user.under_ingrain:
                return -10

        if move.effect == MoveEffect.IMPRISON:
            if user.imprison_active:
                return -10

        if move.effect == MoveEffect.RECYCLE:
            if user.used_item is None:
                return -10

        if move.effect == MoveEffect.SUBSTITUTE:
            if user.under_substitute:
                return -10
            if user.hp_pct <= 25:
                return -10

        if move.effect == MoveEffect.BELLY_DRUM:
            if user.atk_stage >= 6:
                return -10
            if user.hp_pct < 50:
                return -10

        if move.effect == MoveEffect.STOCKPILE:
            if user.stockpile_count >= 3:
                return -10

        if move.effect == MoveEffect.MEAN_LOOK:
            if target.under_mean_look:
                return -10

        if move.effect == MoveEffect.LOCK_ON:
            if target.under_lock_on:
                return -10

        if move.effect == MoveEffect.SPIKES:
            if not target.has_other_pokemon:
                return -10

        if move.effect == MoveEffect.PERISH_SONG:
            if target.under_perish_song:
                return -10

        if move.effect == MoveEffect.TORMENT:
            if target.under_torment:
                return -10

        if move.effect == MoveEffect.ENCORE:
            if target.under_encore:
                return -10
            if target.last_move_used is None:
                return -10

        if move.effect == MoveEffect.DISABLE:
            if target.under_disable:
                return -10
            if target.last_move_used is None:
                return -10

        if move.effect == MoveEffect.FORESIGHT:
            if target.under_foresight:
                return -10

        if move.effect == MoveEffect.YAWN:
            if target.under_yawn:
                return -10
            if target.status is not None:
                return -10

        return 0

    # --- Damaging moves (not status / power1 / discouraged) ---
    if is_type_immune(move.type, target):
        return -10
    if has_absorb_ability(move.type, target):
        return -12
    if target.ability == "Wonder Guard" and type_effectiveness(move.type, target.types) < 2.0:
        return -10
    return 0


def _get_user_stage(user: PokemonState, effect: MoveEffect) -> int:
    return {
        MoveEffect.ATK_UP: user.atk_stage,
        MoveEffect.DEF_UP: user.def_stage,
        MoveEffect.SPA_UP: user.spa_stage,
        MoveEffect.SPD_UP: user.spd_stage,
        MoveEffect.SPE_UP: user.spe_stage,
        MoveEffect.ACC_UP: user.acc_stage,
        MoveEffect.EVA_UP: user.eva_stage,
    }.get(effect, 0)


def _get_target_stage(target: PokemonState, effect: MoveEffect) -> int:
    return {
        MoveEffect.ATK_DOWN: target.atk_stage,
        MoveEffect.DEF_DOWN: target.def_stage,
        MoveEffect.SPA_DOWN: target.spa_stage,
        MoveEffect.SPD_DOWN: target.spd_stage,
        MoveEffect.SPE_DOWN: target.spe_stage,
        MoveEffect.ACC_DOWN: target.acc_stage,
        MoveEffect.EVA_DOWN: target.eva_stage,
        MoveEffect.TICKLE:   target.atk_stage,   # primary check is atk for Tickle
    }.get(effect, 0)


# ---------------------------------------------------------------------------
# Flag 3 — Setup First Turn
# ---------------------------------------------------------------------------

def apply_flag3(ctx: BattleContext, rng) -> int:
    """Return +2 with 80/256 probability if it is the very first battle turn and
    the move is a setup move; otherwise return 0."""
    if ctx.targeting_ally:
        return 0
    if ctx.is_first_battle_turn and ctx.move.effect in SETUP_FIRST_TURN_EFFECTS:
        return 2 if rng(80) else 0
    return 0


# ---------------------------------------------------------------------------
# Flag 4 — Risky
# ---------------------------------------------------------------------------

def apply_flag4(ctx: BattleContext, rng) -> int:
    """Return +2 with 128/256 probability if the move is in RISKY_EFFECTS."""
    if ctx.targeting_ally:
        return 0
    if ctx.move.effect in RISKY_EFFECTS:
        return 2 if rng(128) else 0
    return 0


# ---------------------------------------------------------------------------
# Flag 1 — Try To Faint
# ---------------------------------------------------------------------------

def apply_flag1(ctx: BattleContext, rng) -> int:
    """Score adjustment from Flag 1 (try-to-faint check).

    If the move would KO the target: +6 for priority moves, +4 otherwise
    (Explode/Memento: return 0 without bonus).
    If it is not the highest-damage move available: -1.
    If 4× effective: 176/256 chance of +2.
    """
    if ctx.targeting_ally:
        return 0

    move = ctx.move
    score = 0

    damage_pct = ctx.move_damage_pcts.get(move.name, 0.0)
    would_faint = damage_pct >= ctx.target.hp_pct

    if would_faint:
        if move.effect in (MoveEffect.EXPLODE, MoveEffect.MEMENTO):
            return 0
        if move.priority > 0:
            score += 6
        else:
            score += 4
        return score

    # Not a KO — check if this is the highest-damage move
    if ctx.move_damage_pcts:
        best_dmg = max(ctx.move_damage_pcts.values())
        if damage_pct < best_dmg:
            score -= 1

    # 4× super-effective bonus
    effectiveness = type_effectiveness(move.type, ctx.target.types)
    if effectiveness >= 4.0:
        if rng(176):
            score += 2

    return score


# ---------------------------------------------------------------------------
# Flag 2 — Check Viability
# ---------------------------------------------------------------------------

def apply_flag2(ctx: BattleContext, rng) -> int:  # noqa: C901
    """Score adjustment from Flag 2 (move-viability checks).

    This flag implements a large branching tree covering most move effects.
    Returns the cumulative score delta from all matching "continue" segments
    plus the terminal return for the matched effect block.
    """
    if ctx.targeting_ally:
        return 0

    move, user, target = ctx.move, ctx.user, ctx.target
    score = 0

    # --- Sleep moves ---
    if move.effect == MoveEffect.SLEEP:
        # Bonus if we have Dream Eater
        if any(m.effect == MoveEffect.DREAM_EATER for m in user.moves):
            score += 1
        return score

    # --- Draining moves ---
    if move.is_draining:
        eff = type_effectiveness(move.type, target.types)
        if eff < 1.0 and rng(128):
            score -= 3
        return score

    # --- Explode / Memento ---
    if move.effect in (MoveEffect.EXPLODE, MoveEffect.MEMENTO):
        if user.hp_pct > 70:
            if rng(128):
                score -= 3
        elif user.hp_pct > 40:
            if rng(128):
                score -= 1
        else:
            if rng(128):
                score += 1
        return score

    # --- Speed boost (checked before generic stat-raise) ---
    if move.effect == MoveEffect.SPE_UP:
        if is_user_faster(ctx):
            score -= 3
        elif rng(128):
            score += 3
        return score

    # --- Evasion boost (checked before generic stat-raise) ---
    if move.effect == MoveEffect.EVA_UP:
        if user.hp_pct > 70 and rng(128):
            score += 3
        return score

    # --- Stat-raise (single stat, excluding SPE_UP and EVA_UP handled above) ---
    if move.effect in _STAT_RAISE_EFFECTS:
        stage = _get_user_stage(user, move.effect)
        if stage >= 3:
            score -= 1
        elif user.hp_pct == 100 and rng(128):
            score += 2
        return score

    # --- Multi-stat raise ---
    if move.effect in (MoveEffect.BULK_UP, MoveEffect.CALM_MIND,
                       MoveEffect.COSMIC_POWER, MoveEffect.DRAGON_DANCE):
        max_stage = max(user.atk_stage, user.def_stage, user.spa_stage,
                        user.spd_stage, user.spe_stage)
        if max_stage >= 3:
            score -= 1
        elif user.hp_pct == 100 and rng(128):
            score += 2
        return score

    # --- HP-restore moves ---
    if move.effect in (MoveEffect.RECOVER, MoveEffect.SOFTBOILED,
                       MoveEffect.WEATHER_HEAL, MoveEffect.SWALLOW):
        if user.hp_pct == 100:
            score -= 3
        elif user.hp_pct > 50:
            score -= 1
        return score

    if move.effect == MoveEffect.REST:
        if user.hp_pct == 100:
            score -= 8
        elif is_user_faster(ctx) and user.hp_pct > 50:
            score -= 8
        elif not is_user_faster(ctx) and rng(128):
            score += 2
        return score

    # --- Toxic / Poison (secondary check) ---
    if move.effect in (MoveEffect.TOXIC, MoveEffect.POISON):
        # Discourage if user has no damaging move to finish off
        has_damaging = any(m.category != MoveCategory.STATUS for m in user.moves)
        if has_damaging and target.hp_pct < 25:
            score -= 3
        return score

    # --- Reflect ---
    if move.effect == MoveEffect.REFLECT:
        if user.under_reflect:
            score -= 2
        elif rng(128):
            score += 0   # continue (no bonus, but doesn't penalise)
        return score

    # --- Light Screen ---
    if move.effect == MoveEffect.LIGHT_SCREEN:
        if user.under_light_screen:
            score -= 2
        elif rng(128):
            score += 0
        return score

    # --- Safeguard ---
    if move.effect == MoveEffect.SAFEGUARD:
        if user.under_safeguard:
            score -= 2
        return score

    # --- Weather moves ---
    if move.effect == MoveEffect.RAIN_DANCE:
        if ctx.weather == Weather.RAIN:
            score -= 2
        return score
    if move.effect == MoveEffect.SUNNY_DAY:
        if ctx.weather == Weather.SUN:
            score -= 2
        return score
    if move.effect == MoveEffect.SANDSTORM:
        if ctx.weather == Weather.SAND:
            score -= 2
        return score
    if move.effect == MoveEffect.HAIL:
        if ctx.weather == Weather.HAIL:
            score -= 2
        return score

    # --- Protect / Endure ---
    if move.effect in (MoveEffect.PROTECT, MoveEffect.ENDURE):
        if user.protected_consecutive > 0:
            score -= 5
        elif target.status in ("badly_poison", "burn", "poison") and rng(128):
            score += 2
        return score

    # --- Sleep Talk ---
    if move.effect == MoveEffect.SLEEP_TALK:
        if user.status == "sleep":
            score += 10
        else:
            score -= 5
        return score

    # --- Destiny Bond ---
    if move.effect == MoveEffect.DESTINY_BOND:
        if user.hp_pct <= 25 and rng(128):
            score += 2
        return score

    # --- Substitute ---
    if move.effect == MoveEffect.SUBSTITUTE:
        if user.hp_pct > 50:
            score -= 1
        return score

    # --- Roar / Whirlwind ---
    if move.effect == MoveEffect.ROAR:
        max_target_stage = max(
            target.atk_stage, target.def_stage, target.spa_stage,
            target.spd_stage, target.spe_stage
        )
        if max_target_stage >= 3 and rng(128):
            score += 2
        else:
            score -= 3
        return score

    # --- Snore / Sleep Talk for sleeping user ---
    if move.effect == MoveEffect.SNORE:
        if user.status == "sleep":
            score += 5
        return score

    # --- Confuse (secondary viability) ---
    if move.effect in (MoveEffect.CONFUSE, MoveEffect.SWAGGER,
                       MoveEffect.FLATTER, MoveEffect.TEETER_DANCE):
        if target.confused:
            score -= 5
        elif rng(128):
            score += 1
        return score

    # --- Attract ---
    if move.effect == MoveEffect.ATTRACT:
        if target.under_attract:
            score -= 5
        elif rng(128):
            score += 1
        return score

    # --- Baton Pass ---
    if move.effect == MoveEffect.BATON_PASS:
        max_stage = max(user.atk_stage, user.def_stage, user.spa_stage,
                        user.spd_stage, user.spe_stage)
        if max_stage >= 2 and rng(128):
            score += 3
        return score

    # --- Belly Drum ---
    if move.effect == MoveEffect.BELLY_DRUM:
        if user.hp_pct >= 50 and rng(128):
            score += 3
        return score

    # --- Spikes ---
    if move.effect == MoveEffect.SPIKES:
        if target.has_other_pokemon and rng(128):
            score += 2
        return score

    # --- Curse ---
    if move.effect == MoveEffect.CURSE:
        if PokeType.GHOST not in user.types:
            # Stat-boost path
            if user.atk_stage < 3 and rng(128):
                score += 2
        else:
            # Ghost curse — apply to target
            if not target.cursed and rng(128):
                score += 2
        return score

    # --- Trick / Knock Off / Thief ---
    if move.effect in (MoveEffect.TRICK, MoveEffect.KNOCK_OFF, MoveEffect.THIEF):
        if target.held_item is not None and rng(128):
            score += 2
        return score

    # --- Skill Swap ---
    if move.effect == MoveEffect.SKILL_SWAP:
        if rng(128):
            score += 1
        return score

    # Default: no adjustment for unhandled effects
    return score


# ---------------------------------------------------------------------------
# Flag 7 — Double Battle
# ---------------------------------------------------------------------------

def apply_flag7(ctx: BattleContext, rng) -> int:
    """Score adjustment from Flag 7 (double-battle specific logic).

    When targeting an ally:
    - Helping Hand always gets +2 (if rng fires).
    - Most other status/field moves aimed at ally get -30.
    - Fire-type move on ally with Flash Fire ability: +3.
    - Earthquake on ally with Levitate: +2.

    When targeting an enemy:
    - If this move is the highest-damage option: +3.
    - Guts-powered status: small bonus.
    """
    if not ctx.is_double_battle:
        return 0

    move, user, target = ctx.move, ctx.user, ctx.target

    if ctx.targeting_ally:
        if move.effect == MoveEffect.HELPING_HAND:
            return 2 if rng(128) else 0

        # Fire on Flash Fire ally
        if move.type == PokeType.FIRE and target.ability == "Flash Fire":
            return 3

        # Earthquake hitting Levitate ally
        if move.effect == MoveEffect.MAGNITUDE or (
                move.type == PokeType.GROUND and
                move.category != MoveCategory.STATUS):
            if target.ability == "Levitate":
                return 2

        # Most other moves aimed at ally are heavily penalised
        if move.category == MoveCategory.STATUS:
            return -30

        return 0

    # --- Targeting an enemy ---
    if ctx.move_damage_pcts:
        best_dmg = max(ctx.move_damage_pcts.values())
        this_dmg = ctx.move_damage_pcts.get(move.name, 0.0)
        if this_dmg >= best_dmg and best_dmg > 0:
            if rng(128):
                return 3

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def score_move(ctx: BattleContext, active_flags: list,
               rng=None) -> int:
    """Run the specified flags over ctx and return the total score delta.

    Args:
        ctx: BattleContext describing the current battle state and move.
        active_flags: subset of [0, 1, 2, 3, 4, 7].
        rng: callable(threshold_0_255) -> bool.  Defaults to real random.
    """
    if rng is None:
        rng = make_rng()
    flag_funcs = {
        0: apply_flag0,
        1: apply_flag1,
        2: apply_flag2,
        3: apply_flag3,
        4: apply_flag4,
        7: apply_flag7,
    }
    total = 0
    for flag_id in sorted(active_flags):
        fn = flag_funcs.get(flag_id)
        if fn is not None:
            total += fn(ctx, rng)
    return total
