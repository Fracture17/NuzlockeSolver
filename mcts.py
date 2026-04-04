"""
Generic Monte Carlo Tree Search (MCTS) implementation.

This module provides a reusable MCTS algorithm that can be applied to various
sequential decision-making problems. Users must subclass MCTS and implement
the abstract methods for their specific domain.
"""

import math
import random
import threading
from abc import ABC, abstractmethod
from typing import Any, List, Optional, Tuple


class MCTSNode:
    """
    A node in the Monte Carlo Search Tree.

    Attributes:
        state: The game/problem state at this node (only populated for root)
        parent: Parent node (None for root)
        action: Action taken from parent to reach this node
        children: List of child nodes
        visits: Number of times this node has been visited
        value: Total value accumulated from simulations
        tried_actions: Set of actions that have been tried from this node
    """

    def __init__(self, state: Any = None, parent: Optional['MCTSNode'] = None,
                 action: Any = None):
        self.state = state
        self.parent = parent
        self.action = action
        self.children: List[MCTSNode] = []
        self.visits = 0
        self.value = 0.0
        self.tried_actions: set = set()
        self.lock = threading.Lock()

    #Returns None when node has no children, likely due to parallel workers
    def best_child(self, exploration_weight: float = 1.414,
                   action_penalty_fn=None,
                   exclude_fn=None) -> 'MCTSNode':
        """
        Select the best child using UCB1 formula.

        Args:
            exploration_weight: UCB1 exploration parameter (default sqrt(2))
            action_penalty_fn: Optional callable(action) -> float added to UCB1 for
                               comparison only. Does not affect stored values.
            exclude_fn: Optional callable(action) -> bool; children for which this
                        returns True are removed from consideration entirely.

        Returns:
            Child node with highest UCB1 value
        """
        #The only way this could called with no children is if the untried actions list is empty and the other workers aren't done yet
        #So just return None in that case
        if len(self.children) == 0:
            return None

        candidates = (self.children if exclude_fn is None
                      else [c for c in self.children if not exclude_fn(c.action)])
        if not candidates:
            return None

        def score(child):
            ucb = child.ucb1(exploration_weight)
            if action_penalty_fn is not None:
                ucb += action_penalty_fn(child.action)
            return ucb

        return max(candidates, key=score)

    def ucb1(self, exploration_weight: float = 1.414) -> float:
        """
        Calculate UCB1 (Upper Confidence Bound) value.

        UCB1 = exploitation + exploration
             = (value / visits) + c * sqrt(ln(parent_visits) / visits)

        Args:
            exploration_weight: Exploration constant (typically sqrt(2))

        Returns:
            UCB1 value for this node
        """
        self.lock.acquire()

        if self.visits == 0:
            return float('inf')

        exploitation = self.value / self.visits
        exploration = exploration_weight * math.sqrt(
            math.log(self.parent.visits) / self.visits
        )
        #scale exploration
        exploration *= 8

        self.lock.release()

        return exploitation + exploration


