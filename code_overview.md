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

**`simulate_turn(ipc, state, p1_action, p2_action, flags=None)`**
- Executes one IPC round-trip with fresh PRNG on every call (via `_rand_battle`)
- Builds payload `{"battle": rand_battle, "p1": p1_action, "p2": p2_action}`; merges optional `flags` dict into the attack-turn call only
- Captures 6 chance fields from the raw response: `p1/p2CritChance`, `p1/p2AccuracyChance`, `p1/p2SecondaryChance`
- Runs forced-switch auto-advance loop (flags intentionally omitted — no attack)
- Attaches all 6 as `p1/p2_crit_chance`, `p1/p2_accuracy_chance`, `p1/p2_secondary_chance` (float or None) to the returned state dict
- Used by `PokemonMCTS.apply_action` and `shallow_search._run_group`

**`get_opponent_move_weights(state, n_samples=100)`**
- Estimates probability distribution over opponent's legal actions
- For moves: calls `select_move_with_ai_flags(state, p2_moves, player=2)` n_samples times,
  counts occurrences, normalizes to {action: probability}
- For switches: calls `select_switch_in` once (deterministic), returns {action: 1.0}
- Raises RuntimeError if no legal actions or if scoring produces no results
- Used in Phase 1 by `shallow_search.py`

**`build_battle_context(state, move_action, player)`**
- Constructs `BattleContext` from IPC state for ai_flags scoring
- `move_damage_pcts`: applies a random 85–100% roll (`random.randint(85,100)/100`) to each
  raw IPC damage value before converting to % of target max HP — matches Emerald AI behavior
  (`100 - (Random() % 16)`)

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
- `decision_fn` is designed to accept `shallow_search` directly

---

## search_process.py  (new)

Subprocess wrapper classes moved here from `battle_mode.py`.  Imports only stdlib
(`json`, `os`, `struct`, `subprocess`) — safe to import from normal CPython (no
free-threaded code).

**Constants**
- `_PYTHON_T` — path to `.venv_t/bin/python3.14t`
- `_MCTS_SERVER` — path to `mcts_server.py`
- `_SHALLOW_SERVER` — path to `shallow_search.py`

**`MCTSProcess`**
- Long-lived subprocess running `python3.14t mcts_server.py` with `PYTHON_GIL=0`
- `search(ps_state, iterations) -> (best_action, stats)` — binary-framed JSON round-trip
- `_read_exact`, `close` — standard subprocess IPC helpers

**`ShallowSearchProcess`**
- Long-lived subprocess running `python3.14t shallow_search.py` with `PYTHON_GIL=0`
- Added `verbose: bool = True` parameter — `False` passes `stderr=subprocess.DEVNULL`
  to suppress per-turn diagnostics (used by MatchupInfo analysis runs)
- `search(state) -> str` — sends `{node_script, state, num_workers}`, returns best action
- `_read_exact`, `close` — standard subprocess IPC helpers

---

## battle_mode.py  (modified)

- Removed `MCTSProcess` and `ShallowSearchProcess` class definitions
- Added: `from search_process import MCTSProcess, ShallowSearchProcess`
- All other logic unchanged

**`BattleResources`** (NamedTuple)
- Added field: `decision_fn` — callable `(ipc, state) -> str`, or None to use MCTS

**`_select_best_action(res, state, known_p2)`**
- After the `test_actions` early-exit, added a `decision_fn` early-exit:
  if `res.decision_fn` is not None, calls `decision_fn(res.ipc, state.ps_state)`, prints timing, returns `(action, [])`
- All existing MCTS logic (known_p2 path, candidate enumeration, bad-RNG penalty) is unchanged

**`run_battle_loop(..., shallow_workers=4, decision_fn=None)`**
- Added `shallow_workers` param (number of parallel NodeIPC connections in `ShallowSearchProcess`)
- Always creates `ShallowSearchProcess(node_script_path, num_workers=shallow_workers)`
- `decision_fn=None` → defaults to `lambda _ipc, state: shallow_proc.search(state)`
- Pass explicit `decision_fn` to override
- Closes `shallow_proc` in the `finally` block alongside `mcts_proc` and `ipc`

