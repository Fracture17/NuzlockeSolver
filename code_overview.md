# Code Overview

High-level description of all code written or modified.  Updated after every change.
Contains function/variable/file names but no explicit code.

---

## battle_sim.py  (modified)

### New module-level standalone functions (added above PokemonMCTS class)

**`_pick_opponent_action(state)`**
- Module-level version of what was previously `PokemonMCTS._pick_p2_move`
- If `state["p2_moves"]` is present: calls `select_move_with_ai_flags` for player 2,
  falls back to first move if scoring returns None
- If `state["p2_switches"]` is present: calls `select_switch_in` for player 2
- Returns None if neither moves nor switches are available
- Used by `simulate_turn` for forced-switch auto-advance

**`can_player_switch(state)`**
- Returns True if voluntary switching is allowed this turn
- Condition 1: opponent's active pokemon has `activeTurns == 1` (just switched in)
- Condition 2: any value in `state["p2_dmg_calcs"]` >= player active pokemon's HP
- Reads `state["battle"]["sides"]` for active pokemon info
- Used by `PokemonMCTS._expand` (replaces old depth-based filter)

**`simulate_turn(ipc, state, p1_action, p2_action)`**
- Executes one IPC round-trip with fresh PRNG on every call (via `_rand_battle`)
- Builds payload `{"battle": rand_battle, "p1": p1_action, "p2": p2_action}` (omits p2 if None)
- Calls `ipc.send`, then `parse_ipc_response`
- Runs forced-switch auto-advance loop: while battle not over and p1 has no actions,
  picks opponent switch via `_pick_opponent_action` and advances with another IPC call
- Returns final parsed state dict
- Used by `PokemonMCTS.apply_action` and (in Phase 1) `shallow_search.py`

**`get_opponent_move_weights(state, n_samples=100)`**
- Estimates probability distribution over opponent's legal actions
- For moves: calls `select_move_with_ai_flags(state, p2_moves, player=2)` n_samples times,
  counts occurrences, normalizes to {action: probability}
- For switches: calls `select_switch_in` once (deterministic), returns {action: 1.0}
- Raises RuntimeError if no legal actions or if scoring produces no results
- Used in Phase 1 by `shallow_search.py`

---

### Modified PokemonMCTS methods

**`_expand(node, state, untried)`**
- Now calls `can_player_switch(state)` instead of checking `node.parent is not None`
- If `can_player_switch` returns False: filters switches from `untried` if moves are available
- Delegates to `super()._expand()`

**`_pick_p2_move(state)`**
- Simplified: delegates entirely to `_pick_opponent_action(state)`

**`apply_action(state, action)`**
- Picks p2 via `state.get('_p2_forced') or self._pick_p2_move(state)`
- Records start time, calls `simulate_turn(self.ipc, state, action, p2)`
- Records elapsed time in `self.timing_stats` if set
- Thin wrapper around `simulate_turn`; timing tracking preserved

---

### Modified `play_game`

**`play_game(ipc, player_team_str, opp_team_str, mcts, mcts_iterations, record_path, decision_fn)`**
- New optional parameter: `decision_fn(ipc, state) -> str`
- If `decision_fn` is not None: calls it for player action selection, bypasses MCTS search
- If `decision_fn` is None: existing MCTS behavior unchanged (with/without recording)
- State advancement (`mcts.apply_action`) is always used regardless of `decision_fn`
- All existing print statements, timing output, and debug file writing are preserved
- `decision_fn` is designed to accept `shallow_search` directly (Phase 1)

---

## How components interact

```
play_game
  │
  ├─ if decision_fn: decision_fn(ipc, state) → action string
  └─ else: mcts.search(state, ...) → action string
  │
  └─ mcts.apply_action(state, action)
       │
       ├─ _pick_p2_move(state) → _pick_opponent_action(state)
       └─ simulate_turn(ipc, state, action, p2)
            │
            ├─ _rand_battle(state["battle"]) [fresh PRNG]
            ├─ ipc.send(data)
            ├─ parse_ipc_response(response)
            └─ forced-switch loop: _pick_opponent_action(new_state) → ipc.send

get_opponent_move_weights(state)
  └─ select_move_with_ai_flags(state, p2_moves, player=2) × n_samples [for move weights]
  └─ select_switch_in(state, p2_switches, player=2) [for switch, deterministic]

can_player_switch(state)
  └─ _get_active_pokemon(sides[0/1]) [reads activeTurns and HP]
  └─ state["p2_dmg_calcs"] [checks KO range]
```

---

## Phase 1 (not yet implemented)

`shallow_search.py` will import:
- `simulate_turn` from `battle_sim`
- `get_opponent_move_weights` from `battle_sim`
- `can_player_switch` from `battle_sim`
- `get_reward` from `battle_sim` (via `PokemonMCTS.get_reward`)
- `_get_active_pokemon` from `battle_sim`
- `parse_ipc_response` from `battle_sim`
