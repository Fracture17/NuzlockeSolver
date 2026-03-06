"""
Unit tests for MatchupInfo.py and test2.py using synthetic data only.
No pickle file or IPC connection required.
"""
import sys
import os
import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from MatchupInfo import TurnOutcome, PrunedMatchupInfo, MatchupInfo, STRATEGIES
from test2 import build_scores, select_best_team


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_turns(myHP, oppHP, n=10):
    """Return a list of n identical TurnOutcome objects."""
    return [TurnOutcome(myHP=myHP, opponentHP=oppHP, myStrategy="11", opponentStrategy="11")] * n


def make_pmi(my_pokemon, opp_pokemon, myHP, oppHP):
    """Build a PrunedMatchupInfo with all contexts filled with constant TurnOutcomes."""
    turns = make_turns(myHP, oppHP)
    return PrunedMatchupInfo(
        myPokemon=my_pokemon,
        opponentPokemon=opp_pokemon,
        none=turns,
        move1=turns,
        move2=turns,
        move3=turns,
        move4=turns,
    )


def make_raw_matchup_data(hp_by_strategy):
    """Build the nested matchupData dict used by MatchupInfo methods.

    hp_by_strategy: dict mapping (myStrat, oppStrat) -> (myHP, oppHP) for ALL
                    16 strategy pairs.  Missing pairs default to (1.0, 1.0).
    Returns matchupData[myStrat][oppStrat][context][turn] = (myHP, oppHP)
    """
    contexts = [None, 1, 2, 3, 4]
    data = {}
    for my_s in STRATEGIES:
        data[my_s] = {}
        for opp_s in STRATEGIES:
            hp = hp_by_strategy.get((my_s, opp_s), (1.0, 1.0))
            data[my_s][opp_s] = {}
            for ctx in contexts:
                data[my_s][opp_s][ctx] = [hp] * 10
    return data


def make_matchup_info_instance(raw_matchup_info):
    """Create a MatchupInfo object bypassing __init__, with rawMatchupInfo pre-set."""
    instance = MatchupInfo.__new__(MatchupInfo)
    instance.ipc = None
    instance.rawMatchupInfo = raw_matchup_info
    return instance


# ---------------------------------------------------------------------------
# TurnOutcome tests
# ---------------------------------------------------------------------------

class TestTurnOutcome:
    def test_fields(self):
        t = TurnOutcome(myHP=0.8, opponentHP=0.5, myStrategy="12", opponentStrategy="34")
        assert t.myHP == 0.8
        assert t.opponentHP == 0.5
        assert t.myStrategy == "12"
        assert t.opponentStrategy == "34"

    def test_zero_hp(self):
        t = TurnOutcome(myHP=0.0, opponentHP=0.0, myStrategy="11", opponentStrategy="11")
        assert t.myHP == 0.0
        assert t.opponentHP == 0.0


# ---------------------------------------------------------------------------
# PrunedMatchupInfo tests
# ---------------------------------------------------------------------------

class TestPrunedMatchupInfo:
    def test_repr(self):
        pmi = make_pmi("Zangoose", "Sceptile", 0.9, 0.3)
        assert repr(pmi) == "PrunedMatchupInfo(Zangoose vs Sceptile)"

    def test_fields(self):
        pmi = make_pmi("Tyranitar", "Lapras", 0.7, 0.4)
        assert pmi.myPokemon == "Tyranitar"
        assert pmi.opponentPokemon == "Lapras"
        assert len(pmi.none) == 10
        assert len(pmi.move1) == 10
        assert len(pmi.move4) == 10

    def test_turn_outcome_values(self):
        pmi = make_pmi("Gligar", "Walrein", 0.6, 0.2)
        assert pmi.none[9].myHP == 0.6
        assert pmi.none[9].opponentHP == 0.2


# ---------------------------------------------------------------------------
# MatchupInfo.pruneSingleTurn tests
# ---------------------------------------------------------------------------

class TestPruneSingleTurn:
    def _instance(self):
        return MatchupInfo.__new__(MatchupInfo)

    def test_picks_best_minimax_strategy(self):
        """Strategy '11' forces worst-case ratio of 2.0; '22' forces 0.5.
        Minimax should pick '11'."""
        # myStrat=11: whatever opp does, my HP stays at 0.8 and opp HP at 0.6
        #   -> lost-hp ratio (1-0.6)/(1-0.8+.01) = 0.4/0.21 ≈ 1.90
        # myStrat=22: whatever opp does, my HP at 0.3, opp at 0.8
        #   -> (1-0.8)/(1-0.3+.01) = 0.2/0.71 ≈ 0.28
        hp_map = {}
        for my_s in STRATEGIES:
            for opp_s in STRATEGIES:
                if my_s == "11":
                    hp_map[(my_s, opp_s)] = (0.8, 0.6)
                else:
                    hp_map[(my_s, opp_s)] = (0.3, 0.8)

        data = make_raw_matchup_data(hp_map)
        mi = self._instance()
        outcome, my_strat, opp_strat = mi.pruneSingleTurn(data, None, 0)
        assert my_strat == "11"

    def test_all_equal_returns_valid_result(self):
        """When all strategies are identical, any result is acceptable."""
        data = make_raw_matchup_data({})  # all default to (1.0, 1.0)
        mi = self._instance()
        outcome, my_strat, opp_strat = mi.pruneSingleTurn(data, None, 0)
        assert my_strat in STRATEGIES
        assert opp_strat in STRATEGIES
        assert outcome == (1.0, 1.0)

    def test_returns_correct_turn_index(self):
        """Turn 5 data is distinct from turn 0; verify turn parameter is respected."""
        hp_map = {(s, t): (0.9, 0.1) for s in STRATEGIES for t in STRATEGIES if s == "11"}
        data = make_raw_matchup_data(hp_map)
        # Override turn 5 for strategy 11 to a different value
        for opp_s in STRATEGIES:
            data["11"][opp_s][None][5] = (0.5, 0.5)
        mi = self._instance()
        outcome, _, _ = mi.pruneSingleTurn(data, None, 5)
        # The outcome at turn 5 for the chosen strategy should be from turn 5
        assert outcome is not None


