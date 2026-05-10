"""Shallow search for Pokemon battles.

Helper library (TurnNode, scoring, fingerprinting, printing) plus a long-lived
parallel server that is launched as a subprocess by ShallowSearchProcess in
battle_mode.py.

  Run as server:  .venv_t/bin/python3.14t shallow_search.py
  Protocol: 4-byte big-endian payload length + UTF-8 JSON (same as NodeIPC).
  Request:  {node_script, state, num_workers}
  Response: {action}
"""
from __future__ import annotations

import json
import random
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from battle_sim import (
    _get_active_pokemon,
    _pick_opponent_action,
    can_player_switch,
    get_voluntary_switches,
    get_p2_move_candidates,
    simulate_turn,
)

# ---------------------------------------------------------------------------
# Opponent item prediction helpers
# ---------------------------------------------------------------------------

# Heal amounts (HP restored) keyed by PS item ID, for AI_ITEM_HEAL_HP items.
_ITEM_HEAL_AMOUNTS: dict[str, int] = {
    'potion':      20,
    'superpotion': 50,
    'hyperpotion': 200,
}


def _opp_item_threshold_met(opp_active: dict, item_ps_id: str) -> bool:
    """Return True if the opponent's active pokemon meets the threshold for item use.

    Mirrors pokeemerald ShouldUseItem():
      - Full-restore-type items fire when hp < maxHP / 4.
      - Heal-HP items also fire when (maxHP - hp) > healAmount.
    """
    hp    = opp_active.get('hp', 0)
    maxhp = opp_active.get('maxhp', 1)
    if hp <= 0:
        return False
    if hp < maxhp / 4:
        return True
    heal = _ITEM_HEAL_AMOUNTS.get(item_ps_id)
    if heal is not None and (maxhp - hp) > heal:
        return True
    return False


def _opp_reservation_allows_item(state: dict) -> bool:
    """Return True if the pokeemerald reservation system allows item use this turn.

    pokeemerald skips item slot i when i != 0 AND validMons > (itemsNo - i) + 1.
    The slot index equals how many items have already been used.
    """
    remaining = state.get('opp_items_remaining', 0)
    initial   = state.get('opp_items_initial', remaining)
    used      = initial - remaining
    if used == 0:
        return True   # slot 0 is always allowed
    # Count living opponent pokemon from the PS state.
    opp_side  = state.get('battle', {}).get('sides', [{}, {}])[1]
    valid_mons = sum(1 for p in opp_side.get('pokemon', []) if p.get('hp', 0) > 0)
    return valid_mons <= (initial - used) + 1

NUM_SAMPLES = 30
MAX_DEPTH = 3
MATCHUP_WEIGHT: float = 0  # Scale factor applied to matchup cache switch bias

class _MatchupCacheState:
    """Process-local cache for loaded MatchupInfo objects, keyed by file path."""

    def __init__(self) -> None:
        self._path: Optional[str] = None
        self._obj = None

    def load(self, path):
        """Return cached MatchupInfo for path, loading from disk if needed.

        Returns None if path is None, loading fails, or module is unavailable.
        Caches result by path so repeated calls within the same process are free.
        """
        if path is None:
            return None
        if path == self._path:
            return self._obj
        try:
            import pickle
            from MatchupInfo import MatchupInfo
            with open(path, 'rb') as f:
                obj = pickle.load(f)
            if not isinstance(obj, MatchupInfo):
                raise TypeError(f'Expected MatchupInfo, got {type(obj).__name__}')
            self._obj = obj
            self._path = path
            print(f'[matchup] Loaded cache from {path}', file=sys.stderr)
            return self._obj
        except Exception as e:
            print(f'[matchup] Failed to load cache from {path}: {e}', file=sys.stderr)
            self._obj = None
            self._path = path  # remember path so we don't retry every turn
            return None

_MATCHUP_CACHE = _MatchupCacheState()

# Fraction of outcome probability mass to consider when scoring an action, measured from
# the worst outcome upward.  1.0 = full expected value (current behaviour).  Lower values
# make the search progressively more risk-averse by ignoring lucky high-score scenarios.
PERCENTILE_CUTOFF = 1.0
HP_BIN = 5
# Set to True to print final-turn score variance statistics after each search.
VARIANCE_ANALYSIS = False

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_state(state: dict, early_win_bonus: float = 0.0) -> float:
    """Score a battle state from p1's perspective.

    Scores battle state from p1's perspective (HP%, faint counts, boosts).
    early_win_bonus: added to a p1 win score when the win is found before
    MAX_DEPTH turns. Caller computes 0.1 * (MAX_DEPTH - turn_num) so that
    a T1 win gets +0.2 and a T2 win gets +0.1, preferring faster wins.
    """
    sides = state["battle"].get("sides", [])
    if len(sides) < 2:
        return 0.0

    p1_pokemon = sides[0].get("pokemon", [])
    p2_pokemon = sides[1].get("pokemon", [])

    def hp_fraction(p):
        maxhp = p.get("maxhp", 1) or 1
        return p.get("hp", 0) / maxhp

    p1_hp_sum = sum(hp_fraction(p) for p in p1_pokemon)
    p2_hp_sum = sum(hp_fraction(p) for p in p2_pokemon)
    p1_fainted = sum(1 for p in p1_pokemon if p.get("hp", 0) <= 0)
    p2_fainted = sum(1 for p in p2_pokemon if p.get("hp", 0) <= 0)

    if p2_fainted == len(p2_pokemon) and p2_pokemon:
        return 20.0 - p1_fainted * 20 + early_win_bonus

    score = 0.0

    score += p1_hp_sum
    score -= p2_hp_sum * 3
    score -= p1_fainted * 20
    score += p2_fainted

    numP1Statuses = sum(1 for p in p1_pokemon if p.get("status", "") != "")
    numP2Statuses = sum(1 for p in p2_pokemon if p.get("status", "") != "")
    score -= numP1Statuses / 2
    score += numP2Statuses / 2

    numP1Boosts = sum(
        sum(p["boosts"].values())
        for p in p1_pokemon
        if p.get("isActive")
    )
    numP2Boosts = sum(
        sum(p["boosts"].values())
        for p in p2_pokemon
        if p.get("isActive")
    )
    score += numP1Boosts * .3
    score -= numP2Boosts * .3

    return score


