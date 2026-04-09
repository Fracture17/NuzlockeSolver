# Code Overview

High-level description of all code written or modified.  Updated after every change.
Contains function/variable/file names but no explicit code.

---

## battle_sim.py

Core battle simulation helpers.  MCTS classes and `play_game` were removed; only
stateless helpers remain.

**`_pick_opponent_action(state)`**
- Module-level function selecting the opponent's action each turn
- If `state["p2_moves"]` is present: calls `select_move_with_ai_flags` for player 2,
  falls back to first move if scoring returns None
- If `state["p2_switches"]` is present: calls `select_switch_in` for player 2
- Returns None if neither moves nor switches are available
- Used by `simulate_turn` (forced-switch auto-advance) and `battle_mode._build_p2_action`

**`can_player_switch(state)`**
- Returns True if voluntary switching is allowed this turn
- Condition 1: opponent's active pokemon has `activeTurns == 1` (just switched in)
- Condition 2: any value in `state["p2_dmg_calcs"]` >= player active pokemon's HP
- Reads `state["battle"]["sides"]` for active pokemon info

**`simulate_turn(ipc, state, p1_action, p2_action, flags=None)`**
- Executes one IPC round-trip with fresh PRNG on every call (via `_rand_battle`)
- Builds payload `{"battle": rand_battle, "p1": p1_action, "p2": p2_action}`; merges optional `flags` dict into the attack-turn call only
- Captures 6 chance fields from the raw response: `p1/p2CritChance`, `p1/p2AccuracyChance`, `p1/p2SecondaryChance`
- Runs forced-switch auto-advance loop (flags intentionally omitted — no attack)
- Attaches all 6 as `p1/p2_crit_chance`, `p1/p2_accuracy_chance`, `p1/p2_secondary_chance` (float or None) to the returned state dict
- Used by `battle_mode._simulate_and_reconcile` and `shallow_search._run_group`

**`get_opponent_move_weights(state, n_samples=100)`**
- Estimates probability distribution over opponent's legal actions
- For moves: calls `select_move_with_ai_flags(state, p2_moves, player=2)` n_samples times,
  counts occurrences, normalizes to {action: probability}
- For switches: calls `select_switch_in` once (deterministic), returns {action: 1.0}
- Raises RuntimeError if no legal actions or if scoring produces no results
- Used by `shallow_search.py`

**`build_battle_context(state, move_action, player)`**
- Constructs `BattleContext` from IPC state for ai_flags scoring
- `move_damage_pcts`: applies a random 85–100% roll to each raw IPC damage value before
  converting to % of target max HP — matches Emerald AI behavior

**`select_move_with_ai_flags(state, move_actions, player)`**
- Scores all moves with Gen 3 AI flags; returns best action string

**`assemble_team_string`, `assemble_opponent_string`, `reorder_team`**
- Team string assembly helpers for building PS battle payloads

---

## search_process.py

Subprocess wrapper for the shallow-search server.  Imports only stdlib — safe to import
from normal CPython without pulling in free-threaded code.

**Constants**
- `_PYTHON_T` — path to `.venv_t/bin/python3.14t`
- `_SHALLOW_SERVER` — path to `shallow_search.py`

**`ShallowSearchProcess`**
- Long-lived subprocess running `python3.14t shallow_search.py` with `PYTHON_GIL=0`
- `verbose: bool = True` parameter — `False` passes `stderr=subprocess.DEVNULL`
  to suppress per-turn diagnostics (used by MatchupInfo analysis runs)
- `search(state) -> str` — sends `{node_script, state, num_workers}`, returns best action
- `_read_exact`, `close` — standard subprocess IPC helpers

---

## battle_mode.py

Live battle orchestration.  All MCTS logic removed; `decision_fn` (shallow search) is
the only search path.

**`BattleResources`** (NamedTuple)
- Fields: `ipc`, `decision_fn`, `test_actions`, `test_action_idx`
- `decision_fn`: callable `(ipc, state) -> str`; defaults to `lambda _ipc, state: shallow_proc.search(state)`
- `mcts_proc` and `mcts_iterations` removed

**`_run_decision_fn(res, state, known_p2, label)`**
- Shared helper called by both `_select_best_action` and `_select_faint_switch`
- Injects `known_p2` into `state.ps_state["known_p2"]`
- Calls `res.decision_fn(res.ipc, state.ps_state)`, times the call
- Prints: `[battle_loop] {label} chose {action!r} in {time:.2f}s`

**`_select_best_action(res, state, known_p2)`**
- `test_actions` path: returns indexed test action, increments counter
- Default path: calls `_run_decision_fn(res, state, known_p2)`, returns `(action, [])`
- Returns `(action, [])` — empty stats list (was MCTS-specific, kept for call-site compat)

