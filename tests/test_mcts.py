"""
Tests for the generic MCTS implementation (mcts.py).

Uses a minimal concrete subclass backed by simple deterministic games
so tests run without any external dependencies (no IPC, no Pokémon data).
"""

import pytest
from mcts import MCTS, MCTSNode


# ---------------------------------------------------------------------------
# Minimal concrete MCTS subclass for testing
# ---------------------------------------------------------------------------

class ChainMCTS(MCTS):
    """
    A trivial single-player game: a chain of integers.
    State: int (0 = start, TERMINAL_STATE = terminal)
    Actions: list of strings, e.g. ["move 1", "move 2", "switch 3"]
    Applying action "X Y" transitions to a child state whose reward is
    determined by `rewards` dict keyed on action string.
    """

    TERMINAL_STATE = -1

    def __init__(self, actions, rewards, **kwargs):
        """
        Args:
            actions: list of action strings available at the root state.
            rewards: dict mapping action -> reward float when that action
                     leads directly to a terminal state.
        """
        super().__init__(**kwargs)
        self.actions = actions
        self.rewards = rewards

    def get_legal_actions(self, state):
        if state == self.TERMINAL_STATE:
            return []
        return list(self.actions)

    def apply_action(self, state, action):
        # Each action leads straight to a terminal state.
        return self.TERMINAL_STATE

    def is_terminal(self, state):
        return state == self.TERMINAL_STATE

    def get_reward(self, state, player=1):
        # We stash the last action used in a side channel for reward lookup.
        # Because apply_action always goes to terminal, the simulation
        # terminates immediately and get_reward is called on TERMINAL_STATE.
        # We can't distinguish which action led here inside get_reward alone,
        # so we override _simulate to return the correct reward directly.
        return 0.5


class DirectRewardMCTS(ChainMCTS):
    """
    Override _simulate so each child's reward is determined by its action.
    apply_action leads straight to terminal, so simulation returns
    rewards[action] if known, else 0.5.
    """

    def apply_action(self, state, action):
        # Return a state that encodes the chosen action so get_reward works.
        return ("terminal", action)

    def is_terminal(self, state):
        return isinstance(state, tuple) and state[0] == "terminal"

    def get_legal_actions(self, state):
        if self.is_terminal(state):
            return []
        return list(self.actions)

    def get_reward(self, state, player=1):
        if isinstance(state, tuple) and state[0] == "terminal":
            return self.rewards.get(state[1], 0.5)
        return 0.5


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMCTSNodeUCB1:

    def test_unvisited_node_returns_inf(self):
        parent = MCTSNode(state=0)
        parent.visits = 5
        child = MCTSNode(state=1, parent=parent)
        assert child.ucb1() == float("inf")

    def test_visited_node_finite(self):
        parent = MCTSNode(state=0)
        parent.visits = 10
        child = MCTSNode(state=1, parent=parent)
        child.visits = 3
        child.value = 2.0
        val = child.ucb1()
        assert val != float("inf")
        assert val > 0

    def test_higher_value_scores_higher(self):
        parent = MCTSNode(state=0)
        parent.visits = 10
        good = MCTSNode(state=1, parent=parent)
        good.visits = 3
        good.value = 3.0
        bad = MCTSNode(state=1, parent=parent)
        bad.visits = 3
        bad.value = 0.0
        assert good.ucb1() > bad.ucb1()


class TestMCTSNodeHelpers:

    def test_tried_actions_empty_on_init(self):
        node = MCTSNode(state=0)
        assert node.tried_actions == set()

    def test_tried_actions_is_set(self):
        node = MCTSNode(state=0)
        node.tried_actions.add("move 1")
        assert "move 1" in node.tried_actions
        assert "move 2" not in node.tried_actions

    def test_no_state_on_child_node(self):
        parent = MCTSNode(state=42)
        child = MCTSNode(state=None, parent=parent, action="move 1")
        assert child.state is None
        assert child.action == "move 1"

    def test_best_child_returns_highest_ucb1(self):
        root = MCTSNode(state=0)
        root.visits = 10
        child_a = MCTSNode(state=1, parent=root)
        child_a.visits = 5
        child_a.value = 5.0  # exploitation = 1.0
        child_b = MCTSNode(state=2, parent=root)
        child_b.visits = 5
        child_b.value = 0.0  # exploitation = 0.0
        root.children = [child_a, child_b]
        assert root.best_child() is child_a


class TestBestActionObviousWinner:
    """MCTS should reliably select the action that always yields reward 1.0."""

    def test_obvious_winner_selected(self):
        actions = ["move 1", "move 2", "move 3"]
        rewards = {"move 1": 0.0, "move 2": 1.0, "move 3": 0.0}
        mcts = DirectRewardMCTS(actions, rewards)
        result = mcts.get_best_action(0, iterations=30)
        assert result == "move 2"

    def test_obvious_winner_two_actions(self):
        actions = ["move 1", "switch 2"]
        rewards = {"move 1": 1.0, "switch 2": 0.0}
        mcts = DirectRewardMCTS(actions, rewards)
        result = mcts.get_best_action(0, iterations=20)
        assert result == "move 1"