---

## shallow_search.py

Helper library + long-lived parallel server. Run as a subprocess by
`ShallowSearchProcess` in `battle_mode.py` using `.venv_t/bin/python3.14t`.

Module-level constants: `NUM_SAMPLES`, `MAX_DEPTH = 3`, `PERCENTILE_CUTOFF = 1.0`, `HP_BIN = 5`

Module-level flags: `VARIANCE_ANALYSIS = False` — set to True to print final-turn score variance statistics after each search

Module-level data: `_FACTOR_SPECS` — list of 6 tuples `(state_key, force_true_flag, force_false_flag)` mapping each binary RNG channel to its simulator state field and flag pair names

Module-level variable: `SAMPLER_CLASS` — set to `StratifiedSampler`; swap to `UniformSampler` to revert to baseline behaviour

### Scoring / fingerprinting

**`_score_state(state)`** — reimplements `PokemonMCTS.get_reward(player=1)` standalone
**`StateFingerprint(state)`** — binned HP/PP + status/volatiles/boosts/item → hashable tuple
**`_get_opp_weights(state)`** — returns `[(action, prob)]`; raises `RuntimeError` if no opponent actions

### Tree node

**`TurnNode`** (dataclass) — fields:
- `state` — battle state dict during expansion; `None` after state is released; leaf nodes always `None`
- `turn` — depth (0=root, 1…MAX_DEPTH)
- `action` — immediate p1 action that created this node (`""` for root)
- `children: dict[str, dict[fp, tuple[TurnNode, float]]]` — outer key = p1_action, inner key = fingerprint, value = (child_node, accumulated_weight)
- `score: Optional[float]` — `None` until `GetScore` sets it; pre-set at construction for leaf nodes
- `action_scores: dict[str, float]` — per-action expected score, set by `GetScore`
- `best_action: str` — action with highest score, set by `GetScore`

### Parallel IPC pool

**`_IPCPool(node_script_path, n)`** — creates `n` NodeIPC connections; thread-safe `acquire`/`release`/`close_all`

### Parallel expansion

**`_branch_weight(chance, forced)`** — probability weight for one binary RNG branch; returns 1.0 if `chance` is None, 0.0, or 1.0 (branch not applicable or degenerate)

**`UniformSampler`** — baseline sampler
- `__init__(n_samples)` — stores sample budget
- `run(ipc, node_state, p1_action, opp_weights, is_last_turn) → list[(item, fp, weight)]`
  - Each sample: opponent action drawn from original distribution (`q_k = p_k`, no importance weight); 6 independent Bernoulli(0.5) flips for RNG flags
  - Weight = product of 6 `_branch_weight()` calls against simulator-returned probabilities

**`FactorTracker`** (dataclass) — Bayesian Beta-posterior tracker for one binary RNG factor
- Fields: `a`, `b` (Beta prior, default 1.0), `N` (sample budget), `s` (eligible True-forced count), `n` (eligible sample count)
- `p_hat` property — `(a + s) / (a + b + n)`
- `q(i)` method — adjusted sampling probability for sample `i`; `clamp((p_hat*N - s) / (N - i), 0, 1)`
- `update(forced)` method — increments `n`; increments `s` if `forced=True`

**`CategoricalTracker`** (dataclass) — Dirichlet-posterior tracker for opponent move selection
- Fields: `priors` (`[a_k]` = `p_k * concentration`), `N`, `counts` (`[s_k]`), `n`
- `p_hats` property — `(a_k + s_k) / (sum(a) + n)` for each k
- `q_values(i)` method — adjusted sampling probs, renormalised to sum=1
- `update(k)` method — increments `n` and `counts[k]`

