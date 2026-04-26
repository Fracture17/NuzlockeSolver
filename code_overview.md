# Code Overview

High-level description of all code written or modified.  Updated after every change.
Contains function/variable/file names but no explicit code.

---

## battle_sim.py

Core battle simulation helpers.  MCTS classes and `play_game` were removed; only
stateless helpers remain.

**`_pick_opponent_action(state, active_flags=None)`**
- Module-level function selecting the opponent's action each turn
- `active_flags` forwarded to `select_move_with_ai_flags`; defaults to `[0, 1, 2]` when None
- If `state["p2_moves"]` is present: calls `select_move_with_ai_flags` for player 2,
  falls back to first move if scoring returns None
- If `state["p2_switches"]` is present: calls `select_switch_in` for player 2
- Returns None if neither moves nor switches are available
- Used by `simulate_turn` (forced-switch auto-advance) and `battle_mode._build_p2_action`

**`can_player_switch(state)`**
- Returns True if voluntary switching is allowed this turn
- Condition 1: opponent's active pokemon has `activeTurns == 1` (just switched in)
- Condition 2: any value in `state["p2_dmg_calcs"]` >= player active pokemon's HP (normal KO threat)
- Condition 3: `state["p2_crit_dmg_calcs_bench"]` is present (crit KO threat on active)
- Use `get_voluntary_switches` to also filter out crit-unsafe targets

**`get_voluntary_switches(state, p1_switches)`**
- Returns the filtered list of switches that are safe to make voluntarily this turn
- Opp just switched in → all switches
- Normal-hit KO threat → all switches
- Crit-hit KO threat only (`p2_crit_dmg_calcs_bench` present) → switches where bench pokemon HP > max crit damage for that slot; slot key = `str(int("switch N".split()[1]) - 1)`
- No threat → empty list
- Reads `state["battle"]["sides"]` for active pokemon info

**`simulate_turn(ipc, state, p1_action, p2_action, flags=None)`**
- Executes one IPC round-trip with fresh PRNG on every call (via `_rand_battle`)
- Builds payload `{"battle": rand_battle, "p1": p1_action, "p2": p2_action}`; merges optional `flags` dict into the attack-turn call only
- Captures 6 chance fields from the raw response: `p1/p2CritChance`, `p1/p2AccuracyChance`, `p1/p2SecondaryChance`
- Runs forced-switch auto-advance loop (flags intentionally omitted — no attack)
- Attaches all 6 as `p1/p2_crit_chance`, `p1/p2_accuracy_chance`, `p1/p2_secondary_chance` (float or None) to the returned state dict
- Propagates `opp_items_remaining`, `opp_items_initial`, `opp_item_ps_id`, `opp_ai_flags` from parent state to result state; decrements `opp_items_remaining` if `p2_action` starts with `'item '`
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

## ai_flags.py

Gen 3 trainer AI flag scoring.

**`MoveEffect` enum**
- 150+ entries covering all Gen 3 move effect categories
- Single-stage boosts: `ATK_UP` … `EVA_UP`; double-stage: `ATK_UP_2` … `EVA_UP_2`
- Single-stage drops: `ATK_DOWN` … `EVA_DOWN`; double-stage: `ATK_DOWN_2` … `EVA_DOWN_2`
- Standalone effects: `MINIMIZE`, `DEFENSE_CURL`, `CAMOUFLAGE`
- Combined multi-stat: `BULK_UP`, `CALM_MIND`, `COSMIC_POWER`, `DRAGON_DANCE`, `CURSE`, `TICKLE`

**`SETUP_FIRST_TURN_EFFECTS`** (flag 3 set)
- Matches `AI_SetupFirstTurn_SetupEffectsToEncourage` in pokeemerald exactly
- Includes all single/double-stage stat boosts and drops, screens, status, confusion, misc setup

**`RISKY_EFFECTS`** (flag 4 set)
- Matches `AI_Risky_EffectsToEncourage` in pokeemerald exactly
- `HIGH_CRITICAL` handled via `ctx.move.is_high_crit` in `apply_flag4` (not an enum entry)