**`_select_faint_switch(res, state)`**
- `test_actions` path: returns indexed test action
- Default path: calls `_run_decision_fn(res, state, known_p2=None, label='Faint-switch')`
- Validation fallback: if result is None or doesn't start with `switch `, defaults to
  `available[0]` and prints a warning

**`_build_p2_action(res, state)`**
- Calls `_pick_opponent_action(state.ps_state)` for opponent move/switch selection
- Used to construct the pre-computed opponent action before player decision

**`_simulate_and_reconcile(ipc, state, p1_action, ...)`**
- Calls `_pick_opponent_action(new_state)` for opponent action during simulation

**`run_battle_loop(..., shallow_workers=30)`**
- Removed `mcts_iterations` and `num_workers` params
- Creates `ShallowSearchProcess(node_script_path, num_workers=shallow_workers)`
- `decision_fn` defaults to `lambda _ipc, state: shallow_proc.search(state)`
- Closes `shallow_proc` in `finally` block alongside `ipc`

---

## shallow_search.py

Helper library + long-lived parallel server. Run as a subprocess by
`ShallowSearchProcess` in `battle_mode.py` using `.venv_t/bin/python3.14t`.

Module-level constants: `NUM_SAMPLES`, `MAX_DEPTH = 3`, `PERCENTILE_CUTOFF = 1.0`, `HP_BIN = 5`

Module-level flags: `VARIANCE_ANALYSIS = False` — set to True to print final-turn score variance statistics after each search

Module-level data: `_FACTOR_SPECS` — list of 6 tuples `(state_key, force_true_flag, force_false_flag)` mapping each binary RNG channel to its simulator state field and flag pair names

Module-level variable: `SAMPLER_CLASS` — set to `StratifiedSampler`; swap to `UniformSampler` to revert to baseline behaviour

### Scoring / fingerprinting

**`_score_state(state)`** — scores battle state from p1's perspective (HP%, faint counts, boosts)
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
- stddev is **weighted** by importance weights: `sqrt(sum(w*(s-mu)^2) / sum(w))`
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

## MatchupInfo.py

**`_apply_turn(ipc, state, p1_action) -> state`**
- Lazy-imports `simulate_turn` and `_pick_opponent_action` from `battle_sim` (avoids circular import)
- Calls `_pick_opponent_action(state)` for the opponent action (same ai_flags logic as live battles)
- Calls `simulate_turn(ipc, state, p1_action, opp_action)` with no RNG flags (natural variance)
- Returns the resulting state; forced switches are auto-advanced inside `simulate_turn`

**`_run_matchup_context(ipc, decision_fn, team1, team2, setup_p1, setup_p2, num_runs, ...)`**
- `decision_fn(state) -> str` is called each turn to select p1's action
- Runs at least `num_runs` times; stops early when SEM < `sem_threshold` or `max_runs` reached
- Each turn: `best_action = decision_fn(state)` → `state = _apply_turn(ipc, state, best_action)`
- Scores final state with `matchup_reward`; applies `convergence_penalty` if SEM did not converge
- Returns `MatchupResult(score, action_stats={}, num_runs, variance)`

**`MatchupInfo.__init__`**
- Creates `ShallowSearchProcess(node_script_path, num_workers, verbose=False)`
- `decision_fn = proc.search`; init print says `shallow_workers=N`
- Closes `search_proc` in `finally` block
- Inner loop calls `_run_matchup_context(ipc, decision_fn, ...)` for all pairs

---

## team_analyzer.py

**`build_matchup_info(node_script_path, ...)`**
- Calls `MatchupInfo(node_script_path, ...)` — `use_mcts` and `mcts_iterations` removed

**`find_best_surviving_team(...)`**
- Stubbed: raises `NotImplementedError("find_best_surviving_team needs redesign for shallow search (play_game removed)")`

---

## How components interact

```
battle_mode.py / MatchupInfo.py
  │
  └─ ShallowSearchProcess.search(state)   [imported from search_process.py]
       └─ subprocess: .venv_t/python3.14t shallow_search.py  [PYTHON_GIL=0]
            └─ run_search → expand_turn_parallel ×3
                 └─ ThreadPoolExecutor → _run_group → simulate_turn (per thread, own NodeIPC)

MatchupInfo._apply_turn(ipc, state, p1_action)
  └─ _pick_opponent_action(state) + simulate_turn(ipc, state, p1, p2)

simulate_turn(ipc, state, action, p2)
  ├─ _rand_battle [fresh PRNG]
  ├─ ipc.send / parse_ipc_response
  └─ forced-switch loop: _pick_opponent_action → ipc.send

battle_mode._run_decision_fn(res, state, known_p2, label)
  └─ res.decision_fn(res.ipc, state.ps_state)  [= ShallowSearchProcess.search]
       [called by both _select_best_action and _select_faint_switch]

can_player_switch(state)
  └─ _get_active_pokemon, state["p2_dmg_calcs"]
```