**`StratifiedSampler`** — sequential stratified sampler with online Beta/Dirichlet-prior updating
- `__init__(n_samples, prior_a=1.0, prior_b=1.0)` — creates 6 `FactorTracker` instances; `CategoricalTracker` created per-run from `opp_weights`
- `run(ipc, node_state, p1_action, opp_weights, is_last_turn) → list[(item, fp, weight)]`
  - Pass 1: opponent action sampled via `CategoricalTracker.q_values(i)`, binary factors via `FactorTracker.q(i)`; eligibility checked for all 7 factors; trackers updated for eligible only
  - Opponent ineligible: ≤1 action in `opp_weights`, action is switch/None, or opponent fainted before their turn (`p2_switches` present without `p2_moves`)
  - Pass 2: `w_i = product over eligible factors`; binary: `p/q or (1-p)/(1-q)`; opponent: `p_hat_k / q_k`; zero denominator → `w_i = 0`

**`_run_group(ipc_pool, node_state, p1_action, opp_weights, n_samples, is_last_turn)`**
- Thin wrapper: acquires IPC, creates `SAMPLER_CLASS(n_samples)`, calls `sampler.run(...)`, releases IPC
- Computes stddev of last-turn scores when `VARIANCE_ANALYSIS` is enabled
- Returns `(results, stddev_or_none)` 2-tuple

**`_print_variance_analysis(variance_records)`** — final-turn variance reporter
- Accepts pre-computed `(parent_node, p1_action, stddev)` tuples from `_run_group` (parallel)
- stddev is now **weighted** by importance weights: `sqrt(sum(w*(s-mu)^2) / sum(w))`
- Prints aggregate stats (average, median, 10th-percentile StdDev, 90th-percentile StdDev, count) in two groupings:
  1. By `(T2 prior action → final action)` — e.g. `"move 1 -> move 2"`
  2. By final action alone

**`_print_action_variance(root)`** — T1 action score distribution reporter (called after `GetScore`)
- For each T1 action, collects T2 children minimax scores and accumulated transition weights
- Computes weighted mean, weighted stddev, weighted 10th/90th percentile, child count
- Weighted percentile: sort by score, walk cumulative weight to threshold
- Prints one line per T1 action to `sys.stderr`; called from `run_search` when `VARIANCE_ANALYSIS=True`

**`expand_turn_parallel(frontier, ipc_pool, turn_num, n_workers, is_last_turn)`**
- Task building: one task per `(node, p1_action)` with full `opp_weights` — no inner loop over opponent actions; terminal frontier nodes (`is_over=True`) have their score set via `_score_state` and are skipped (no tasks generated)
- Parallel phase: submits `_run_group` tasks to `ThreadPoolExecutor`; collects `variance_records`
- Calls `_print_variance_analysis(variance_records)` when records are present
- Dedup phase: per-turn `seen: dict[fp, TurnNode]`; `parent.children[action][fp]` accumulates float weight (not integer count)
- Clears `node.state` for all frontier nodes after expansion
- Returns new frontier (all newly created nodes at this depth)

### Scoring

**`GetScore(node)`** — recursive bottom-up scorer; memoized via `node.score is not None`
- Base case (leaf): returns pre-set `node.score`
- Recursive: for each action, builds `(score, weight)` pairs from children, then applies `PERCENTILE_CUTOFF` filter: sorts ascending by score, accumulates weights from worst upward, keeps only the first states whose combined weight reaches `PERCENTILE_CUTOFF × total_weight`, then computes expected value over the kept set normalized by their weight sum. `PERCENTILE_CUTOFF = 1.0` keeps all states (standard expected value); lower values discard lucky high-score outcomes for Nuzlocke-style risk aversion. Sets `node.action_scores`, `node.best_action`, `node.score`.

### Printing

**`_print_turn_scores(parents, turn_num)`** — aggregates action scores across all `(TurnNode, weight)` pairs in `parents`; for each action prints weighted mean, weighted stddev, unweighted stddev, and `n` (number of parent states that included this action); header shows unique-state count; rows sorted descending by weighted mean