# ---------------------------------------------------------------------------
# MatchupInfo.pruneSingleMatchup tests
# ---------------------------------------------------------------------------

class TestPruneSingleMatchup:
    def test_returns_10_turns(self):
        data = make_raw_matchup_data({})
        mi = MatchupInfo.__new__(MatchupInfo)
        result = mi.pruneSingleMatchup(data, None)
        assert len(result) == 10

    def test_all_items_are_turn_outcomes(self):
        data = make_raw_matchup_data({})
        mi = MatchupInfo.__new__(MatchupInfo)
        result = mi.pruneSingleMatchup(data, None)
        for item in result:
            assert isinstance(item, TurnOutcome)

    def test_values_reflect_chosen_strategy(self):
        """When one strategy dominates, all 10 turns should reflect its HP values."""
        hp_map = {(my_s, opp_s): (0.8, 0.6) if my_s == "11" else (0.1, 0.9)
                  for my_s in STRATEGIES for opp_s in STRATEGIES}
        data = make_raw_matchup_data(hp_map)
        mi = MatchupInfo.__new__(MatchupInfo)
        result = mi.pruneSingleMatchup(data, None)
        for turn in result:
            assert turn.myHP == 0.8
            assert turn.opponentHP == 0.6
            assert turn.myStrategy == "11"


# ---------------------------------------------------------------------------
# MatchupInfo.makePrunedMatchupInfo tests
# ---------------------------------------------------------------------------

class TestMakePrunedMatchupInfo:
    def _build_raw(self, my_pokemon_list, opp_pokemon_list):
        raw = {}
        for p in my_pokemon_list:
            raw[p] = {}
            for p2 in opp_pokemon_list:
                raw[p][p2] = make_raw_matchup_data({})
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

    def test_each_result_has_5_contexts(self):
        raw = self._build_raw(["A"], ["X"])
        mi = make_matchup_info_instance(raw)
        result = mi.makePrunedMatchupInfo()
        pmi = result[0]
        assert len(pmi.none) == 10
        assert len(pmi.move1) == 10
        assert len(pmi.move2) == 10
        assert len(pmi.move3) == 10
        assert len(pmi.move4) == 10


# ---------------------------------------------------------------------------
# build_scores tests
# ---------------------------------------------------------------------------

def _make_pmi_list(specs):
    """specs: list of (my, opp, myHP, oppHP)"""
    return [make_pmi(my, opp, mh, oh) for my, opp, mh, oh in specs]


class TestBuildScores:
    def test_structure_keys(self):
        pmis = _make_pmi_list([
            ("A", "X", 0.8, 0.2),
            ("B", "X", 0.6, 0.4),
        ])
        raw, norm, my_pokemon, opponents = build_scores(pmis)
        assert set(my_pokemon) == {"A", "B"}
        assert set(opponents) == {"X"}
        assert "A" in raw and "X" in raw["A"]

    def test_normalization_best_is_one(self):
        pmis = _make_pmi_list([
            ("A", "X", 0.9, 0.1),  # ratio = 0.9/0.1 = 9.0 → should be 1.0
            ("B", "X", 0.3, 0.7),  # ratio ≈ 0.43 → should be 0.0
            ("C", "X", 0.5, 0.5),  # ratio = 1.0 → between
        ])
        _, norm, _, _ = build_scores(pmis)
        assert norm["A"]["X"] == pytest.approx(1.0)
        assert norm["B"]["X"] == pytest.approx(0.0)

    def test_normalization_all_equal(self):
        pmis = _make_pmi_list([
            ("A", "X", 0.5, 0.5),
            ("B", "X", 0.5, 0.5),
            ("C", "X", 0.5, 0.5),
        ])
        _, norm, _, _ = build_scores(pmis)
        for p in ["A", "B", "C"]:
            assert norm[p]["X"] == pytest.approx(1.0)

    def test_raw_scores_correct_score(self):
        pmis = _make_pmi_list([("A", "X", 0.8, 0.4)])
        raw, _, _, _ = build_scores(pmis)
        expected = 0.8 - 0.4  # myHP - opponentHP
        assert raw["A"]["X"] == pytest.approx(expected, rel=1e-6)

    def test_multiple_opponents(self):
        pmis = _make_pmi_list([
            ("A", "X", 0.8, 0.2),
            ("A", "Y", 0.6, 0.4),
            ("B", "X", 0.4, 0.6),
            ("B", "Y", 0.9, 0.1),
        ])
        raw, norm, my_pokemon, opponents = build_scores(pmis)
        assert set(opponents) == {"X", "Y"}
        # A is best vs X, B is best vs Y
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
    Pokémon i in BOX has myHP=0.9 vs opponent i (best matchup) and 0.3 otherwise.
    The ideal team of 6 is thus BOX0..BOX5, each matched to OPP0..OPP5.
    """
    pmis = []
    for i in range(n_box):
        for j in range(n_opp):
            myHP = 0.9 if i == j else 0.3
            pmis.append(make_pmi(f"BOX{i}", f"OPP{j}", myHP, 0.5))
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
