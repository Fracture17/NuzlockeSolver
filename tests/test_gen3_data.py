"""Tests for gen3_data.py: move lookup and effect inference."""
import pytest
from gen3_data import get_move_info, _effect_from_fields
from ai_flags import MoveCategory, MoveEffect, PokeType


class TestGetMoveInfo:
    """Verify get_move_info returns correct MoveInfo for known Gen 3 moves."""

    def test_surf_lookup(self):
        mi = get_move_info("surf")
        assert mi is not None
        assert mi.type == PokeType.WATER
        assert mi.category == MoveCategory.SPECIAL
        assert mi.power == 95

    def test_tackle_lookup(self):
        mi = get_move_info("tackle")
        assert mi is not None
        assert mi.type == PokeType.NORMAL
        assert mi.category == MoveCategory.PHYSICAL
        assert mi.power == 35

    def test_unknown_id_returns_none(self):
        assert get_move_info("notamove_xyz") is None

    def test_case_sensitive(self):
        """get_move_info uses the key as-is; 'Surf' (capitalized) is not found."""
        assert get_move_info("Surf") is None


class TestEffectFromFields:
    """Verify _effect_from_fields infers the correct MoveEffect from PS move fields."""

    def test_ohko(self):
        result = _effect_from_fields("fissure", {"ohko": True, "category": "Physical"})
        assert result == MoveEffect.OHKO

    def test_selfdestruct(self):
        result = _effect_from_fields(
            "selfdestruct", {"selfdestruct": "always", "category": "Physical"}
        )
        assert result == MoveEffect.EXPLODE

    def test_rain_dance_weather(self):
        result = _effect_from_fields("raindance", {"weather": "RainDance"})
        assert result == MoveEffect.RAIN_DANCE

    def test_atk_down_one(self):
        result = _effect_from_fields(
            "growl", {"category": "Status", "boosts": {"atk": -1}}
        )
        assert result == MoveEffect.ATK_DOWN

    def test_atk_up_two(self):
        # Use a fake ID so the name table doesn't override field-based inference
        result = _effect_from_fields(
            "swordsdancefb", {"category": "Status", "boosts": {"atk": 2}}
        )
        assert result == MoveEffect.ATK_UP_2

    def test_sleep_status(self):
        result = _effect_from_fields(
            "spore", {"status": "slp", "category": "Status"}
        )
        assert result == MoveEffect.SLEEP

    def test_no_effect(self):
        result = _effect_from_fields("tackle", {"category": "Physical", "basePower": 35})
        assert result == MoveEffect.NONE