class MCTS(ABC):
    """
    Generic Monte Carlo Tree Search implementation.

    Subclass this and implement the abstract methods for your specific problem.
    """

    def __init__(self, exploration_weight: float = 1.414,
                 simulation_depth_limit: int = 100):
        """
        Initialize MCTS.

        Args:
            exploration_weight: UCB1 exploration parameter (default sqrt(2))
            simulation_depth_limit: Max depth for simulations to prevent infinite loops
        """
        self.exploration_weight = exploration_weight
        self.simulation_depth_limit = simulation_depth_limit

    @abstractmethod
    def get_legal_actions(self, state: Any) -> List[Any]:
        """
        Get all legal actions from the given state.

        Args:
            state: Current state

        Returns:
            List of legal actions
        """
        pass

    @abstractmethod
    def apply_action(self, state: Any, action: Any) -> Any:
        """
        Apply an action to a state and return the new state.

        Important: This should NOT modify the original state (return a copy).

        Args:
            state: Current state
            action: Action to apply

        Returns:
            New state after applying action
        """
        pass

    @abstractmethod
    def is_terminal(self, state: Any) -> bool:
        """
        Check if the state is terminal (game over, goal reached, etc.).

        Args:
            state: State to check

        Returns:
            True if terminal, False otherwise
        """
        pass

    @abstractmethod
    def get_reward(self, state: Any, player: int = 1) -> float:
        """
        Get the reward/value for a terminal state.

        Args:
            state: Terminal state
            player: Player perspective (1 for maximizing player, -1 for opponent)

        Returns:
            Reward value (typically 0-1 or -1 to 1)
        """
        pass

    def search(self, initial_state: Any, iterations: int,
               player: int = 1) -> Tuple[Any, MCTSNode]:
        """
        Perform MCTS search and return the best action.

        Args:
            initial_state: Starting state
            iterations: Number of MCTS iterations to perform
            player: Player perspective (1 or -1)

        Returns:
            Tuple of (best_action, root_node)
        """
        root = MCTSNode(state=initial_state)

        for _ in range(iterations):
            # Selection: traverse tree, re-simulating each step for fresh RNG
            node, state = self._select(root)

            # Expansion: try a new untried action if available
            if not self.is_terminal(state):
                available = self.get_legal_actions(state)
                untried = [a for a in available if a not in node.tried_actions]
                if untried:
                    node, state = self._expand(node, state, untried)

            # Simulation: playout from new node
            reward = self._simulate(state, player)

            # Backpropagation: update node values
            self._backpropagate(node, reward)

        # Return best action (most visited child)
        if not root.children:
            actions = self.get_legal_actions(initial_state)
            return (actions[0] if actions else None), root
        best_child = max(root.children, key=lambda c: c.visits)
        return best_child.action, root

    def _select(self, root: MCTSNode) -> Tuple[MCTSNode, Any]:
        """
        Selection phase: traverse tree using UCB1, re-simulating each action
        for a fresh RNG sample. Returns (node, state) at the expansion point.

        Args:
            root: Starting node (root of tree)

        Returns:
            Tuple of (node to expand, state at that node)
        """
        node = root
        state = root.state
        while True:
            if self.is_terminal(state):
                return node, state
            available = self.get_legal_actions(state)
            untried = [a for a in available if a not in node.tried_actions]
            if untried:
                return node, state
            best = node.best_child(self.exploration_weight)
            if best is None:
                return node, state
            # Re-simulate this action to advance state (fresh RNG each visit)
            state = self.apply_action(state, best.action)
            node = best

    def _expand(self, node: MCTSNode, state: Any, untried: List[Any]) -> Tuple[MCTSNode, Any]:
        """
        Expansion phase: pick the first untried action, simulate it, add a child node.

        Args:
            node: Node to expand from
            state: Current state at this node
            untried: List of untried actions at this node

        Returns:
            Tuple of (newly created child node, new state after action)
        """
        action = untried[0]
        node.tried_actions.add(action)
        new_state = self.apply_action(state, action)
        child_node = MCTSNode(
            state=None,
            parent=node,
            action=action,
        )
        node.children.append(child_node)
        return child_node, new_state

    def _simulate(self, state: Any, player: int) -> float:
        """
        Simulation phase: playout using random policy until terminal state.

        Args:
            state: State to simulate from
            player: Player perspective

        Returns:
            Reward from terminal state
        """
        current_state = state
        depth = 0

        while not self.is_terminal(current_state) and depth < self.simulation_depth_limit:
            actions = self.get_legal_actions(current_state)
            if not actions:
                break
            action = random.choice(actions)
            current_state = self.apply_action(current_state, action)
            depth += 1

        return self.get_reward(current_state, player)

    def _backpropagate(self, node: MCTSNode, reward: float) -> None:
        """
        Backpropagation phase: update all ancestors with simulation result.

        Args:
            node: Node to start backpropagation from
            reward: Reward to propagate
        """
        while node is not None:
            node.visits += 1
            node.value += reward
            node = node.parent

    def get_best_action(self, initial_state: Any, iterations: int,
                        player: int = 1) -> Any:
        """
        Convenience method to get just the best action.

        Args:
            initial_state: Starting state
            iterations: Number of MCTS iterations
            player: Player perspective

        Returns:
            Best action to take
        """
        action, _ = self.search(initial_state, iterations, player)
        return action

    def get_action_statistics(self, root: MCTSNode) -> List[Tuple[Any, int, float]]:
        """
        Get statistics for all actions from root node.

        Args:
            root: Root node from search

        Returns:
            List of (action, visits, average_value) tuples, sorted by visits
        """
        stats = [
            (child.action, child.visits, child.value / child.visits if child.visits > 0 else 0)
            for child in root.children
        ]
        return sorted(stats, key=lambda x: x[1], reverse=True)