def _is_switch_only(state: dict) -> bool:
    """Return True when the state requires a forced player switch.

    A forced switch has p1_switches available but no p1_moves (the active
    Pokémon fainted) and the battle is still ongoing.  These nodes do not
    consume a search depth slot — they are re-expanded at the same depth.
    """
    return (
        bool(state.get("p1_switches"))
        and not state.get("p1_moves")
        and not state.get("is_over")
    )


# ---------------------------------------------------------------------------
# State fingerprinting
# ---------------------------------------------------------------------------

def _bin_hp(pokemon: dict) -> int:
    maxhp = pokemon.get("maxhp", 1) or 1
    frac = pokemon.get("hp", 0) / maxhp
    if frac >= 1.0:
        return 100
    if frac <= 0.0:
        return 0
    return max(HP_BIN, int(frac * 100 // HP_BIN) * HP_BIN)


def _bin_pp(pp: int, maxpp: int) -> str:
    if maxpp == 0:
        return "empty"
    frac = pp / maxpp
    if frac > 0.5:
        return ">50%"
    if frac > 0.1:
        return ">10%"
    if frac > 0.0:
        return ">0%"
    return "empty"


def _pokemon_fp(pokemon: dict) -> tuple:
    hp = _bin_hp(pokemon)
    pp_bins = tuple(
        _bin_pp(ms["pp"], ms["maxpp"])
        for ms in pokemon.get("moveSlots", [])
    )
    status = pokemon.get("status", "")
    volatiles = frozenset(pokemon.get("volatiles", {}).keys())
    boosts_dict = pokemon.get("boosts", {})
    boosts = tuple(boosts_dict[k] for k in sorted(boosts_dict))
    item = pokemon.get("item", "")
    return (hp, pp_bins, status, volatiles, boosts, item)


def StateFingerprint(state: dict) -> tuple:
    """Hashable summary of a battle state for deduplication."""
    battle = state["battle"]
    sides = battle.get("sides", [])

    side_fps = tuple(
        tuple(_pokemon_fp(p) for p in side.get("pokemon", []))
        for side in sides
    )

    weather = battle.get("field", {}).get("weather", "")
    side_conditions = tuple(
        repr(side.get("sideConditions", {})) for side in sides
    )

    return (side_fps, weather, side_conditions)


# ---------------------------------------------------------------------------
# Tree node
# ---------------------------------------------------------------------------

@dataclass
class TurnNode:
    state: object                    # dict during expansion; None after state is released
    turn: int                        # 0=root, 1, 2, or MAX_DEPTH
    action: str                      # immediate player action taken to reach this node ("" for root)
    children: dict = field(default_factory=dict)   # dict[str, dict[fingerprint, tuple[TurnNode, int]]]
    score: Optional[float] = None    # None until GetScore sets it; leaf score set at construction
    action_scores: dict = field(default_factory=dict)   # dict[str, float], set by GetScore
    best_action: str = ""            # action with highest score, set by GetScore


# ---------------------------------------------------------------------------
# Opponent weight helper
# ---------------------------------------------------------------------------

def _get_opp_weights(state: dict) -> list[tuple[Optional[str], float]]:
    """Return [(opp_action, probability)] for the opponent this turn.

    If _p2_forced is set in the state (e.g. the opponent is known to use a battle
    item this turn), that action is returned with probability 1.0, matching the
    behaviour of simulate_turn which checks the same key.

    If the opponent has items remaining and their active pokemon meets the item
    threshold (and the pokeemerald reservation system allows it), the item action
    is returned with probability 1.0.

    Raises RuntimeError if no opponent actions are available (shouldn't happen
    in a live battle before the battle is over unless the player is in a forced switch).
    """
    forced = state.get("known_p2")
    #print("forced-:", forced)
    if forced:
        print("forced:", forced, file=sys.stderr)
        return [(forced, 1.0)]
    # Check predicted item use before normal move/switch scoring.
    item_ps_id = state.get('opp_item_ps_id', '')
    if state.get('opp_items_remaining', 0) > 0 and item_ps_id:
        opp_side   = state.get('battle', {}).get('sides', [{}, {}])[1]
        opp_active = _get_active_pokemon(opp_side)
        if (opp_active is not None
                and _opp_item_threshold_met(opp_active, item_ps_id)
                and _opp_reservation_allows_item(state)):
            return [(f'item {item_ps_id}', 1.0)]
    ai_flags = state.get('opp_ai_flags')
    if state.get("p2_moves"):
        candidates = get_p2_move_candidates(state, state["p2_moves"], player=2,
                                            active_flags=ai_flags)
        return candidates if candidates else [(state["p2_moves"][0], 1.0)]
    if state.get("p2_switches"):
        action = _pick_opponent_action(state, active_flags=ai_flags)
        return [(action, 1.0)]
    if state.get("p1_switches") and state['battle'].get("requestState") == "switch":
        return [(None, 1.0)]
    raise RuntimeError(
        "no opponent actions available — state should be terminal before this point"
    )


def _print_opp_actions(state: dict) -> None:
    """Print the opponent's available actions and probabilities.

    Mirrors _get_opp_weights: known_p2 takes priority over move/switch inference.
    """
    known_p2 = state.get("known_p2")
    if known_p2 and str(known_p2).startswith("item "):
        print(f'[shallow_search] Opp: using item')
        print(f'  {known_p2[5:]}  prob=1.00')
        return
    if known_p2:
        print(f'[shallow_search] Opp: forced action')
        print(f'  {known_p2}  prob=1.00')
        return
    ai_flags = state.get('opp_ai_flags')
    if state.get("p2_moves"):
        print('[shallow_search] Opp move probabilities:')
        get_p2_move_candidates(state, state["p2_moves"], player=2,
                               active_flags=ai_flags, log=True)
    elif state.get("p2_switches"):
        action = _pick_opponent_action(state, active_flags=ai_flags)
        print('[shallow_search] Opp switch probabilities:')
        print(f'  {action}  prob=1.00')
    else:
        print('[shallow_search] Opp: forced p1 switch (no opp action)')


# ---------------------------------------------------------------------------
# IPC connection pool
# ---------------------------------------------------------------------------

class _IPCPool:
    """Fixed pool of NodeIPC connections, each backed by its own Node.js process."""

    def __init__(self, node_script_path: str, n: int):
        from NodeIPC import NodeIPC
        self._lock = threading.Lock()
        self._pool = [NodeIPC(node_script_path) for _ in range(n)]

    def acquire(self):
        while True:
            with self._lock:
                if self._pool:
                    return self._pool.pop()
            time.sleep(0.001)

    def release(self, ipc):
        with self._lock:
            self._pool.append(ipc)

    def close_all(self):
        with self._lock:
            for ipc in self._pool:
                ipc.close()
            self._pool.clear()


# ---------------------------------------------------------------------------
# Parallel expansion
# ---------------------------------------------------------------------------

# Each entry: (state_result_key, flag_when_forced_true, flag_when_forced_false)
_FACTOR_SPECS = [
    ("p1_crit_chance",      "P1ForceCrit",   "P1NoCrit"),
    ("p2_crit_chance",      "P2ForceCrit",   "P2NoCrit"),
    ("p1_accuracy_chance",  "P1ForceHit",    "P1ForceMiss"),
    ("p2_accuracy_chance",  "P2ForceHit",    "P2ForceMiss"),
    ("p1_secondary_chance", "P1ForceEffect", "P1NoEffect"),
    ("p2_secondary_chance", "P2ForceEffect", "P2NoEffect"),
]


def _branch_weight(chance, forced: bool) -> float:
    """Return the probability weight for one binary RNG branch (crit, accuracy, effect).

    Args:
        chance: Simulator-returned probability (0–1), or None if branch never triggered.
        forced: True if the "force" flag was used; False if the "prevent" flag was used.

    If chance is None the branch was not applicable this turn → weight 1.0.
    """
    if chance is None or chance == 0.0 or chance == 1.0:
        return 1.0
    return chance if forced else (1.0 - chance)


class UniformSampler:
    """Samples each RNG factor independently with a 50/50 coin flip each time.

    Opponent action is sampled from the original probability distribution each step
    (q_k = p_k), so no importance weight is needed for the opponent factor.
    Binary RNG factors are reweighted by the true simulator-returned probability.
    This is the baseline behaviour.
    """

    def __init__(self, n_samples: int):
        self._n = n_samples

    def run(self, ipc, node_state: dict, p1_action: str, opp_weights: list,
            is_last_turn: bool = False) -> list:
        """Run self._n simulations and return list of (item, fingerprint, weight).

        Normal turns:  item = state_dict.
        Last turn:     item = score_float (state never stored).
        """
        opp_actions = [a for a, _ in opp_weights]
        opp_probs   = [p for _, p in opp_weights]
        results = []
        for _ in range(self._n):
            opp_action = random.choices(opp_actions, weights=opp_probs)[0]

            forced = [random.random() < 0.5 for _ in _FACTOR_SPECS]
            flags = {"P1QuantizedRNG": True, "P2QuantizedRNG": True}
            for (_, flag_true, flag_false), f in zip(_FACTOR_SPECS, forced):
                flags[flag_true]  = f
                flags[flag_false] = not f

            result = simulate_turn(ipc, node_state, p1_action, opp_action, flags=flags)

            weight = 1.0
            for (state_key, _, _), f in zip(_FACTOR_SPECS, forced):
                weight *= _branch_weight(result[state_key], f)

            fingerprint = StateFingerprint(result)
            if is_last_turn and not _is_switch_only(result):
                results.append((_score_state(result), fingerprint, weight))
            else:
                results.append((result, fingerprint, weight))
        return results


@dataclass
class FactorTracker:
    """Bayesian Beta-posterior tracker for one binary RNG factor.

    Maintains an online estimate of the true probability p_hat and computes
    adjusted sampling probabilities q(i) that steer the empirical frequency
    toward p_hat without fixing it.

    Fields:
        a, b    — Beta prior hyperparameters (default 1.0 each, weak uniform prior).
        N       — total sample budget for this group.
        s       — number of eligible samples where forced=True so far.
        n       — total number of eligible samples so far.
    """
    a: float
    b: float
    N: int
    s: int = 0
    n: int = 0

    @property
    def p_hat(self) -> float:
        """Current probability estimate: (a + s) / (a + b + n)."""
        return (self.a + self.s) / (self.a + self.b + self.n)

    def q(self, i: int) -> float:
        """Adjusted sampling probability for sample i (0-based).

        Pulls the remaining empirical rate toward p_hat.  Clamped to [0, 1].
        """
        remaining = self.N - i
        if remaining <= 0:
            return self.p_hat
        raw = (self.p_hat * self.N - self.s) / remaining
        return max(0.0, min(1.0, raw))

    def update(self, forced: bool) -> None:
        """Record one eligible sample."""
        self.n += 1
        if forced:
            self.s += 1

    def skip(self) -> None:
        """Record that one stratum sample was ineligible for this factor.

        Decrements N so future q() calls see the correct remaining budget.
        Without this, N - local_i overstates the remaining eligible slots,
        causing q() to over-compensate in later samples.
        """
        self.N = max(self.n, self.N - 1)


@dataclass
class CategoricalTracker:
    """Dirichlet-posterior tracker for categorical opponent move selection.

    Generalises FactorTracker to K outcomes.  At each step, computes adjusted
    sampling probabilities q_k(i) that steer the empirical frequencies toward the
    current p_hat_k estimates, then renormalises so they sum to 1.

    Fields:
        priors  — [a_k] Dirichlet prior counts; initialised from known move probs
                  scaled by a concentration parameter.
        N       — total sample budget.
        counts  — [s_k] eligible samples where action k was chosen.
        n       — total eligible samples.
    """
    priors: list   # list[float]
    N: int
    counts: list   # list[int]
    n: int = 0

    @property
    def p_hats(self) -> list:
        """Current probability estimates: (a_k + s_k) / (sum(a) + n) for each k."""
        denom = sum(self.priors) + self.n
        return [(a + s) / denom for a, s in zip(self.priors, self.counts)]

    def q_values(self, i: int) -> list:
        """Adjusted sampling probabilities for sample i, renormalised to sum=1."""
        p_hats = self.p_hats
        remaining = self.N - i
        if remaining <= 0:
            return p_hats
        raw = [
            max(0.0, min(1.0, (ph * self.N - sk) / remaining))
            for ph, sk in zip(p_hats, self.counts)
        ]
        total = sum(raw)
        if total == 0.0:
            return p_hats  # fallback: use posterior directly
        return [q / total for q in raw]

    def update(self, k: int) -> None:
        """Record one eligible sample where action k was chosen."""
        self.n += 1
        self.counts[k] += 1


# ---------------------------------------------------------------------------
# Factor prior helpers
# ---------------------------------------------------------------------------

_CRIT_A = 2.0 * (1.0 / 16.0)   # Beta prior centered at 1/16, concentration=2
_CRIT_B = 2.0 * (15.0 / 16.0)


def _move_priors_from_info(info: dict | None) -> tuple:
    """Return (accuracy_frac, secondary_frac) from one p1MoveInfo/p2MoveInfo entry."""
    if not info:
        return (1.0, 0.0)
    acc = info.get("accuracy")
    sec = info.get("secondaryChance")
    return (
        acc / 100.0 if acc is not None else 1.0,
        sec / 100.0 if sec is not None else 0.0,
    )


def _factor_priors(p1_action: str, p2_action, node_state: dict) -> list:
    """Return 6 (a, b) Beta prior pairs for FactorTracker, in _FACTOR_SPECS order.

    Order: p1_crit, p2_crit, p1_accuracy, p2_accuracy, p1_secondary, p2_secondary.
    All priors use concentration=2 (a + b = 2).  Crit is always 1/16.  Accuracy
    and secondary are derived from the IPC move info for the specific actions used.
    Called once per opponent-action stratum so each stratum gets the correct p2 prior.
    """
    def _get(action, infos):
        if action and str(action).startswith("move "):
            slot = int(str(action).split()[1]) - 1
            return infos[slot] if slot < len(infos) else None
        return None

    p1_acc, p1_sec = _move_priors_from_info(_get(p1_action, node_state.get("p1_move_info", [])))
    p2_acc, p2_sec = _move_priors_from_info(_get(p2_action, node_state.get("p2_move_info", [])))

    def _ab(p: float) -> tuple:
        return (p * 2.0, (1.0 - p) * 2.0)

    return [
        (_CRIT_A, _CRIT_B),   # p1 crit
        (_CRIT_A, _CRIT_B),   # p2 crit
        _ab(p1_acc),           # p1 accuracy
        _ab(p2_acc),           # p2 accuracy
        _ab(p1_sec),           # p1 secondary
        _ab(p2_sec),           # p2 secondary
    ]


class StratifiedSampler:
    """Sequential stratified sampler with online Beta-prior updating.

    For each binary RNG factor the sampling probability is adjusted each step
    so the empirical True-rate converges toward the simulator-returned probability
    rather than staying at 50/50.  Post-hoc importance reweighting corrects for
    the non-uniform sampling probabilities.

    Two-pass algorithm:
      Pass 1 — run all N samples, track per-factor forced/q/eligibility.
      Pass 2 — compute final p_hat values, then compute importance weights.
    """

    def __init__(self, n_samples: int):
        self._n = n_samples

    def run(self, ipc, node_state: dict, p1_action: str, opp_weights: list,
            is_last_turn: bool = False) -> list:
        """Run self._n simulations and return list of (item, fingerprint, weight).

        Opponent action is stratified via CategoricalTracker (global index i).
        One set of 6 FactorTrackers is maintained per opponent-action stratum so that
        binary RNG priors and empirical estimates are conditioned on which move the
        opponent actually used.  Each stratum tracker uses N_k = round(N * p_k) as its
        budget.  When a binary factor is ineligible for a sample, skip() decrements
        that tracker's N so future q() calls see the correct remaining eligible budget.

        Normal turns:  item = state_dict.
        Last turn:     item = score_float (state never stored).
        """
        opp_actions  = [a for a, _ in opp_weights]
        opp_probs    = [p for _, p in opp_weights]
        opp_is_multi = len(opp_weights) > 1

        # Opponent categorical tracker — stratifies over global samples
        if opp_is_multi:
            opp_tracker = CategoricalTracker(
                priors=[p * 2.0 for p in opp_probs],
                N=self._n,
                counts=[0] * len(opp_actions),
            )

        # Per-stratum binary factor tracker sets — one per opponent action
        strata = []
        for opp_action, prob in opp_weights:
            n_k = max(1, round(self._n * prob))
            priors = _factor_priors(p1_action, opp_action, node_state)
            strata.append({
                'trackers': [FactorTracker(a=a, b=b, N=n_k) for (a, b) in priors],
                'count': 0,   # samples seen in this stratum so far
            })

        # Pass 1: simulate all samples
        # Records: (item, fingerprint, forced_list, q_list, eligible_list, opp_action_idx, opp_sampling_prob, opp_action_eligible)
        records = []
        for i in range(self._n):
            # Sample opponent action (stratified via CategoricalTracker)
            if opp_is_multi:
                qs    = opp_tracker.q_values(i)
                opp_action_idx = random.choices(range(len(opp_actions)), weights=qs)[0]
                opp_sampling_prob = qs[opp_action_idx]
            else:
                opp_action_idx, opp_sampling_prob = 0, 1.0
            opp_action = opp_actions[opp_action_idx]

            stratum  = strata[opp_action_idx]
            trackers = stratum['trackers']
            local_i  = stratum['count']

            # Sample binary factors using this stratum's per-step q values
            forced_list, q_list = [], []
            for tracker in trackers:
                q_i = tracker.q(local_i)
                f = random.random() < q_i
                forced_list.append(f)
                q_list.append(q_i)

            flags = {"P1QuantizedRNG": True, "P2QuantizedRNG": True}
            for (_, flag_true, flag_false), f in zip(_FACTOR_SPECS, forced_list):
                flags[flag_true]  = f
                flags[flag_false] = not f

            result = simulate_turn(ipc, node_state, p1_action, opp_action, flags=flags)

            # Binary factor eligibility + updates (within this stratum only)
            # skip() adjusts N when ineligible so future q() sees the correct budget
            eligible_list = []
            for j, (state_key, _, _) in enumerate(_FACTOR_SPECS):
                chance = result[state_key]
                eligible = chance is not None and chance != 0.0 and chance != 1.0
                eligible_list.append(eligible)
                if eligible:
                    trackers[j].update(forced_list[j])
                else:
                    trackers[j].skip()

            stratum['count'] += 1

            # Opponent eligibility: move actually executed (not a switch, not OHKOd first)
            opp_action_eligible = (
                opp_is_multi
                and opp_action is not None
                and str(opp_action).startswith("move")
                and not result.get("is_over")
                and not (result.get("p2_switches") and not result.get("p2_moves"))
            )
            if opp_action_eligible:
                opp_tracker.update(opp_action_idx)

            fingerprint = StateFingerprint(result)
            item = _score_state(result) if (is_last_turn and not _is_switch_only(result)) else result
            records.append((item, fingerprint, forced_list, q_list, eligible_list,
                            opp_action_idx, opp_sampling_prob, opp_action_eligible))

        # Pass 2: importance weights = binary factor weights × opponent move weight
        final_p_hats     = [[t.p_hat for t in s['trackers']] for s in strata]
        final_opp_p_hats = opp_tracker.p_hats if opp_is_multi else []

        results = []
        for item, fingerprint, forced_list, q_list, eligible_list, opp_action_idx, opp_sampling_prob, opp_action_eligible in records:
            weight = 1.0
            p_hats = final_p_hats[opp_action_idx]
            for j, eligible in enumerate(eligible_list):
                if not eligible:
                    continue
                p     = p_hats[j]
                q_i   = q_list[j]
                f     = forced_list[j]
                denom = q_i if f else (1.0 - q_i)
                numer = p  if f else (1.0 - p)
                if denom == 0.0:
                    weight = 0.0
                    break
                weight *= numer / denom
            if weight != 0.0 and opp_action_eligible:
                if opp_sampling_prob == 0.0:
                    weight = 0.0
                else:
                    weight *= final_opp_p_hats[opp_action_idx] / opp_sampling_prob
            results.append((item, fingerprint, weight))

        return results


def _run_group(ipc_pool, node_state: dict, p1_action: str, opp_weights: list,
               n_samples: int, is_last_turn: bool = False):
    """Run n_samples simulations for one (p1_action, opp_weights) group.

    Opponent action sampling is handled inside the sampler; opp_weights is the full
    list of (action, probability) pairs for this turn.

    Acquires an IPC connection from the pool, delegates to SAMPLER_CLASS, releases
    the connection, and returns a 2-tuple:
      (results, stddev)
    where results is the list of (item, fingerprint, weight) tuples and stddev is the
    sample standard deviation of the scores when is_last_turn=True and VARIANCE_ANALYSIS
    is enabled, otherwise None.

    Normal turns:  item = state_dict.
    Last turn:     item = score_float (state never stored).
    """
    ipc = ipc_pool.acquire()
    try:
        sampler = SAMPLER_CLASS(n_samples)
        results = sampler.run(ipc, node_state, p1_action, opp_weights, is_last_turn)
    finally:
        ipc_pool.release(ipc)

    mean_stddev = None
    if is_last_turn and VARIANCE_ANALYSIS:
        # Filter to float items only — switch-only results arrive as dicts and must be excluded.
        float_results = [(s, fingerprint, w) for s, fingerprint, w in results if not isinstance(s, dict)]
        n = len(float_results)
        total_weight = sum(w for _, _fp, w in float_results)
        if n >= 2 and total_weight > 0:
            mu = sum(s * w for s, _fp, w in float_results) / total_weight
            var = sum(w * (s - mu) ** 2 for s, _fp, w in float_results) / total_weight
            mean_stddev = (mu, var ** 0.5)
        else:
            mu = sum(s for s, _fp, _w in float_results) / n if n else 0.0
            mean_stddev = (mu, 0.0)

    return results, mean_stddev


def _print_variance_analysis(variance_records: list) -> None:
    """Print final-turn score and stddev statistics grouped two ways.

    variance_records is a list of (parent_node, p1_action, mean, stddev) tuples
    produced in parallel by _run_group.  For each group reports aggregate statistics
    (average, median, 10th/90th percentile, count) over both the score means and
    the stddev values:

    1. Grouped by (T2 prior action → final action) — e.g. "move 1 → move 2"
    2. Grouped by final action alone — e.g. "move 1", "move 2", ...
    """
    from collections import defaultdict

    def _percentile(sorted_vals: list, p: float) -> float:
        n = len(sorted_vals)
        if n == 1:
            return sorted_vals[0]
        idx = p * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        return sorted_vals[lo] + (idx - lo) * (sorted_vals[hi] - sorted_vals[lo])

    # Each bucket stores (means_list, stddevs_list)
    by_prior_final: dict = defaultdict(lambda: ([], []))
    by_final:       dict = defaultdict(lambda: ([], []))
    for parent_node, p1_action, mu, sd in variance_records:
        prior = parent_node.action
        by_prior_final[(prior, p1_action)][0].append(mu)
        by_prior_final[(prior, p1_action)][1].append(sd)
        by_final[p1_action][0].append(mu)
        by_final[p1_action][1].append(sd)

    def _print_stats(label: str, means: list, stddevs: list) -> None:
        n = len(means)
        if n == 0:
            return

        def _agg(vals):
            s = sorted(vals)
            return (sum(vals) / n,
                    max(vals),
                    _percentile(s, 0.5),
                    _percentile(s, 0.1),
                    _percentile(s, 0.9))

        m_avg, m_max, m_med, m_bot, m_top = _agg(means)
        s_avg, s_max, s_med, s_bot, s_top = _agg(stddevs)
        print(f"  {label:<40s}"
              f"  score: avg={m_avg:.4f} max={m_max:.4f} med={m_med:.4f} bot10%={m_bot:.4f} top10%={m_top:.4f}"
              f"  |  stddev: avg={s_avg:.4f} max={s_max:.4f} med={s_med:.4f} bot10%={s_bot:.4f} top10%={s_top:.4f}"
              f"  n={n}",
              file=sys.stderr)

    print("[variance] By (T2 prior action → final action):", file=sys.stderr)
    for (prior, final), (means, sds) in sorted(by_prior_final.items()):
        _print_stats(f"{prior} -> {final}", means, sds)

    print("[variance] By final action:", file=sys.stderr)
    for final, (means, sds) in sorted(by_final.items()):
        _print_stats(final, means, sds)


def expand_turn_parallel(
    frontier: list[TurnNode],
    ipc_pool: _IPCPool,
    turn_num: int,
    n_workers: int,
    is_last_turn: bool = False,
    numSamples = NUM_SAMPLES
) -> list[TurnNode]:
    """Expand one depth level in parallel.

    For each node in frontier, samples all (p1_action, opp_action) combinations.
    Uses per-turn deduplication: a single canonical TurnNode is created per unique
    fingerprint at this depth; that node may be referenced from multiple parents.
    Parent children counts are tracked per (parent, action, fingerprint).

    Clears node.state for all frontier nodes after expansion (memory management).
    Leaf nodes (is_last_turn=True) never store state — they are constructed with
    state=None and score pre-computed inside _run_group.
    """
    t0 = time.perf_counter()

    tasks = []
    for node in frontier:
        if node.state is not None and node.state.get("is_over"):
            # Early win bonus: 0.1 per turn ahead of MAX_DEPTH (T1→+0.2, T2→+0.1, T3→+0.0)
            bonus = 0.1 * (MAX_DEPTH - turn_num) if turn_num < MAX_DEPTH else 0.0
            node.score = _score_state(node.state, early_win_bonus=bonus)
            continue

        p1_moves    = list(node.state.get("p1_moves")    or [])
        p1_switches = list(node.state.get("p1_switches") or [])
        if p1_moves:
            # Normal turn: add safe voluntary switches (empty if switching not allowed)
            p1_actions = p1_moves + get_voluntary_switches(node.state, p1_switches)
        else:
            # Forced faint-switch: no moves available, switches are the only option
            p1_actions = p1_switches
        if not p1_actions:
            continue

        opp_weights = _get_opp_weights(node.state)

        for p1_action in p1_actions:
            tasks.append((node, p1_action, opp_weights, numSamples))

    # Parallel phase: run simulation groups
    raw_results      = []  # list of (parent_node, p1_action, item, fingerprint, weight)
    variance_records = []  # list of (parent_node, p1_action, mean, stddev) — last turn only

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        future_map = {}
        for node, p1_action, opp_wts, n_samples in tasks:
            fut = executor.submit(_run_group, ipc_pool, node.state,
                                  p1_action, opp_wts, n_samples, is_last_turn)
            future_map[fut] = (node, p1_action)

        for fut in as_completed(future_map):
            node, p1_action = future_map[fut]
            group_results, group_mean_stddev = fut.result()
            for item, fingerprint, weight in group_results:
                raw_results.append((node, p1_action, item, fingerprint, weight))
            if group_mean_stddev is not None:
                mu, sd = group_mean_stddev
                variance_records.append((node, p1_action, mu, sd))

    elapsed1 = time.perf_counter() - t0
    t1 = time.perf_counter()

    # Sequential dedup phase
    seen: dict = {}   # fingerprint -> TurnNode (canonical node at this depth)
    new_nodes = []
    total_samples = len(raw_results)
    total_new = total_dupes = 0

    for parent_node, p1_action, item, fingerprint, weight in raw_results:
        node = seen.get(fingerprint)
        if node is None:
            if isinstance(item, dict):
                # State dict: either a normal non-leaf or a switch-only result that
                # bypassed scoring even at is_last_turn — must retain state for re-expansion.
                node = TurnNode(state=item, turn=turn_num, action=p1_action)
            else:
                # Float score: a genuine scored leaf node.
                node = TurnNode(state=None, turn=turn_num, action=p1_action, score=item)
            seen[fingerprint] = node
            new_nodes.append(node)
            total_new += 1
        else:
            total_dupes += 1

        # Update (parent, action) → fingerprint accumulated weight (float)
        if p1_action not in parent_node.children:
            parent_node.children[p1_action] = {}
        fp_dict = parent_node.children[p1_action]
        if fingerprint in fp_dict:
            existing_node, accumulated_weight = fp_dict[fingerprint]
            fp_dict[fingerprint] = (existing_node, accumulated_weight + weight)
        else:
            fp_dict[fingerprint] = (node, weight)

    # Release state memory for frontier nodes (leaf states were never stored)
    for node in frontier:
        node.state = None

    elapsed2 = time.perf_counter() - t1
    print(
        f"[shallow_search] Turn {turn_num}: {total_samples} samples, "
        f"{total_new} new states, {total_dupes} duplicates, "
        f"{elapsed1:.2f}s parallel, {elapsed2:.2f}s dedup",
        file=sys.stderr,
    )

    if variance_records:
        _print_variance_analysis(variance_records)

    return new_nodes


# ---------------------------------------------------------------------------
# Recursive scoring
# ---------------------------------------------------------------------------

def GetScore(node: TurnNode) -> float:
    """Recursively compute and cache expected scores bottom-up.

    Sets node.score, node.action_scores, and node.best_action in place.
    Leaf nodes have score pre-set at construction; this acts as the base case
    via the memoization check.

    Returns node.score.
    """
    if node.score is not None:
        return node.score

    # Non-leaf: aggregate over children, normalizing by accumulated probability weight.
    # If PERCENTILE_CUTOFF < 1.0, only the worst PERCENTILE_CUTOFF fraction of outcomes
    # (by probability mass) is considered, discarding lucky high-score scenarios.
    for p1_action, fp_dict in node.children.items():
        score_weight_pairs = [(GetScore(child), weight) for _, (child, weight) in fp_dict.items()]

        total_weight = sum(weight for _, weight in score_weight_pairs)
        if total_weight == 0:
            # Degenerate: all samples had zero weight — fall back to uniform average
            n = len(score_weight_pairs)
            node.action_scores[p1_action] = sum(s for s, _ in score_weight_pairs) / n if n else 0.0
            continue

        if PERCENTILE_CUTOFF < 1.0:
            # Sort ascending so worst outcomes come first, then keep enough states to
            # account for PERCENTILE_CUTOFF of the total probability mass.
            score_weight_pairs.sort(key=lambda x: x[0])
            keep_weight = PERCENTILE_CUTOFF * total_weight
            kept, accumulated = [], 0.0
            for score, weight in score_weight_pairs:
                kept.append((score, weight))
                accumulated += weight
                if accumulated >= keep_weight:
                    break
            score_weight_pairs = kept

        kept_weight = sum(weight for _, weight in score_weight_pairs)
        node.action_scores[p1_action] = sum(s * weight / kept_weight for s, weight in score_weight_pairs)

    if not node.action_scores:
        node.score = 0.0
        return node.score

    node.best_action = max(node.action_scores, key=node.action_scores.__getitem__)
    node.score = node.action_scores[node.best_action]
    return node.score


# ---------------------------------------------------------------------------
# Recursive score printing
# ---------------------------------------------------------------------------

def _print_turn_scores(parents: list, turn_num: int, matchup_details: dict | None = None) -> None:
    """Print aggregated action scores for one depth level.

    parents is a list of (TurnNode, cumulative_weight) pairs representing all
    reachable nodes at this depth along the best-action path from the root.
    Each node's action_scores are weighted by its cumulative reach probability.

    Per action prints:
      weighted mean, weighted stddev, unweighted stddev,
      from=number of parent states that had this action,
      to=number of unique result states this action leads to.
    Header shows total unique states involved in this turn's calculation.
    """
    # Collect parent-level (action_score, weight) pairs for the weighted mean,
    # and child-level (child_score, combined_weight) pairs for the stddev calculations.
    # The mean uses parent action_scores (which respect PERCENTILE_CUTOFF in GetScore).
    # The stddevs use child outcome scores so they reflect actual outcome variance.
    action_data:  dict = {}  # action -> list of (action_score, parent_weight)
    child_data:   dict = {}  # action -> list of (child_score, parent_weight * child_weight)
    child_fps:    dict = {}  # action -> set of unique child fingerprints (for to_n)

    for node, weight in parents:
        for action, score in node.action_scores.items():
            if action not in action_data:
                action_data[action] = []
            action_data[action].append((score, weight))

        for action, fp_dict in node.children.items():
            if action not in child_data:
                child_data[action]  = []
                child_fps[action]   = set()
            for fingerprint, (child, child_w) in fp_dict.items():
                child_fps[action].add(fingerprint)
                if child.score is not None:
                    child_data[action].append((child.score, weight * child_w))

    if not action_data:
        return

    num_states = len(parents)
    print(f"\n[shallow_search] Turn {turn_num} scores ({num_states} unique state{'s' if num_states != 1 else ''}):")

    results = []
    for action, action_score_pairs in action_data.items():
        from_n  = len(action_score_pairs)
        to_n    = len(child_fps.get(action, ()))
        total_weight = sum(weight for _, weight in action_score_pairs)

        # Weighted mean from parent action_scores (respects GetScore's PERCENTILE_CUTOFF)
        weighted_mean = sum(s * weight for s, weight in action_score_pairs) / total_weight if total_weight > 0 else 0.0

        # Stddev over child outcome scores (variance in what actually happens)
        child_score_pairs = child_data.get(action, [])
        child_total_weight = sum(cw for _, cw in child_score_pairs)
        nc = len(child_score_pairs)

        if child_total_weight > 0 and nc > 1:
            w_sd = (sum(cw * (s - weighted_mean) ** 2 for s, cw in child_score_pairs) / child_total_weight) ** 0.5
        else:
            w_sd = 0.0

        if nc > 1:
            u_mean = sum(s for s, _ in child_score_pairs) / nc
            u_sd = (sum((s - u_mean) ** 2 for s, _ in child_score_pairs) / nc) ** 0.5
        else:
            u_sd = 0.0

        results.append((action, weighted_mean, w_sd, u_sd, from_n, to_n))

    results.sort(key=lambda x: -x[1])
    best_action = results[0][0] if results else ""

    for action, weighted_mean, w_sd, u_sd, from_n, to_n in results:
        marker = " <- BEST" if action == best_action else ""
        matchup_str = ''
        if matchup_details and action in matchup_details:
            active_c, bench_c = matchup_details[action]
            total_c = active_c - bench_c
            matchup_str = f'  [matchup: active={active_c:+.3f}  bench={bench_c:+.3f}  total={total_c:+.3f}]'
        print(f"  {action:<22s}  score={weighted_mean:.4f}  wsd={w_sd:.4f}  sd={u_sd:.4f}"
              f"  from={from_n}  to={to_n}{matchup_str}{marker}")


def print_scores(root: TurnNode, matchup_details: dict | None = None) -> None:
    """Print aggregated action scores at each depth along the best-action path.

    For each turn level, aggregates action scores across all reachable states
    (weighted by cumulative reach probability) rather than following a single path.
    This shows what decisions truly drive the root action choice.

    Turn 1 output is identical to the old single-path version (one parent, weight=1).
    Turn 2+ aggregates across multiple states, showing weighted/unweighted stddevs
    and how many states contributed to each action.
    """
    parents = [(root, 1.0)]

    for turn_num in range(1, MAX_DEPTH + 1):
        if not any(node.action_scores for node, _ in parents):
            break

        _print_turn_scores(parents, turn_num,
                           matchup_details=matchup_details if turn_num == 1 else None)

        # Advance: follow each parent's best_action to its children, multiplying weights
        next_parents = []
        for node, w in parents:
            if not node.best_action or node.best_action not in node.children:
                continue
            for child, child_w in node.children[node.best_action].values():
                next_parents.append((child, w * child_w))

        parents = next_parents
        if not parents:
            break


# ---------------------------------------------------------------------------
# T1 action variance (called after GetScore)
# ---------------------------------------------------------------------------

def _print_action_variance(root: TurnNode) -> None:
    """Print weighted score distribution for each T1 action at the root.

    For each T1 action, collects the minimax scores of all reachable T2 children
    (set by GetScore) along with their accumulated transition weights, then reports:
      weighted mean, weighted stddev, weighted 10th/90th percentile, child count.

    All output goes to sys.stderr.
    """
    def _weighted_percentile(sorted_pairs: list, p: float) -> float:
        """Return the score at cumulative-weight fraction p (0–1)."""
        total_weight = sum(w for _, w in sorted_pairs)
        if total_weight == 0:
            return sorted_pairs[0][0] if sorted_pairs else 0.0
        target = p * total_weight
        accumulated = 0.0
        for score, w in sorted_pairs:
            accumulated += w
            if accumulated >= target:
                return score
        return sorted_pairs[-1][0]

    print("[variance] T1 action score distribution:", file=sys.stderr)
    for p1_action, fp_dict in sorted(root.children.items()):
        pairs = [
            (child.score, w)
            for _, (child, w) in fp_dict.items()
            if child.score is not None
        ]
        n = len(pairs)
        if n == 0:
            continue

        total_weight = sum(w for _, w in pairs)
        if total_weight > 0:
            mu = sum(s * w for s, w in pairs) / total_weight
            var = sum(w * (s - mu) ** 2 for s, w in pairs) / total_weight
            sd = var ** 0.5
            sorted_pairs = sorted(pairs, key=lambda x: x[0])
            bot10 = _weighted_percentile(sorted_pairs, 0.1)
            top10 = _weighted_percentile(sorted_pairs, 0.9)
        else:
            scores = [s for s, _ in pairs]
            mu = sum(scores) / n
            sd = 0.0
            bot10 = top10 = mu

        print(f"  {p1_action:<22s}  mean={mu:.4f}  stddev={sd:.4f}"
              f"  bot10%={bot10:.4f}  top10%={top10:.4f}  n={n}",
              file=sys.stderr)


# ---------------------------------------------------------------------------
# Search entry point (used by server loop below)
# ---------------------------------------------------------------------------

def _drain_switch_nodes(
    frontier: list,
    ipc_pool: _IPCPool,
    depth: int,
    n_workers: int,
    is_last_turn: bool,
) -> list:
    """Expand all switch-only nodes in the frontier at the same depth, looping until none remain.

    Switch-only nodes (forced faint-switch, no p1_moves) do not consume a depth slot.
    They are re-expanded immediately at the same depth with the same is_last_turn flag,
    and their children replace them in the frontier.  The while-loop handles consecutive
    forced switches (e.g. both players faint on the same simulated turn).

    Switch nodes remain in the tree — they are already linked as children of their parents
    by expand_turn_parallel.  Only the returned frontier is updated.
    expand_turn_parallel clears node.state for every node it expands, so processed switch
    nodes will not re-trigger the predicate in subsequent iterations.
    """
    while True:
        switch_nodes = [
            n for n in frontier
            if n.state is not None and _is_switch_only(n.state)
        ]
        if not switch_nodes:
            return frontier
        non_switch = [
            n for n in frontier
            if not (n.state is not None and _is_switch_only(n.state))
        ]
        expanded = expand_turn_parallel(switch_nodes, ipc_pool, depth, n_workers, is_last_turn, numSamples=3)
        frontier = non_switch + expanded


def _load_matchup_cache(path):
    """Load (or return cached) MatchupInfo from a pickle file."""
    return _MATCHUP_CACHE.load(path)


def _apply_matchup_bias(root: 'TurnNode', state: dict, matchup_cache) -> dict:
    """Bias switch action scores in root using the matchup cache.

    Applied after GetScore(root). Modifies root.action_scores in-place and
    recomputes root.best_action and root.score.

    Returns {action: (active_contrib, bench_contrib)} where both values are
    scaled by MATCHUP_WEIGHT (for use in the per-turn printout). Returns {}
    when no bias is applied. Returns {'_validation_failed': True} on cache
    mismatch so the caller can print a status line.
    """
    if matchup_cache is None:
        return {}
    # Mirror the action-building logic in expand_turn_parallel:
    # on a normal turn, voluntary switches come from get_voluntary_switches,
    # not directly from state['p1_switches'] (which IPC leaves empty for non-faint turns).
    p1_moves      = state.get('p1_moves') or []
    p1_switches_raw = state.get('p1_switches') or []
    if p1_moves:
        p1_switches = get_voluntary_switches(state, p1_switches_raw)
    else:
        p1_switches = p1_switches_raw
    if not p1_switches:
        return {}
    if not matchup_cache.validate_for_state(state):
        return {'_validation_failed': True}
    # {action: (active_avg, bench_avg)}
    avgs = matchup_cache.get_switch_biases(state, p1_switches)
    if not avgs:
        return {}
    details = {}
    for action, (active_avg, bench_avg) in avgs.items():
        bias = active_avg - bench_avg
        if action in root.action_scores:
            root.action_scores[action] += MATCHUP_WEIGHT * bias
        details[action] = (active_avg * MATCHUP_WEIGHT, bench_avg * MATCHUP_WEIGHT)
    # Recompute best action after bias
    root.best_action = max(root.action_scores, key=root.action_scores.__getitem__)
    root.score = root.action_scores[root.best_action]
    return details


def run_search(state: dict, node_script_path: str, num_workers: int,
               matchup_cache_path: str | None = None) -> tuple[str, dict]:
    """Run MAX_DEPTH-turn parallel shallow search. Returns (best_action, action_scores)."""
    cache = _load_matchup_cache(matchup_cache_path)
    # Per-search cache status (printed before the tree expansion starts)
    if cache is not None:
        print(f'[matchup] Cache active', file=sys.stderr)
    elif matchup_cache_path is not None:
        print(f'[matchup] Cache unavailable', file=sys.stderr)

    ipc_pool = _IPCPool(node_script_path, num_workers)
    try:
        root = TurnNode(state=state, turn=0, action="")

        frontier = [root]
        for depth in range(1, MAX_DEPTH + 1):
            is_last = (depth == MAX_DEPTH)
            frontier = expand_turn_parallel(frontier, ipc_pool, depth, num_workers, is_last)
            frontier = _drain_switch_nodes(frontier, ipc_pool, depth, num_workers, is_last)

        GetScore(root)
        if MATCHUP_WEIGHT > 0:
            matchup_details = _apply_matchup_bias(root, state, cache)
        else:
            matchup_details = {}

        # Redirect stdout → stderr during printing so it doesn't corrupt the framing protocol
        _real_stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            _print_opp_actions(state)
            if matchup_details.get('_validation_failed'):
                print('[matchup] Cache validation failed — no bias applied')
            elif matchup_details:
                print(f'[matchup] Cache OK — bias applied to {len(matchup_details)} switch action(s)')
            elif cache is not None:
                print('[matchup] Cache OK — no switches to bias')
            print_scores(root, matchup_details=matchup_details)
            if VARIANCE_ANALYSIS:
                _print_action_variance(root)
            print()
        finally:
            sys.stdout = _real_stdout

        return root.best_action, root.action_scores
    finally:
        ipc_pool.close_all()


# ---------------------------------------------------------------------------
# Subprocess server loop
# ---------------------------------------------------------------------------

def _read_exact(n: int) -> bytes:
    buf = b''
    while len(buf) < n:
        chunk = sys.stdin.buffer.read(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf


def _send(obj: dict) -> None:
    data = json.dumps(obj).encode()
    sys.stdout.buffer.write(struct.pack('>I', len(data)) + data)
    sys.stdout.buffer.flush()


def main():
    """Long-lived server loop. Reads requests from stdin, writes responses to stdout."""
    while True:
        try:
            length = struct.unpack('>I', _read_exact(4))[0]
        except EOFError:
            break
        req = json.loads(_read_exact(length))
        action, action_scores = run_search(req['state'], req['node_script'], req['num_workers'],
                                           matchup_cache_path=req.get('matchup_cache'))
        _send({'action': action, 'action_scores': action_scores})


#Put it here so it's easier to find
SAMPLER_CLASS = StratifiedSampler

if __name__ == '__main__':
    main()
