"""
Tests for ai_flags.py — Pokemon Emerald trainer AI flag logic.
All tests use synthetic data; no IPC, no pickle file required.
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_flags import (
    PokeType, Weather, MoveCategory, MoveEffect,
    MoveInfo, PokemonState, BattleContext,
    type_effectiveness, is_type_immune, has_absorb_ability, is_user_faster,
    apply_flag0, apply_flag1, apply_flag2, apply_flag3, apply_flag4, apply_flag7,
    score_move,
    POWER1_EFFECTS, DISCOURAGED_EFFECTS, SETUP_FIRST_TURN_EFFECTS, RISKY_EFFECTS,
)

# ---------------------------------------------------------------------------
# RNG fixtures
# ---------------------------------------------------------------------------
ALWAYS = lambda t: True   # threshold always passes
NEVER  = lambda t: False  # threshold never passes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _move(name="tackle", type=PokeType.NORMAL, cat=MoveCategory.PHYSICAL,
          power=40, effect=MoveEffect.NONE, priority=0,
          is_sound=False, is_draining=False, is_trapping=False, is_high_crit=False):
    return MoveInfo(name=name, type=type, category=cat, power=power,
                    effect=effect, priority=priority, is_sound=is_sound,
                    is_draining=is_draining, is_trapping=is_trapping,
                    is_high_crit=is_high_crit)


def _poke(hp_pct=100, types=None, ability="", status=None, moves=None,
          held_item=None, used_item=None, level=50, gender="male", speed=100,
          is_first_turn=False, **kwargs):
    if types is None:
        types = [PokeType.NORMAL]
    if moves is None:
        moves = []
    return PokemonState(
        hp_pct=hp_pct, types=types, ability=ability, status=status,
        moves=moves, held_item=held_item, used_item=used_item,
        level=level, gender=gender, speed=speed, is_first_turn=is_first_turn,
        **kwargs,
    )


def _ctx(move=None, user=None, target=None, weather=Weather.NONE,
         targeting_ally=False, is_double_battle=False,
         is_first_battle_turn=False, move_damage_pcts=None):
    if move is None:
        move = _move()
    if user is None:
        user = _poke()
    if target is None:
        target = _poke()
    return BattleContext(
        user=user, target=target, move=move,
        weather=weather, targeting_ally=targeting_ally,
        is_double_battle=is_double_battle,
        is_first_battle_turn=is_first_battle_turn,
        move_damage_pcts=move_damage_pcts or {},
    )


# ---------------------------------------------------------------------------
# type_effectiveness
# ---------------------------------------------------------------------------

class TestTypeEffectiveness:
    def test_normal_vs_normal(self):
        assert type_effectiveness(PokeType.NORMAL, [PokeType.NORMAL]) == pytest.approx(1.0)

    def test_electric_vs_water(self):
        assert type_effectiveness(PokeType.ELECTRIC, [PokeType.WATER]) == pytest.approx(2.0)

    def test_electric_vs_ground(self):
        assert type_effectiveness(PokeType.ELECTRIC, [PokeType.GROUND]) == pytest.approx(0.0)

    def test_fire_vs_grass(self):
        assert type_effectiveness(PokeType.FIRE, [PokeType.GRASS]) == pytest.approx(2.0)

    def test_water_vs_fire_water(self):
        # Water vs Fire/Water: 2× * 0.5× = 1×
        result = type_effectiveness(PokeType.WATER, [PokeType.FIRE, PokeType.WATER])
        assert result == pytest.approx(1.0)

    def test_fighting_vs_ghost(self):
        assert type_effectiveness(PokeType.FIGHTING, [PokeType.GHOST]) == pytest.approx(0.0)

    def test_fire_vs_grass_bug(self):
        # 2× (grass) * 2× (bug) = 4×
        result = type_effectiveness(PokeType.FIRE, [PokeType.GRASS, PokeType.BUG])
        assert result == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# is_type_immune / has_absorb_ability
# ---------------------------------------------------------------------------

class TestIsTypeImmune:
    def test_electric_vs_ground_type(self):
        target = _poke(types=[PokeType.GROUND])
        assert is_type_immune(PokeType.ELECTRIC, target) is True

    def test_normal_vs_ghost(self):
        target = _poke(types=[PokeType.GHOST])
        assert is_type_immune(PokeType.NORMAL, target) is True

    def test_ground_vs_levitate(self):
        target = _poke(types=[PokeType.NORMAL], ability="Levitate")
        assert is_type_immune(PokeType.GROUND, target) is True

    def test_ground_vs_non_levitate(self):
        target = _poke(types=[PokeType.NORMAL], ability="")
        assert is_type_immune(PokeType.GROUND, target) is False

    def test_fire_vs_water_not_immune(self):
        target = _poke(types=[PokeType.WATER])
        assert is_type_immune(PokeType.FIRE, target) is False


class TestHasAbsorbAbility:
    def test_volt_absorb_electric(self):
        target = _poke(ability="Volt Absorb")
        assert has_absorb_ability(PokeType.ELECTRIC, target) is True

    def test_volt_absorb_non_electric(self):
        target = _poke(ability="Volt Absorb")
        assert has_absorb_ability(PokeType.FIRE, target) is False

    def test_water_absorb(self):
        target = _poke(ability="Water Absorb")
        assert has_absorb_ability(PokeType.WATER, target) is True

    def test_flash_fire(self):
        target = _poke(ability="Flash Fire")
        assert has_absorb_ability(PokeType.FIRE, target) is True


# ---------------------------------------------------------------------------
# Flag 0
# ---------------------------------------------------------------------------

class TestFlag0:
    def test_targeting_ally_skips(self):
        ctx = _ctx(targeting_ally=True, move=_move(cat=MoveCategory.STATUS))
        assert apply_flag0(ctx, ALWAYS) == 0

    def test_sound_vs_soundproof(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.SLEEP, is_sound=True)
        target = _poke(ability="Soundproof")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_sleep_vs_insomnia(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.SLEEP)
        target = _poke(ability="Insomnia")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_sleep_vs_vital_spirit(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.SLEEP)
        target = _poke(ability="Vital Spirit")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_sleep_vs_already_statused(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.SLEEP)
        target = _poke(status="burn")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_sleep_vs_safeguard(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.SLEEP)
        target = _poke(under_safeguard=True)
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_sleep_clean_target_no_penalty(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.SLEEP)
        target = _poke()
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == 0

    def test_toxic_vs_poison_type(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.TOXIC)
        target = _poke(types=[PokeType.POISON])
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_toxic_vs_steel_type(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.TOXIC)
        target = _poke(types=[PokeType.STEEL])
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_toxic_vs_immunity_ability(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.TOXIC)
        target = _poke(ability="Immunity")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_paralyze_vs_ground_type(self):
        # Thunder Wave is Electric-type; Ground is immune to Electric → -10
        move = _move(type=PokeType.ELECTRIC, cat=MoveCategory.STATUS, effect=MoveEffect.PARALYZE)
        target = _poke(types=[PokeType.GROUND])
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_paralyze_vs_electric_type(self):
        # Electric resists Electric (0.5×) but is NOT immune → no -10 penalty
        move = _move(type=PokeType.ELECTRIC, cat=MoveCategory.STATUS, effect=MoveEffect.PARALYZE)
        target = _poke(types=[PokeType.ELECTRIC])
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == 0

    def test_paralyze_vs_limber(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.PARALYZE)
        target = _poke(ability="Limber")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_will_o_wisp_vs_fire_type(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.WILL_O_WISP)
        target = _poke(types=[PokeType.FIRE])
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_will_o_wisp_vs_water_veil(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.WILL_O_WISP)
        target = _poke(ability="Water Veil")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_dream_eater_not_sleeping(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.DREAM_EATER)
        target = _poke()
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -8

    def test_dream_eater_sleeping_immune_type(self):
        move = _move(type=PokeType.PSYCHIC, cat=MoveCategory.STATUS,
                     effect=MoveEffect.DREAM_EATER)
        target = _poke(types=[PokeType.DARK], status="sleep")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_ohko_vs_sturdy(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.OHKO)
        target = _poke(ability="Sturdy")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_ohko_vs_higher_level(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.OHKO)
        user = _poke(level=40)
        target = _poke(level=50)
        ctx = _ctx(move=move, user=user, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_confuse_vs_already_confused(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.CONFUSE)
        target = _poke(confused=True)
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -5

    def test_confuse_vs_own_tempo(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.CONFUSE)
        target = _poke(ability="Own Tempo")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_mist_already_active(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.MIST)
        user = _poke(under_mist=True)
        ctx = _ctx(move=move, user=user)
        assert apply_flag0(ctx, ALWAYS) == -8

    def test_focus_energy_already_active(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.FOCUS_ENERGY)
        user = _poke(under_focus_energy=True)
        ctx = _ctx(move=move, user=user)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_leech_seed_vs_grass_type(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.LEECH_SEED)
        target = _poke(types=[PokeType.GRASS])
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_future_sight_not_active(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.FUTURE_SIGHT)
        user = _poke(under_future_sight=False)
        ctx = _ctx(move=move, user=user)
        assert apply_flag0(ctx, ALWAYS) == 5

    def test_future_sight_already_active(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.FUTURE_SIGHT)
        user = _poke(under_future_sight=True)
        ctx = _ctx(move=move, user=user)
        assert apply_flag0(ctx, ALWAYS) == -12

    def test_stat_raise_already_maxed(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.ATK_UP)
        user = _poke(atk_stage=6)
        ctx = _ctx(move=move, user=user)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_stat_lower_already_at_min(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.ATK_DOWN)
        target = _poke(atk_stage=-6)
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_stat_lower_vs_clear_body(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.DEF_DOWN)
        target = _poke(ability="Clear Body")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_explode_vs_damp(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.EXPLODE)
        target = _poke(ability="Damp")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_explode_user_has_others_target_has_others(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.EXPLODE)
        user = _poke(has_other_pokemon=True)
        target = _poke(has_other_pokemon=True)
        ctx = _ctx(move=move, user=user, target=target)
        assert apply_flag0(ctx, ALWAYS) == 0

    def test_explode_user_alone_target_alone(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.EXPLODE)
        user = _poke(has_other_pokemon=False)
        target = _poke(has_other_pokemon=False)
        ctx = _ctx(move=move, user=user, target=target)
        assert apply_flag0(ctx, ALWAYS) == -1

    def test_damaging_vs_type_immune(self):
        move = _move(type=PokeType.ELECTRIC, cat=MoveCategory.SPECIAL, power=90)
        target = _poke(types=[PokeType.GROUND])
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_damaging_vs_volt_absorb(self):
        move = _move(type=PokeType.ELECTRIC, cat=MoveCategory.SPECIAL, power=90)
        target = _poke(ability="Volt Absorb")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -12

    def test_damaging_vs_wonder_guard_non_se(self):
        move = _move(type=PokeType.NORMAL, cat=MoveCategory.PHYSICAL, power=40)
        target = _poke(types=[PokeType.GHOST], ability="Wonder Guard")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == -10

    def test_damaging_vs_wonder_guard_se(self):
        # Dark is SE vs Ghost: 2×, so Wonder Guard does NOT block it
        move = _move(type=PokeType.DARK, cat=MoveCategory.SPECIAL, power=80)
        target = _poke(types=[PokeType.GHOST], ability="Wonder Guard")
        ctx = _ctx(move=move, target=target)
        assert apply_flag0(ctx, ALWAYS) == 0


# ---------------------------------------------------------------------------
# Flag 1
# ---------------------------------------------------------------------------

class TestFlag1:
    def test_targeting_ally_skips(self):
        ctx = _ctx(targeting_ally=True)
        assert apply_flag1(ctx, ALWAYS) == 0

    def test_would_faint_explode_returns_0(self):
        move = _move(effect=MoveEffect.EXPLODE)
        target = _poke(hp_pct=30)
        ctx = _ctx(move=move, target=target, move_damage_pcts={"explode": 50.0})
        ctx.move.name = "explode"
        assert apply_flag1(ctx, ALWAYS) == 0

    def test_would_faint_priority_move(self):
        move = _move(name="quick attack", priority=1, power=40)
        target = _poke(hp_pct=30)
        ctx = _ctx(move=move, target=target,
                   move_damage_pcts={"quick attack": 40.0})
        assert apply_flag1(ctx, ALWAYS) == 6

    def test_would_faint_normal_move(self):
        move = _move(name="tackle", power=40)
        target = _poke(hp_pct=30)
        ctx = _ctx(move=move, target=target,
                   move_damage_pcts={"tackle": 40.0})
        assert apply_flag1(ctx, ALWAYS) == 4

    def test_not_highest_damage(self):
        move = _move(name="tackle", power=40)
        target = _poke(hp_pct=80)
        ctx = _ctx(move=move, target=target,
                   move_damage_pcts={"tackle": 10.0, "earthquake": 50.0})
        assert apply_flag1(ctx, ALWAYS) == -1

    def test_x4_effective_boost_fires(self):
        # Ground vs Fire/Rock → should be 4×
        move = _move(name="earthquake", type=PokeType.GROUND, power=100)
        target = _poke(hp_pct=80, types=[PokeType.FIRE, PokeType.ROCK])
        ctx = _ctx(move=move, target=target,
                   move_damage_pcts={"earthquake": 60.0})
        assert apply_flag1(ctx, ALWAYS) == 2  # best dmg, 4× → +2

    def test_x4_effective_boost_misses(self):
        move = _move(name="earthquake", type=PokeType.GROUND, power=100)
        target = _poke(hp_pct=80, types=[PokeType.FIRE, PokeType.ROCK])
        ctx = _ctx(move=move, target=target,
                   move_damage_pcts={"earthquake": 60.0})
        assert apply_flag1(ctx, NEVER) == 0  # NEVER rng → no +2


# ---------------------------------------------------------------------------
# Flag 2
# ---------------------------------------------------------------------------

class TestFlag2:
    def test_targeting_ally_skips(self):
        ctx = _ctx(targeting_ally=True)
        assert apply_flag2(ctx, ALWAYS) == 0

    def test_sleep_move_with_dream_eater_always(self):
        de_move = _move(name="dreameater", effect=MoveEffect.DREAM_EATER)
        user = _poke(moves=[de_move])
        move = _move(name="spore", cat=MoveCategory.STATUS, effect=MoveEffect.SLEEP)
        ctx = _ctx(move=move, user=user)
        assert apply_flag2(ctx, ALWAYS) == 1

    def test_sleep_move_no_dream_eater(self):
        user = _poke(moves=[])
        move = _move(name="spore", cat=MoveCategory.STATUS, effect=MoveEffect.SLEEP)
        ctx = _ctx(move=move, user=user)
        assert apply_flag2(ctx, ALWAYS) == 0

    def test_draining_resisted_always(self):
        move = _move(name="mega drain", type=PokeType.GRASS, power=40,
                     effect=MoveEffect.NONE, is_draining=True)
        target = _poke(types=[PokeType.FIRE])   # resist Grass → 0.5×
        ctx = _ctx(move=move, target=target)
        assert apply_flag2(ctx, ALWAYS) == -3

    def test_draining_not_resisted(self):
        move = _move(name="mega drain", type=PokeType.GRASS, power=40,
                     effect=MoveEffect.NONE, is_draining=True)
        target = _poke(types=[PokeType.WATER])  # 2× — not resisted
        ctx = _ctx(move=move, target=target)
        assert apply_flag2(ctx, ALWAYS) == 0

    def test_explode_high_hp_always(self):
        move = _move(name="explosion", effect=MoveEffect.EXPLODE, cat=MoveCategory.STATUS)
        user = _poke(hp_pct=80)
        ctx = _ctx(move=move, user=user)
        assert apply_flag2(ctx, ALWAYS) == -3

    def test_explode_medium_hp_always(self):
        move = _move(name="explosion", effect=MoveEffect.EXPLODE, cat=MoveCategory.STATUS)
        user = _poke(hp_pct=55)
        ctx = _ctx(move=move, user=user)
        assert apply_flag2(ctx, ALWAYS) == -1

    def test_explode_low_hp_always(self):
        move = _move(name="explosion", effect=MoveEffect.EXPLODE, cat=MoveCategory.STATUS)
        user = _poke(hp_pct=20)
        ctx = _ctx(move=move, user=user)
        # HP=20: both +1 tiers apply (<=50 and <=30), so +2 with ALWAYS
        assert apply_flag2(ctx, ALWAYS) == 2

    def test_atk_boost_low_stage_full_hp_always(self):
        move = _move(name="sd", cat=MoveCategory.STATUS, effect=MoveEffect.ATK_UP)
        user = _poke(hp_pct=100, atk_stage=0)
        ctx = _ctx(move=move, user=user)
        assert apply_flag2(ctx, ALWAYS) == 2

    def test_atk_boost_high_stage_always(self):
        move = _move(name="sd", cat=MoveCategory.STATUS, effect=MoveEffect.ATK_UP)
        user = _poke(hp_pct=100, atk_stage=4)
        ctx = _ctx(move=move, user=user)
        assert apply_flag2(ctx, ALWAYS) == -1

    def test_speed_boost_user_faster(self):
        move = _move(name="agility", cat=MoveCategory.STATUS, effect=MoveEffect.SPE_UP)
        user = _poke(speed=200)
        target = _poke(speed=100)
        ctx = _ctx(move=move, user=user, target=target)
        assert apply_flag2(ctx, ALWAYS) == -3

    def test_speed_boost_target_faster_always(self):
        move = _move(name="agility", cat=MoveCategory.STATUS, effect=MoveEffect.SPE_UP)
        user = _poke(speed=50)
        target = _poke(speed=200)
        ctx = _ctx(move=move, user=user, target=target)
        assert apply_flag2(ctx, ALWAYS) == 3

    def test_evasion_boost_high_hp_always(self):
        # Source: only applies +3 bonus when HP >= 90%; HP=95 exercises that path
        move = _move(name="double team", cat=MoveCategory.STATUS, effect=MoveEffect.EVA_UP)
        user = _poke(hp_pct=95)
        ctx = _ctx(move=move, user=user)
        assert apply_flag2(ctx, ALWAYS) == 3

    def test_recover_full_hp(self):
        move = _move(name="recover", cat=MoveCategory.STATUS, effect=MoveEffect.RECOVER)
        user = _poke(hp_pct=100)
        ctx = _ctx(move=move, user=user)
        assert apply_flag2(ctx, ALWAYS) == -3

    def test_rest_faster_user_full_hp(self):
        move = _move(name="rest", cat=MoveCategory.STATUS, effect=MoveEffect.REST)
        user = _poke(hp_pct=100, speed=200)
        target = _poke(speed=100)
        ctx = _ctx(move=move, user=user, target=target)
        assert apply_flag2(ctx, ALWAYS) == -8

    def test_sleep_talk_user_asleep(self):
        move = _move(name="sleep talk", cat=MoveCategory.STATUS, effect=MoveEffect.SLEEP_TALK)
        user = _poke(status="sleep")
        ctx = _ctx(move=move, user=user)
        assert apply_flag2(ctx, ALWAYS) == 10

    def test_sleep_talk_user_awake(self):
        move = _move(name="sleep talk", cat=MoveCategory.STATUS, effect=MoveEffect.SLEEP_TALK)
        user = _poke(status=None)
        ctx = _ctx(move=move, user=user)
        assert apply_flag2(ctx, ALWAYS) == -5

    def test_destiny_bond_low_hp_always(self):
        move = _move(name="destiny bond", cat=MoveCategory.STATUS,
                     effect=MoveEffect.DESTINY_BOND)
        user = _poke(hp_pct=20)
        ctx = _ctx(move=move, user=user)
        # -1 always; equal speed = user not slower; HP<=70,<=50,<=30 all apply
        # → -1 + 1 + 1 + 2 = +3 with ALWAYS
        assert apply_flag2(ctx, ALWAYS) == 3

    def test_substitute_high_hp(self):
        move = _move(name="substitute", cat=MoveCategory.STATUS, effect=MoveEffect.SUBSTITUTE)
        user = _poke(hp_pct=80)
        ctx = _ctx(move=move, user=user)
        assert apply_flag2(ctx, ALWAYS) == -1

    def test_roar_vs_high_stat_target_always(self):
        move = _move(name="roar", cat=MoveCategory.STATUS, effect=MoveEffect.ROAR)
        target = _poke(atk_stage=4)
        ctx = _ctx(move=move, target=target)
        assert apply_flag2(ctx, ALWAYS) == 2

    def test_roar_vs_normal_target(self):
        move = _move(name="roar", cat=MoveCategory.STATUS, effect=MoveEffect.ROAR)
        target = _poke(atk_stage=0)
        ctx = _ctx(move=move, target=target)
        assert apply_flag2(ctx, ALWAYS) == -3


# ---------------------------------------------------------------------------
# Flag 3
# ---------------------------------------------------------------------------

class TestFlag3:
    def test_first_battle_turn_setup_move_always(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.CALM_MIND)
        ctx = _ctx(move=move, is_first_battle_turn=True)
        assert apply_flag3(ctx, ALWAYS) == 2

    def test_first_battle_turn_setup_move_never(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.CALM_MIND)
        ctx = _ctx(move=move, is_first_battle_turn=True)
        assert apply_flag3(ctx, NEVER) == 0

    def test_not_first_battle_turn(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.CALM_MIND)
        ctx = _ctx(move=move, is_first_battle_turn=False)
        assert apply_flag3(ctx, ALWAYS) == 0

    def test_non_setup_move(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.SLEEP)
        ctx = _ctx(move=move, is_first_battle_turn=True)
        assert apply_flag3(ctx, ALWAYS) == 0

    def test_targeting_ally_skips(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.CALM_MIND)
        ctx = _ctx(move=move, is_first_battle_turn=True, targeting_ally=True)
        assert apply_flag3(ctx, ALWAYS) == 0


# ---------------------------------------------------------------------------
# Flag 4
# ---------------------------------------------------------------------------

class TestFlag4:
    def test_risky_move_always(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.FOCUS_PUNCH)
        ctx = _ctx(move=move)
        assert apply_flag4(ctx, ALWAYS) == 2

    def test_risky_move_never(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.FOCUS_PUNCH)
        ctx = _ctx(move=move)
        assert apply_flag4(ctx, NEVER) == 0

    def test_non_risky_move(self):
        move = _move(cat=MoveCategory.PHYSICAL, power=40)
        ctx = _ctx(move=move)
        assert apply_flag4(ctx, ALWAYS) == 0

    def test_targeting_ally_skips(self):
        move = _move(cat=MoveCategory.STATUS, effect=MoveEffect.FOCUS_PUNCH)
        ctx = _ctx(move=move, targeting_ally=True)
        assert apply_flag4(ctx, ALWAYS) == 0


# ---------------------------------------------------------------------------
# Flag 7
# ---------------------------------------------------------------------------

class TestFlag7:
    def test_not_double_battle_returns_0(self):
        ctx = _ctx(is_double_battle=False)
        assert apply_flag7(ctx, ALWAYS) == 0

    def test_targeting_ally_helping_hand_always(self):
        move = _move(name="helping hand", cat=MoveCategory.STATUS,
                     effect=MoveEffect.HELPING_HAND)
        ctx = _ctx(move=move, targeting_ally=True, is_double_battle=True)
        assert apply_flag7(ctx, ALWAYS) == 2

    def test_targeting_ally_helping_hand_never(self):
        move = _move(name="helping hand", cat=MoveCategory.STATUS,
                     effect=MoveEffect.HELPING_HAND)
        ctx = _ctx(move=move, targeting_ally=True, is_double_battle=True)
        assert apply_flag7(ctx, NEVER) == 0

    def test_targeting_ally_status_move_returns_minus30(self):
        move = _move(name="toxic", cat=MoveCategory.STATUS, effect=MoveEffect.TOXIC)
        ctx = _ctx(move=move, targeting_ally=True, is_double_battle=True)
        assert apply_flag7(ctx, ALWAYS) == -30

    def test_targeting_ally_fire_flash_fire_inactive(self):
        move = _move(name="flamethrower", type=PokeType.FIRE, cat=MoveCategory.SPECIAL,
                     power=95)
        target = _poke(ability="Flash Fire")
        ctx = _ctx(move=move, target=target, targeting_ally=True, is_double_battle=True)
        assert apply_flag7(ctx, ALWAYS) == 3

    def test_targeting_ally_earthquake_partner_levitate(self):
        move = _move(name="earthquake", type=PokeType.GROUND, cat=MoveCategory.PHYSICAL,
                     power=100)
        target = _poke(types=[PokeType.NORMAL], ability="Levitate")
        ctx = _ctx(move=move, target=target, targeting_ally=True, is_double_battle=True)
        assert apply_flag7(ctx, ALWAYS) == 2

    def test_targeting_enemy_highest_damage_always(self):
        move = _move(name="earthquake", power=100)
        ctx = _ctx(move=move, is_double_battle=True, targeting_ally=False,
                   move_damage_pcts={"earthquake": 80.0, "tackle": 30.0})
        assert apply_flag7(ctx, ALWAYS) == 3

    def test_targeting_enemy_not_highest_damage(self):
        move = _move(name="tackle", power=40)
        ctx = _ctx(move=move, is_double_battle=True, targeting_ally=False,
                   move_damage_pcts={"earthquake": 80.0, "tackle": 30.0})
        assert apply_flag7(ctx, ALWAYS) == 0


# ---------------------------------------------------------------------------
# score_move
# ---------------------------------------------------------------------------

class TestScoreMove:
    def test_empty_flags_returns_0(self):
        ctx = _ctx()
        assert score_move(ctx, [], rng=ALWAYS) == 0

    def test_runs_all_specified_flags(self):
        # A sleep move vs a target with Insomnia:
        # flag0 returns -10; flag3 with is_first_battle_turn=False returns 0
        move = _move(name="spore", cat=MoveCategory.STATUS, effect=MoveEffect.SLEEP)
        target = _poke(ability="Insomnia")
        ctx = _ctx(move=move, target=target, is_first_battle_turn=False)
        result = score_move(ctx, [0, 3], rng=ALWAYS)
        assert result == -10

    def test_flag0_and_flag4_combined(self):
        # Damaging Fire move vs Flash Fire: flag0 → -12; flag4 → +0 (not risky)
        move = _move(name="flamethrower", type=PokeType.FIRE, cat=MoveCategory.SPECIAL,
                     power=95, effect=MoveEffect.NONE)
        target = _poke(ability="Flash Fire")
        ctx = _ctx(move=move, target=target)
        result = score_move(ctx, [0, 4], rng=ALWAYS)
        assert result == -12

    def test_unknown_flag_id_ignored(self):
        ctx = _ctx()
        assert score_move(ctx, [99], rng=ALWAYS) == 0