**`apply_flag3(ctx, rng)`**
- +2 with **176/256** probability on first battle turn for setup moves
- (pokeemerald `if_random_less_than 80` means score when random ≥ 80 → 176/256)

**`apply_flag4(ctx, rng)`**
- +2 with 128/256 probability for risky moves OR moves with `is_high_crit`

**`score_move(ctx, active_flags, rng)`**
- Dispatches to `apply_flag0` … `apply_flag4` (flag 7 still commented out)
- Flags 3 and 4 are now active (enabled 2026-04-11)

---

## gen3_data.py

Move data loader and `MoveInfo` builder.

**`_DOUBLE_RAISE_MAP` / `_DOUBLE_LOWER_MAP`**
- Parallel to `_SINGLE_RAISE_MAP`/`_SINGLE_LOWER_MAP` for ±2 single-stat boosts/drops
- Used by `_effect_from_fields` when `abs(boost_value) >= 2`

**`_NAME_EFFECTS` additions**
- `swordsdance` → `ATK_UP_2`, `agility` → `SPE_UP_2`, `barrier`/`irondefense` → `DEF_UP_2`
- `amnesia` → `SPD_UP_2`, `tailglow` → `SPA_UP_2`, `doubleteam` → `EVA_UP_2`
- `minimize` → `MINIMIZE`, `defensecurl` → `DEFENSE_CURL`, `camouflage` → `CAMOUFLAGE`

**`_effect_from_fields` boost handler**
- `abs(val) >= 2` → double map; `abs(val) == 1` → single map (was always single before)

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
- `matchup_cache_path: str | None = None` — forwarded to subprocess in every request dict
- `search(state) -> str` — sends `{node_script, state, num_workers, matchup_cache}`, returns best action
- `_read_exact`, `close` — standard subprocess IPC helpers

---

## battle_mode.py

Live battle orchestration.  All MCTS logic removed; `decision_fn` (shallow search) is
the only search path.

**`_flags_for_trainer(name)`**
- Maps trainer name (case-insensitive) to opponent AI flag list
- `'Winona'` → `[0, 1, 2, 4]`; `'Sidney'` → `[0, 1, 2, 3]`; None/unrecognised → `[0, 1, 2]`

**`BattleResources`** (NamedTuple)
- Fields: `ipc`, `decision_fn`, `test_actions`, `test_action_idx`, `opp_ai_flags`, `opp_items`, `opp_item_ps_id`
- `decision_fn`: callable `(ipc, state) -> str`; defaults to `lambda _ipc, state: shallow_proc.search(state)`
- `opp_ai_flags`: AI flag list for the opponent, set by `_flags_for_trainer(trainer_name)` at startup
- `opp_items`: count of healing items the opponent has; read from `OPP_ITEMS` in `config.py`
- `opp_item_ps_id`: PS item ID string; read from `APPROVED_OPPONENT_ITEMS` in `config.py`
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
- Calls `_pick_opponent_action(state.ps_state, res.opp_ai_flags)` — flags flow from trainer name
- Used to construct the pre-computed opponent action before player decision

**`_simulate_and_reconcile(ipc, state, p1_action, ...)`**
- Calls `_pick_opponent_action(new_state)` for opponent action during simulation (default flags)

**`_init_ps_battle(res, state, ...)`**
- After PS battle init, injects `opp_items_remaining`, `opp_items_initial`, `opp_item_ps_id` into `ps_state` when `res.opp_items > 0` and `res.opp_item_ps_id` is set

**`run_battle_loop(..., shallow_workers=30, matchup_cache_path=None, trainer_name=None)`**
- `trainer_name`: e.g. `'Winona'` or `'Sidney'`; computes `opp_ai_flags` via `_flags_for_trainer`
- Reads `OPP_ITEMS` and `APPROVED_OPPONENT_ITEMS` from `config.py`; passes to `BattleResources`
- Prints `[battle_loop] Trainer: {name} → opponent AI flags {flags}` at startup
- Creates `ShallowSearchProcess(node_script_path, num_workers=shallow_workers, matchup_cache_path=matchup_cache_path)`
- `decision_fn` defaults to `lambda _ipc, state: shallow_proc.search(state)`
- Closes `shallow_proc` in `finally` block alongside `ipc`

