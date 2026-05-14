"""
Unit tests for MatchupInfo.py and team_scoring.py using synthetic data only.
No pickle file or IPC connection required.
"""
import sys
import os
import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from MatchupInfo import MatchupResult, PrunedMatchupInfo, MatchupInfo
from team_scoring import build_scores, select_best_team


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_result(score=0.5, action_stats=None):
    """Return a MatchupResult with the given score and optional action_stats."""
    return MatchupResult(score=score, action_stats=action_stats or {}, num_runs=1, variance=0.0)


def make_pmi(my_pokemon, opp_pokemon, score, action_stats=None):
    """Build a PrunedMatchupInfo with all contexts filled with the same MatchupResult."""
    result = make_result(score, action_stats)
    return PrunedMatchupInfo(
        myPokemon=my_pokemon,
        opponentPokemon=opp_pokemon,
        none=result,
        move1=result,
        move2=result,
        move3=result,
        move4=result,
    )


def make_raw_context(score=0.5):
    """Build a raw context dict {None: MatchupResult, 1-4: MatchupResult}."""
    return {ctx: make_result(score) for ctx in [None, 1, 2, 3, 4]}


def make_matchup_info_instance(raw_matchup_info):
    """Create a MatchupInfo object bypassing __init__, with rawMatchupInfo pre-set."""
    instance = MatchupInfo.__new__(MatchupInfo)
    instance.rawMatchupInfo = raw_matchup_info
    return instance


# ---------------------------------------------------------------------------
# MatchupResult tests
# ---------------------------------------------------------------------------

class TestMatchupResult:
    def test_fields(self):
        r = MatchupResult(score=0.75, action_stats={"move 1": 0.8, "move 2": 0.4}, num_runs=5, variance=0.1)
        assert r.score == pytest.approx(0.75)
        assert r.action_stats["move 1"] == pytest.approx(0.8)

    def test_empty_action_stats(self):
        r = MatchupResult(score=0.0, action_stats={}, num_runs=1, variance=0.0)
        assert r.score == 0.0
        assert r.action_stats == {}


# ---------------------------------------------------------------------------
# PrunedMatchupInfo tests
# ---------------------------------------------------------------------------

class TestPrunedMatchupInfo:
    def test_repr(self):
        pmi = make_pmi("Zangoose", "Sceptile", 0.9)
        assert repr(pmi) == "PrunedMatchupInfo(Zangoose vs Sceptile)"

    def test_fields(self):
        pmi = make_pmi("Tyranitar", "Lapras", 0.7)
        assert pmi.myPokemon == "Tyranitar"
        assert pmi.opponentPokemon == "Lapras"
        assert isinstance(pmi.none, MatchupResult)
        assert isinstance(pmi.move1, MatchupResult)
        assert isinstance(pmi.move4, MatchupResult)

    def test_score_value(self):
        pmi = make_pmi("Gligar", "Walrein", 0.6)
        assert pmi.none.score == pytest.approx(0.6)
        assert pmi.move1.score == pytest.approx(0.6)

    def test_action_stats_stored(self):
        stats = {"move 1": 1.2, "switch 2": -0.5}
        pmi = make_pmi("Zangoose", "Sceptile", 0.5, action_stats=stats)
        assert pmi.none.action_stats == stats


# ---------------------------------------------------------------------------
# MatchupInfo.makePrunedMatchupInfo tests
# ---------------------------------------------------------------------------

class TestMakePrunedMatchupInfo:
    def _build_raw(self, my_pokemon_list, opp_pokemon_list, score=0.5):
        raw = {}
        for p in my_pokemon_list:
            raw[p] = {}
            for p2 in opp_pokemon_list:
                raw[p][p2] = make_raw_context(score)
        return raw

    def test_count(self):
        raw = self._build_raw(["A", "B"], ["X", "Y"])
        mi = make_matchup_info_instance(raw)
        result = mi.makePrunedMatchupInfo()
        assert len(result) == 4  # 2 × 2

    def test_pokemon_names(self):
        raw = self._build_raw(["Tyranitar", "Gligar"], ["Sceptile", "Lapras"])
        mi = make_matchup_info_instance(raw)
        result = mi.makePrunedMatchupInfo()
        my_names = {pmi.myPokemon for pmi in result}
        opp_names = {pmi.opponentPokemon for pmi in result}
        assert my_names == {"Tyranitar", "Gligar"}
        assert opp_names == {"Sceptile", "Lapras"}

    def test_each_result_has_matchup_results(self):
        raw = self._build_raw(["A"], ["X"], score=0.42)
        mi = make_matchup_info_instance(raw)
        result = mi.makePrunedMatchupInfo()
        pmi = result[0]
        assert isinstance(pmi.none, MatchupResult)

    def test_score_preserved(self):
        raw = self._build_raw(["A"], ["X"], score=0.77)
        mi = make_matchup_info_instance(raw)
        result = mi.makePrunedMatchupInfo()
        assert result[0].none.score == pytest.approx(0.77)