class TestTiebreakBehavior:
    """When all actions have equal reward, MCTS returns a valid action."""

    def test_equal_rewards_returns_some_action(self):
        actions = ["move 1", "move 2", "switch 3", "switch 4"]
        rewards = {a: 0.5 for a in actions}
        mcts = DirectRewardMCTS(actions, rewards)
        result = mcts.get_best_action(0, iterations=20)
        assert result in actions

    def test_equal_rewards_returns_move_not_last_switch(self):
        """After the pop(0) fix, equal-reward actions should NOT systematically
        return the last switch (old biased behaviour)."""
        actions = ["move 1", "move 2", "switch 3", "switch 4"]
        rewards = {a: 0.5 for a in actions}
        mcts = DirectRewardMCTS(actions, rewards)
        # Run many times; the last switch should not win every single time.
        results = [mcts.get_best_action(0, iterations=len(actions)) for _ in range(20)]
        assert "switch 4" not in results or set(results) != {"switch 4"}, (
            "MCTS always returned last switch — expansion bias is still present"
        )


class TestZeroIterationsNoCrash:
    """get_best_action with iterations=0 must not crash."""

    def test_zero_iterations_single_action(self):
        mcts = DirectRewardMCTS(["move 1"], {"move 1": 1.0})
        result = mcts.get_best_action(0, iterations=0)
        # With 0 iterations no children are expanded; fallback returns first legal action.
        assert result == "move 1"

    def test_zero_iterations_multiple_actions(self):
        actions = ["move 1", "move 2", "switch 3"]
        mcts = DirectRewardMCTS(actions, {})
        result = mcts.get_best_action(0, iterations=0)
        assert result in actions

    def test_zero_iterations_returns_first_action(self):
        """Fallback should return the first legal action."""
        actions = ["move 1", "switch 4"]
        mcts = DirectRewardMCTS(actions, {})
        result = mcts.get_best_action(0, iterations=0)
        assert result == "move 1"


class TestTerminalInitialStateNoCrash:
    """search() on an already-terminal state must not crash."""

    def test_terminal_state_returns_none(self):
        mcts = DirectRewardMCTS([], {})
        action, root = mcts.search(("terminal", "move 1"), iterations=10)
        assert action is None

    def test_terminal_state_root_has_no_children(self):
        mcts = DirectRewardMCTS([], {})
        action, root = mcts.search(("terminal", "move 1"), iterations=10)
        # No expansion possible — root has no children.
        assert root.children == []


class TestVisitCounts:
    """After N iterations root.visits should equal N."""

    def test_visit_count_equals_iterations(self):
        actions = ["move 1", "move 2"]
        rewards = {"move 1": 1.0, "move 2": 0.0}
        mcts = DirectRewardMCTS(actions, rewards)
        _, root = mcts.search(0, iterations=15)
        assert root.visits == 15

    def test_visit_count_one_iteration(self):
        mcts = DirectRewardMCTS(["move 1"], {"move 1": 1.0})
        _, root = mcts.search(0, iterations=1)
        assert root.visits == 1

    def test_visit_count_many_iterations(self):
        actions = ["move 1", "move 2", "move 3"]
        rewards = {a: 0.5 for a in actions}
        mcts = DirectRewardMCTS(actions, rewards)
        n = 50
        _, root = mcts.search(0, iterations=n)
        assert root.visits == n


class TestExpansionOrder:
    """Children should be appended in get_legal_actions order (pop(0) fix)."""

    def test_first_child_corresponds_to_first_action(self):
        actions = ["move 1", "move 2", "switch 3"]
        rewards = {a: 0.5 for a in actions}
        mcts = DirectRewardMCTS(actions, rewards)
        # One iteration expands exactly one child.
        _, root = mcts.search(0, iterations=1)
        assert len(root.children) == 1
        assert root.children[0].action == "move 1"

    def test_second_expansion_uses_second_action(self):
        actions = ["move 1", "move 2", "switch 3"]
        rewards = {a: 0.5 for a in actions}
        mcts = DirectRewardMCTS(actions, rewards)
        _, root = mcts.search(0, iterations=2)
        assert len(root.children) >= 2
        actions_expanded = [c.action for c in root.children[:2]]
        assert actions_expanded == ["move 1", "move 2"]

    def test_all_actions_expanded_in_order(self):
        actions = ["move 1", "move 2", "switch 3", "switch 4"]
        rewards = {a: 0.5 for a in actions}
        mcts = DirectRewardMCTS(actions, rewards)
        _, root = mcts.search(0, iterations=len(actions))
        child_actions = [c.action for c in root.children]
        assert child_actions == actions


class TestGetActionStatistics:
    """get_action_statistics returns sorted list of (action, visits, avg_value)."""

    def test_returns_one_entry_per_child(self):
        actions = ["move 1", "move 2"]
        rewards = {"move 1": 1.0, "move 2": 0.0}
        mcts = DirectRewardMCTS(actions, rewards)
        _, root = mcts.search(0, iterations=10)
        stats = mcts.get_action_statistics(root)
        assert len(stats) == len(root.children)

    def test_sorted_by_visits_descending(self):
        actions = ["move 1", "move 2"]
        rewards = {"move 1": 1.0, "move 2": 0.0}
        mcts = DirectRewardMCTS(actions, rewards)
        _, root = mcts.search(0, iterations=20)
        stats = mcts.get_action_statistics(root)
        visits = [s[1] for s in stats]
        assert visits == sorted(visits, reverse=True)

    def test_stat_tuple_structure(self):
        mcts = DirectRewardMCTS(["move 1"], {"move 1": 1.0})
        _, root = mcts.search(0, iterations=5)
        stats = mcts.get_action_statistics(root)
        assert len(stats) == 1
        action, visits, avg_value = stats[0]
        assert action == "move 1"
        assert visits > 0
        assert 0.0 <= avg_value <= 1.0
