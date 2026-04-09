"""
Unit tests for battle_sim.py using a mock IPC — no Node.js simulator required.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from battle_sim import (
    parse_move_actions,
    parse_switch_actions,
    detect_winner,
    parse_ipc_response,
    assemble_team_string,
    assemble_opponent_string,
    reorder_team,
)

class MockIPC:
    """Provides canned IPC responses in order; records all requests sent."""

    def __init__(self, responses):
        self._queue = list(responses)
        self.last_sent = None
        self.all_sent = []

    def send(self, data):
        self.last_sent = data
        self.all_sent.append(data)
        if not self._queue:
            raise RuntimeError("MockIPC: response queue exhausted")
        return self._queue.pop(0)



def make_battle_state(p1_hp=0.8, p2_hp=0.8):
    """Minimal battle state dict with one Pokémon per side."""
    return {
        "sides": [
            {"pokemon": [{"hp": p1_hp, "maxhp": 1.0}]},
            {"pokemon": [{"hp": p2_hp, "maxhp": 1.0}]},
        ],
        "log": [],
    }


def make_response(p1_moves=None, p1_switches=None, p2_moves=None, p2_switches=None,
                  p1_hp=0.8, p2_hp=0.8):
    """Build a fake IPC response dict.

    Pass None for p1_moves (and p1_switches) to simulate a terminal state.
    """
    battle = make_battle_state(p1_hp=p1_hp, p2_hp=p2_hp)
    result = {"battle": battle}
    if p1_moves is not None:
        result["p1Moves"] = p1_moves
    if p1_switches is not None:
        result["p1Switches"] = p1_switches
    if p2_moves is not None:
        result["p2Moves"] = p2_moves
    if p2_switches is not None:
        result["p2Switches"] = p2_switches
    return {"result": result}


# Reusable canned responses
ACTIVE_RESPONSE = make_response(p1_moves="0:1", p2_moves="0")
TERMINAL_P1_WIN = make_response(p1_hp=0.6, p2_hp=0.0)   # no p1Moves → terminal, p1 wins
TERMINAL_P2_WIN = make_response(p1_hp=0.0, p2_hp=0.7)   # no p1Moves → terminal, p2 wins


# ---------------------------------------------------------------------------
# parse_move_actions
# ---------------------------------------------------------------------------

class TestParseMoveActions:
    def test_standard_format(self):
        result = parse_move_actions("0:1:2:3")
        assert result == ["move 1", "move 2", "move 3", "move 4"]

    def test_single_move(self):
        result = parse_move_actions("1")
        assert result == ["move 2"]

    def test_subset_of_moves(self):
        result = parse_move_actions("0:2")
        assert result == ["move 1", "move 3"]

    def test_fallback_on_empty_string(self):
        # Empty string means all PP depleted; fall back to move 1 (triggers Struggle)
        result = parse_move_actions("")
        assert result == ["move 1"]

    def test_fallback_on_unparseable(self):
        # Unparseable string similarly falls back to move 1
        result = parse_move_actions("???")
        assert result == ["move 1"]


# ---------------------------------------------------------------------------
# parse_switch_actions
# ---------------------------------------------------------------------------

class TestParseSwitchActions:
    def test_standard_format(self):
        result = parse_switch_actions("1:2")
        assert result == ["switch 2", "switch 3"]

    def test_single_switch(self):
        result = parse_switch_actions("3")
        assert result == ["switch 4"]

    def test_empty_string_returns_empty(self):
        assert parse_switch_actions("") == []

    def test_unparseable_returns_empty(self):
        assert parse_switch_actions("???") == []


# ---------------------------------------------------------------------------
# detect_winner
# ---------------------------------------------------------------------------

class TestDetectWinner:
    def test_p1_wins(self):
        assert detect_winner(make_battle_state(p1_hp=0.5, p2_hp=0.0)) == "p1"

    def test_p2_wins(self):
        assert detect_winner(make_battle_state(p1_hp=0.0, p2_hp=0.5)) == "p2"

    def test_both_alive_returns_none(self):
        assert detect_winner(make_battle_state(p1_hp=0.5, p2_hp=0.5)) is None

    def test_both_fainted_returns_none(self):
        assert detect_winner(make_battle_state(p1_hp=0.0, p2_hp=0.0)) is None

    def test_multi_pokemon_p1_wins(self):
        state = {
            "sides": [
                {"pokemon": [{"hp": 0}, {"hp": 0.6}]},   # p1 still has one alive
                {"pokemon": [{"hp": 0}, {"hp": 0}]},
            ]
        }
        assert detect_winner(state) == "p1"

    def test_empty_sides_returns_none(self):
        assert detect_winner({}) is None

    def test_missing_pokemon_key_returns_none(self):
        state = {"sides": [{"pokemon": []}, {"pokemon": []}]}
        assert detect_winner(state) is None


# ---------------------------------------------------------------------------
# parse_ipc_response
# ---------------------------------------------------------------------------

class TestParseIpcResponse:
    def test_active_state_not_over(self):
        response = make_response(p1_moves="0:1", p2_moves="0")
        state = parse_ipc_response(response)
        assert state["is_over"] is False
        assert state["winner"] is None
        assert state["p1_moves"] == ["move 1", "move 2"]

    def test_active_state_p2_moves_parsed(self):
        response = make_response(p1_moves="0", p2_moves="0:1")
        state = parse_ipc_response(response)
        assert state["p2_moves"] == ["move 1", "move 2"]

    def test_terminal_no_player_moves(self):
        response = make_response(p1_hp=0.5, p2_hp=0.0)   # no p1Moves key
        state = parse_ipc_response(response)
        assert state["is_over"] is True
        assert state["winner"] == "p1"
        assert state["p1_moves"] == []
        assert state["p1_switches"] == []

    def test_switches_only_not_terminal(self):
        response = make_response(p1_switches="1:2")
        state = parse_ipc_response(response)
        assert state["is_over"] is False
        assert state["p1_switches"] == ["switch 2", "switch 3"]
        assert state["p1_moves"] == []

    def test_battle_state_preserved(self):
        battle = make_battle_state(p1_hp=0.7, p2_hp=0.3)
        response = {"result": {"battle": battle, "p1Moves": "0"}}
        state = parse_ipc_response(response)
        assert state["battle"] is battle

    def test_no_p2_moves_when_absent(self):
        response = make_response(p1_moves="0")   # no p2Moves key
        state = parse_ipc_response(response)
        assert state["p2_moves"] == []
        assert state["p2_switches"] == []

    def test_opponent_switch_only_not_terminal(self):
        """Only p2Switches present — opponent must switch; battle is NOT over."""
        response = make_response(p2_switches="1:2")
        state = parse_ipc_response(response)
        assert state["is_over"] is False
        assert state["p2_switches"] == ["switch 2", "switch 3"]
        assert state["p1_moves"] == []
        assert state["p1_switches"] == []


# ---------------------------------------------------------------------------
# assemble_team_string
# ---------------------------------------------------------------------------

class TestAssembleTeamString:
    def test_single_pokemon(self):
        box = {"Pikachu": "Pikachu||Leftovers|Static|thunderbolt||"}
        assert assemble_team_string(["Pikachu"], box) == "Pikachu||Leftovers|Static|thunderbolt||"

    def test_multiple_pokemon_joined_by_bracket(self):
        box = {"A": "str_a", "B": "str_b", "C": "str_c"}
        assert assemble_team_string(["A", "B", "C"], box) == "str_a]str_b]str_c"

    def test_order_preserved(self):
        box = {"X": "x", "Y": "y"}
        assert assemble_team_string(["X", "Y"], box) == "x]y"
        assert assemble_team_string(["Y", "X"], box) == "y]x"


# ---------------------------------------------------------------------------
# assemble_opponent_string
# ---------------------------------------------------------------------------

class TestAssembleOpponentString:
    def test_requested_pokemon_leads(self):
        opp = {"Sceptile": "s", "Bellossom": "b", "Lapras": "l"}
        result = assemble_opponent_string("Bellossom", opp)
        assert result.startswith("b]")

    def test_already_first_unchanged(self):
        opp = {"Sceptile": "s", "Bellossom": "b"}
        result = assemble_opponent_string("Sceptile", opp)
        assert result.startswith("s]")

    def test_all_members_present(self):
        opp = {"A": "a", "B": "b", "C": "c"}
        result = assemble_opponent_string("B", opp)
        parts = result.split("]")
        assert set(parts) == {"a", "b", "c"}
        assert parts[0] == "b"


# ---------------------------------------------------------------------------
# reorder_team
# ---------------------------------------------------------------------------

class TestReorderTeam:
    def test_full_assignment_orders_by_opponent(self):
        team = ("Gligar", "Magneton", "Tyranitar", "Ariados", "Jirachi", "Nosepass")
        assignment = {
            "Gligar": "Bellossom", "Magneton": "Walrein",
            "Tyranitar": "Lapras", "Ariados": "Sceptile",
            "Jirachi": "Linoone", "Nosepass": "Noctowl",
        }
        opponents = ["Sceptile", "Bellossom", "Walrein", "Lapras", "Linoone", "Noctowl"]
        result = reorder_team(team, assignment, opponents)
        assert result == ["Ariados", "Gligar", "Magneton", "Tyranitar", "Jirachi", "Nosepass"]

    def test_all_members_preserved(self):
        team = ("A", "B", "C")
        assignment = {"A": "X", "B": "Y", "C": "Z"}
        result = reorder_team(team, assignment, ["X", "Y", "Z"])
        assert set(result) == {"A", "B", "C"}
        assert result == ["A", "B", "C"]

    def test_flex_members_appended_in_original_order(self):
        # 2 opponents, 3 team members — "B" is a flex slot
        team = ("A", "B", "C")
        assignment = {"A": "X", "C": "Z"}
        result = reorder_team(team, assignment, ["Z", "X"])
        assert result == ["C", "A", "B"]  # C→Z first, A→X second, B flex last