# ---------------------------------------------------------------------------
# build_scores tests
# ---------------------------------------------------------------------------

def _make_pmi_list(specs):
    """specs: list of (my, opp, score)"""
    return [make_pmi(my, opp, score) for my, opp, score in specs]


class TestBuildScores:
    def test_structure_keys(self):
        pmis = _make_pmi_list([
            ("A", "X", 0.8),
            ("B", "X", 0.6),
        ])
        raw, norm, my_pokemon, opponents = build_scores(pmis)
        assert set(my_pokemon) == {"A", "B"}
        assert set(opponents) == {"X"}
        assert "A" in raw and "X" in raw["A"]

    def test_normalization_best_is_one(self):
        pmis = _make_pmi_list([
            ("A", "X", 0.9),  # highest → normalized 1.0
            ("B", "X", 0.3),  # lowest  → normalized 0.0
            ("C", "X", 0.5),  # middle
        ])
        _, norm, _, _ = build_scores(pmis)
        assert norm["A"]["X"] == pytest.approx(1.0)
        assert norm["B"]["X"] == pytest.approx(0.0)

    def test_normalization_all_equal(self):
        pmis = _make_pmi_list([
            ("A", "X", 0.5),
            ("B", "X", 0.5),
            ("C", "X", 0.5),
        ])
        _, norm, _, _ = build_scores(pmis)
        for p in ["A", "B", "C"]:
            assert norm[p]["X"] == pytest.approx(1.0)

    def test_raw_scores_correct_score(self):
        pmis = _make_pmi_list([("A", "X", 0.8)])
        raw, _, _, _ = build_scores(pmis)
        assert raw["A"]["X"] == pytest.approx(0.8)

    def test_multiple_opponents(self):
        pmis = _make_pmi_list([
            ("A", "X", 0.8),
            ("A", "Y", 0.6),
            ("B", "X", 0.4),
            ("B", "Y", 0.9),
        ])
        raw, norm, my_pokemon, opponents = build_scores(pmis)
        assert set(opponents) == {"X", "Y"}
        assert norm["A"]["X"] == pytest.approx(1.0)
        assert norm["B"]["Y"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# select_best_team tests
# ---------------------------------------------------------------------------

class _MockM:
    """Minimal stand-in for MatchupInfo with prunedMatchupInfo."""
    def __init__(self, pmi_list):
        self.prunedMatchupInfo = pmi_list


def _make_mock_m(n_box=7, n_opp=6):
    """Build a mock MatchupInfo with n_box BOX Pokémon and n_opp opponents.
    Pokémon i in BOX scores 0.9 vs opponent i (best matchup) and 0.3 otherwise.
    The ideal team of 6 is thus BOX0..BOX5, each matched to OPP0..OPP5.
    """
    pmis = []
    for i in range(n_box):
        for j in range(n_opp):
            score = 0.9 if i == j else 0.3
            pmis.append(make_pmi(f"BOX{i}", f"OPP{j}", score))
    return _MockM(pmis)


class TestSelectBestTeam:
    def test_returns_n(self):
        mock = _make_mock_m()
        result = select_best_team(mock, 3)
        assert len(result) == 3

    def test_sorted_descending(self):
        mock = _make_mock_m()
        result = select_best_team(mock, 5)
        scores = [r[0] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_team_size(self):
        mock = _make_mock_m()
        result = select_best_team(mock, 1)
        score, team, assignment = result[0]
        assert len(team) == 6

    def test_assignment_keys_match_team(self):
        mock = _make_mock_m()
        result = select_best_team(mock, 1)
        score, team, assignment = result[0]
        assert set(assignment.keys()) == set(team)

    def test_assignment_values_are_opponents(self):
        mock = _make_mock_m()
        _, norm, _, opponents = build_scores(mock.prunedMatchupInfo)
        result = select_best_team(mock, 1)
        score, team, assignment = result[0]
        for opp in assignment.values():
            assert opp in opponents

    def test_assignment_is_bijection(self):
        """Each opponent should appear at most once in the assignment."""
        mock = _make_mock_m()
        result = select_best_team(mock, 1)
        score, team, assignment = result[0]
        assert len(set(assignment.values())) == 6

    def test_optimal_team_is_top(self):
        """With 7 BOX Pokémon where BOX0-5 each dominate one opponent,
        the best team must include BOX0 through BOX5."""
        mock = _make_mock_m(n_box=7, n_opp=6)
        result = select_best_team(mock, 1)
        score, team, assignment = result[0]
        assert set(team) == {f"BOX{i}" for i in range(6)}