**`battle_test.py` / `game_loop.py`**
- `TRAINER_NAME` imported from `config.py` (no longer defined locally)

**`config.py` constants**
- `APPROVED_OPPONENT_ITEMS: str | None` — PS item ID the current trainer carries, or `None` to disable
- `OPP_ITEMS: int` — number of copies of that item the trainer has (default 0)
- `TRAINER_NAME: str | None` — current opponent trainer name (e.g. `'Winona'`, `'Sidney'`); used by F2 logging, live battle loop, and 6v6 simulation

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
**`_is_switch_only(state)`** — predicate: `p1_switches` present, `p1_moves` absent, not `is_over`; used to identify forced faint-switch states that don't consume a depth slot
**`StateFingerprint(state)`** — binned HP/PP + status/volatiles/boosts/item → hashable tuple
**`_opp_item_threshold_met(opp_active, item_ps_id)`** — returns True if item's trigger condition is met; mirrors pokeemerald `ShouldUseItem()`: full-restore-type items fire at `hp < maxHP/4`; heal-HP items also fire when `(maxHP-hp) > healAmount` (Potion=20, Super Potion=50, Hyper Potion=200)

**`_opp_reservation_allows_item(state)`** — returns True if pokeemerald's slot-based reservation allows item use; slot 0 always allowed; later slots require `validMons <= (initial - used) + 1`; counts living opponent mons from PS state

**`_get_opp_weights(state)`** — returns `[(action, prob)]`; checks `opp_items_remaining` + threshold + reservation before normal move/switch scoring; reads `opp_ai_flags` from state and passes to `get_p2_move_candidates` / `_pick_opponent_action`; raises `RuntimeError` if no opponent actions

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

**`_drain_switch_nodes(frontier, ipc_pool, depth, n_workers, is_last_turn)`**
- Called after each `expand_turn_parallel` in `run_search`
- Extracts switch-only nodes from the frontier (via `_is_switch_only`), re-expands them at the same depth with the same `is_last_turn`, replaces them with their children
- Loops until no switch-only nodes remain — handles consecutive forced switches
- Switch nodes stay in the tree (already linked as children); only the frontier is updated
- `expand_turn_parallel` clears `node.state` after expanding, so processed nodes don't re-trigger

**`expand_turn_parallel(frontier, ipc_pool, turn_num, n_workers, is_last_turn)`**
- Task building: one task per `(node, p1_action)` with full `opp_weights` — no inner loop over opponent actions; terminal frontier nodes (`is_over=True`) have their score set via `_score_state` and are skipped (no tasks generated)
- Node construction uses `isinstance(item, dict)` to distinguish scored float leaves from state-dict nodes (including switch-only results that bypassed scoring even at `is_last_turn=True`)
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

**`MATCHUP_WEIGHT = 0.3`** — scale factor for matchup cache switch bias
**`_matchup_cache`, `_matchup_cache_path`** — module-level cache globals; reuse across requests to same path
**`_load_matchup_cache(path) -> MatchupInfo | None`** — loads and caches a pickled `MatchupInfo`; returns None on error
**`_apply_matchup_bias(root, state, cache)`** — biases switch action scores after `GetScore`; recomputes `root.best_action`
**`run_search(state, node_script_path, num_workers, matchup_cache_path=None)`** — creates pool, runs `MAX_DEPTH` × `expand_turn_parallel` + `_drain_switch_nodes`, calls `GetScore(root)`, applies matchup bias, prints scores, returns `root.best_action`
**`main()`** — server loop: reads `{node_script, state, num_workers, matchup_cache}`, writes `{action}`
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

**`MatchupInfo._parse_pipe_for_validation(pipe_str) -> dict`** (static)
- Extracts `{level: int, moves: frozenset}` from a PS pipe string
- Level at index -2 (empty → 100); moves at index 4 split by `,`

**`MatchupInfo.validate_for_state(state) -> bool`**
- Validates all p1 party pokemon against `self.box` and revealed p2 pokemon against `self.opp`
- Checks: species lookup, level, move set; returns False on any mismatch