**`print_scores(root)`** — iterates turns 1..MAX_DEPTH, maintaining a `parents = [(node, cumulative_weight)]` list; calls `_print_turn_scores` each turn; advances by following each parent's `best_action` edges and multiplying weights (`w * child_w`); stops when `parents` is empty (terminal path) or no parent has `action_scores`
- Turn 1: one parent (root, 1.0) → output identical to old single-path version, stddevs = 0
- Turn 2: all T1 children of root's best action, weighted by T1 importance weights
- Turn 3: all T2 children of each T1's best action, weighted by w1 × w2

### Server

**`run_search(state, node_script_path, num_workers)`** — creates pool, runs `MAX_DEPTH` × `expand_turn_parallel`, calls `GetScore(root)`, prints scores, returns `root.best_action`
**`main()`** — server loop: reads `{node_script, state, num_workers}`, writes `{action}`
**`if __name__ == '__main__': main()`** — entry point when launched by `ShallowSearchProcess`

---

## MatchupInfo.py  (modified)

**`_apply_turn(ipc, state, p1_action) -> state`**
- Lazy-imports `simulate_turn` and `_pick_opponent_action` from `battle_sim` (avoids circular import)
- Calls `_pick_opponent_action(state)` for the opponent action (same ai_flags logic as live battles)
- Calls `simulate_turn(ipc, state, p1_action, opp_action)` with no RNG flags (natural variance)
- Returns the resulting state; forced switches are auto-advanced inside `simulate_turn`

**`_run_matchup_context(ipc, decision_fn, team1, team2, setup_p1, setup_p2, num_runs, ...)`**
- Replaces the old MCTS-specific loop; `decision_fn(state) -> str` is called each turn
- Runs at least `num_runs` times; stops early when SEM < `sem_threshold` or `max_runs` reached
- Each turn: `best_action = decision_fn(state)` → `state = _apply_turn(ipc, state, best_action)`
- Scores final state with `matchup_reward`; applies `convergence_penalty` if SEM did not converge
- Returns `MatchupResult(score, action_stats={}, num_runs, variance)`
  (`action_stats` is always empty — shallow search doesn't expose per-action stats)

**`_run_mcts_context(ipc, mcts, ..., mcts_iterations, ...)`**
- Backward-compatible shim: wraps `_run_matchup_context` with `lambda state: mcts.search(state, mcts_iterations)[0]`

**`MatchupInfo.__init__`**  (updated)
- New parameter `use_mcts: bool = False`
- `use_mcts=False` (default): creates `ShallowSearchProcess(node_script_path, num_workers, verbose=False)`;
  `decision_fn = proc.search`; init print says `shallow_workers=N`
- `use_mcts=True`: creates `PokemonMCTS`; `decision_fn = lambda state: mcts.search(state, mcts_iterations)[0]`;
  init print unchanged
- `search_proc.close()` called in `finally` block (None-safe)
- Inner loop calls `_run_matchup_context(ipc, decision_fn, ...)` for all pairs

---

## How components interact

```
battle_mode.py / MatchupInfo.py
  │
  ├─ ShallowSearchProcess.search(state)   [imported from search_process.py]
  │    └─ subprocess: .venv_t/python3.14t shallow_search.py  [PYTHON_GIL=0]
  │         └─ run_search → expand_turn_parallel ×3
  │              └─ ThreadPoolExecutor → _run_group → simulate_turn (per thread, own NodeIPC)
  │
  └─ MCTSProcess.search(state, iterations)  [imported from search_process.py]
       └─ subprocess: .venv_t/python3.14t mcts_server.py

MatchupInfo._apply_turn(ipc, state, p1_action)
  └─ _pick_opponent_action(state) + simulate_turn(ipc, state, p1, p2)

mcts.apply_action(state, action)
  └─ simulate_turn(ipc, state, action, p2)
       ├─ _rand_battle [fresh PRNG]
       ├─ ipc.send / parse_ipc_response
       └─ forced-switch loop: _pick_opponent_action → ipc.send

get_p2_move_candidates(state, move_actions)
  └─ build_battle_context × n + score_move × n_samples (RNG sampling)

can_player_switch(state)
  └─ _get_active_pokemon, state["p2_dmg_calcs"]
```
