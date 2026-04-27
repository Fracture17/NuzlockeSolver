"""Tests for berry_logic.py: most_common_status, assign_berry, best_berry_for_flex."""
import pytest
from berry_logic import most_common_status, assign_berry, best_berry_for_flex
from item_meta import STATUS_TO_BERRY, DEFAULT_BERRY


class TestMostCommonStatus:
    """Verify most_common_status returns correct status with tiebreaking."""

    def test_empty_list(self):
        assert most_common_status([]) is None

    def test_single_status(self):
        assert most_common_status(["psn"]) == "psn"

    def test_plurality(self):
        assert most_common_status(["psn", "psn", "slp"]) == "psn"

    def test_tiebreak_first_occurrence(self):
        # Equal count: 'slp' appears first, so it wins
        assert most_common_status(["slp", "psn"]) == "slp"

    def test_tiebreak_first_occurrence_reversed(self):
        # Equal count: 'psn' appears first, so it wins
        assert most_common_status(["psn", "slp"]) == "psn"


class TestAssignBerry:
    """Verify assign_berry maps each opponent to the correct cure berry."""

    def test_poison_gets_pecha(self):
        result = assign_berry({"Sceptile": ["psn", "psn"]})
        assert result == {"Sceptile": STATUS_TO_BERRY["psn"]}

    def test_empty_statuses_gets_default(self):
        result = assign_berry({"Rayquaza": []})
        assert result == {"Rayquaza": DEFAULT_BERRY}

    def test_multiple_opponents(self):
        result = assign_berry({
            "Breloom": ["slp", "slp", "psn"],
            "Blaziken": ["brn", "brn"],
        })
        assert result == {
            "Breloom": STATUS_TO_BERRY["slp"],
            "Blaziken": STATUS_TO_BERRY["brn"],
        }

    def test_unknown_status_gets_default(self):
        # Status not in STATUS_TO_BERRY falls back to DEFAULT_BERRY
        result = assign_berry({"Flygon": ["xyz", "xyz"]})
        assert result == {"Flygon": DEFAULT_BERRY}


class TestBestBerryForFlex:
    """Verify best_berry_for_flex returns the berry for the highest-scoring opponent."""

    def test_returns_berry_of_highest_scoring_opp(self):
        scores = {"Breloom": 3.5, "Blaziken": 1.2}
        berry_map = {
            "Breloom": STATUS_TO_BERRY["slp"],
            "Blaziken": STATUS_TO_BERRY["brn"],
        }
        result = best_berry_for_flex(scores, berry_map)
        assert result == STATUS_TO_BERRY["slp"]

    def test_empty_inputs_returns_default(self):
        assert best_berry_for_flex({}, {}) == DEFAULT_BERRY

    def test_missing_opp_in_berry_map(self):
        # best_opp resolved from scores, but not present in berry_map → DEFAULT_BERRY
        scores = {"Flygon": 5.0}
        result = best_berry_for_flex(scores, {})
        assert result == DEFAULT_BERRY