**`MatchupInfo.get_switch_biases(state, p1_switches) -> dict[str, (float, float)]`**
- Returns `{switch_action: (active_avg, bench_avg)}` for each switch action
- `avg_score` averages `rawMatchupInfo[species][opp_species][None].score` over alive non-active opponents
- Uses `self.opp` (cache roster) as ground truth for who is alive — unrevealed opponents are assumed alive; only the currently active opponent and confirmed-fainted ones (hp==0 in battle state) are excluded
- Returns `{}` only if all non-active opponents are defeated; omits entries where any lookup fails

---

## team_analyzer.py

**`build_matchup_info(node_script_path, ...)`**
- Calls `MatchupInfo(node_script_path, ...)` — original non-variant path

**`_level_pipe(pipe, multiplier) → str`**
- Applies level multiplier to the level field (index -2) of a PS pipe string

**`VariantMatchupInfo`** (class)
- Drop-in for `MatchupInfo`; exposes `box`, `opp`, `badge_boosts`, `prunedMatchupInfo`, `variants`, `berry_map`, `rawMatchupInfo`
- `box`: dict `{variant_key → leveled pipe string}` (all moves already in the pipe for expanded variants)
- `get_battle_pipe(var_key, assigned_opp) → str`: substitutes cure berry (lum variants) and/or 4 locked moves (expanded variants) before a 6v6 battle

**`build_variant_matchup_info(node_script_path, level_multiplier, num_workers, num_runs)`**
- Calls `build_variants(box_raw, AVAILABLE_ITEMS, AVAILABLE_TMS)` → `variants`
- Applies level multiplier to each variant's pipe string → `leveled_box`
- Runs `_run_matchup_context` for every (variant_key, opp_name) with `move_pool`, `original_moves`, `is_lum` from `VariantInfo`
- **Expanded score correction**: sentinel results (num_runs==0) are replaced with the corresponding `orig` variant's score; all-sentinel expanded variants are discarded
- Builds `berry_map[var_key][opp_name]` via `assign_berry` for all lum variants
- Returns `VariantMatchupInfo`

**`_print_variant_team(ordered, assignment, matchup_info)`**
- Prints final item and move assignments for each team member after team selection

**`_play_full_game(ipc, search_proc, player_team_str, opp_team_str, badge_boosts, turn_limit=200)`**
- Runs one complete simulated battle; injects `opp_ai_flags` and item state from config
- Returns `(winner, any_p1_fainted)`

**`find_best_surviving_team(node_script_path, matchup_info, n_games=3, num_workers=15)`**
- Accepts both `MatchupInfo` and `VariantMatchupInfo` (duck-typed via `isinstance` check)
- For variant matchups: uses `get_battle_pipe` to substitute berry/moves per team member before each 6v6 battle
- On success with variant matchup: calls `_print_variant_team` to show final assignments
- Returns `(score, team_tuple, assignment_dict)` or `None` if all teams fail

---

---

## test2.py

**`is_valid_team(team, assignment, variants, raw_matchup_info) → bool`**
- Species check: no two variant keys in `team` share the same `VariantInfo.species`
- Item check: counts usage of each `AVAILABLE_ITEMS` item tag across the team; fails if any exceeds its quantity
- TM check: for assigned expanded members only, counts TM usage from `locked_moves` in the matchup result; fails if any TM exceeds its `AVAILABLE_TMS` quantity. Flex expanded members are skipped (locked moves unknown at team-selection time)
- Berries (`lum`, `orig`) and non-berry items not in `AVAILABLE_ITEMS` are exempt from item counting

**`select_best_team(m, n)`**
- Now detects `VariantMatchupInfo` via `hasattr(m, 'variants')` duck-typing
- When variants are present: calls `is_valid_team` after computing the best assignment for each team combination; skips invalid teams before adding to results

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

can_player_switch(state) / get_voluntary_switches(state, p1_switches)
  └─ _get_active_pokemon, state["p2_dmg_calcs"], state["p2_crit_dmg_calcs_bench"]
```
