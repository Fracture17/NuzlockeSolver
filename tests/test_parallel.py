"""Tests for parallel processing in battle_sim.

Tests use module-level picklable stubs (required for ProcessPoolExecutor —
MagicMock objects cannot be pickled across process boundaries).

Verifies:
  1. get_teams_at_level() applies per-pokemon level multipliers correctly.
  2. simulate_team with num_workers > 1 returns structurally valid results.
"""

import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from MatchupInfo import get_teams_at_level, _BOX_RAW, _OPP_RAW
from battle_sim import BattleResult, simulate_team


# ---------------------------------------------------------------------------
# Module-level picklable stubs (closures and MagicMocks can't cross processes)
# ---------------------------------------------------------------------------

def _fake_game_worker(node_script_path, player_str, opp_str,
                      mcts_iterations, depth_limit, record_path):
    """Picklable replacement for battle_sim._play_game_worker used in parallel tests."""
    return BattleResult(winner="p1", turns=5, log=[])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_level(pokemon_str: str) -> int:
    y = pokemon_str.split('|')
    raw = y[-2].strip()
    return int(raw) if raw else 100


# ---------------------------------------------------------------------------
# 1. get_teams_at_level
# ---------------------------------------------------------------------------

class TestGetTeamsAtLevel:

    def test_multiplier_one_gives_natural_levels(self):
        box, opp = get_teams_at_level(1.0)
        for raw in _BOX_RAW:
            y = raw.split('|')
            natural = int(y[-2]) if y[-2].strip() else 100
            name = raw.split('|')[0]
            assert _extract_level(box[name]) == natural, \
                f"{name}: expected {natural}, got {_extract_level(box[name])}"

    def test_multiplier_point_six(self):
        box, _ = get_teams_at_level(0.6)
        for raw in _BOX_RAW:
            y = raw.split('|')
            natural = int(y[-2]) if y[-2].strip() else 100
            expected = max(1, int(natural * 0.6))
            name = raw.split('|')[0]
            assert _extract_level(box[name]) == expected, \
                f"{name}: expected {expected}"

    #We expect no opponent multiplier, since only box should be affected
    def test_multiplier_applied_to_opponent(self):
        _, opp = get_teams_at_level(0.8)
        for raw in _OPP_RAW:
            y = raw.split('|')
            natural = int(y[-2]) if y[-2].strip() else 100
            expected = natural
            name = raw.split('|')[0]
            assert _extract_level(opp[name]) == expected

    def test_empty_level_defaults_to_100(self):
        box_raw_empty = [x for x in _BOX_RAW if x.split('|')[0] == "Nosepass"]
        if box_raw_empty:
            y = box_raw_empty[0].split('|')
            if not y[-2].strip():
                box, _ = get_teams_at_level(1.0)
                assert _extract_level(box["Nosepass"]) == 100

    def test_level_at_least_one(self):
        box, _ = get_teams_at_level(0.01)
        for name, poke_str in box.items():
            assert _extract_level(poke_str) >= 1

    def test_different_multipliers_give_different_levels(self):
        box06, _ = get_teams_at_level(0.6)
        box10, _ = get_teams_at_level(1.0)
        for raw in _BOX_RAW:
            name = raw.split('|')[0]
            if raw.split('|')[-2].strip():
                assert _extract_level(box06[name]) < _extract_level(box10[name])
                break

    def test_returns_dict_keyed_by_name(self):
        box, opp = get_teams_at_level(1.0)
        assert isinstance(box, dict)
        assert isinstance(opp, dict)
        for raw in _BOX_RAW:
            assert raw.split('|')[0] in box
        for raw in _OPP_RAW:
            assert raw.split('|')[0] in opp


# ---------------------------------------------------------------------------
# 2. simulate_team parallel — structural validity
# ---------------------------------------------------------------------------

class TestSimulateTeamParallel:
    """Test that simulate_team with num_workers > 1 returns valid BattleResult lists.

    Uses _fake_game_worker (module-level, picklable) instead of MagicMock
    so it can be pickled and sent to ProcessPoolExecutor worker processes.
    """

    def _fake_team_result(self):
        box, opp = get_teams_at_level(1.0)
        box_names = list(box.keys())
        opp_names = list(opp.keys())
        team = tuple(box_names[:6])
        assignment = {team[i]: opp_names[i] for i in range(min(6, len(opp_names)))}
        return (1.0, team, assignment)

    def test_parallel_returns_correct_count(self):
        box, opp = get_teams_at_level(1.0)
        team_result = self._fake_team_result()

        with patch("battle_sim._play_game_worker", new=_fake_game_worker):
            results = simulate_team(
                ipc=None,
                team_result=team_result,
                n_games=4,
                mcts_iterations=10,
                box=box,
                opp_dict=opp,
                node_script_path="dummy",
                num_workers=2,
            )
        assert len(results) == 4

    def test_parallel_results_are_battle_results(self):
        box, opp = get_teams_at_level(1.0)
        team_result = self._fake_team_result()

        with patch("battle_sim._play_game_worker", new=_fake_game_worker):
            results = simulate_team(
                ipc=None,
                team_result=team_result,
                n_games=3,
                mcts_iterations=10,
                box=box,
                opp_dict=opp,
                node_script_path="dummy",
                num_workers=3,
            )
        for r in results:
            assert isinstance(r, BattleResult)
            assert r.winner in {"p1", "p2", "unknown"}

    def test_sequential_fallback_when_num_workers_is_one(self):
        """num_workers=1 must use the passed ipc, never _play_game_worker.

        MagicMock is safe here because num_workers=1 never submits to a pool.
        """
        from unittest.mock import MagicMock
        from tests.test_battle_sim import MockIPC

        terminal = {
            "result": {
                "battle": {
                    "sides": [
                        {"pokemon": [{"hp": 0, "maxhp": 100}]},
                        {"pokemon": [{"hp": 100, "maxhp": 100}]},
                    ],
                    "log": [],
                },
            }
        }
        mock_ipc = MockIPC([terminal])
        box, opp = get_teams_at_level(1.0)
        team_result = self._fake_team_result()
        mock_worker = MagicMock()

        with patch("battle_sim._play_game_worker", mock_worker):
            try:
                simulate_team(
                    ipc=mock_ipc,
                    team_result=team_result,
                    n_games=1,
                    mcts_iterations=1,
                    box=box,
                    opp_dict=opp,
                    num_workers=1,
                )
            except Exception:
                pass
            mock_worker.assert_not_called()

    def test_parallel_n_games_determines_result_count(self):
        """Verifies one result is produced per game (replaces call_count check)."""
        box, opp = get_teams_at_level(1.0)
        team_result = self._fake_team_result()
        n = 5

        with patch("battle_sim._play_game_worker", new=_fake_game_worker):
            results = simulate_team(
                ipc=None,
                team_result=team_result,
                n_games=n,
                mcts_iterations=10,
                box=box,
                opp_dict=opp,
                node_script_path="dummy",
                num_workers=4,
            )
        assert len(results) == n
        assert all(r.winner in {"p1", "p2", "unknown"} for r in results)
