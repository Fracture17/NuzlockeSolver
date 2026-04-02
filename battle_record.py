"""
battle_record.py

Data-only module for recording battle events during an automated test run.
No emulator or battle_mode imports — safe to import from anywhere.
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class BattleTurn:
    turn_number:       int
    p1_action:         str              # e.g. 'move 3'
    p2_action:         str              # e.g. 'item superpotion' or 'move 1'
    p1_move_name:      Optional[str]    # resolved move name or None
    p2_move_name:      Optional[str]    # resolved move name or None (None for items)
    skipped_reconcile: bool = False
    battle_texts:      list = field(default_factory=list)  # GBA messages during this turn


# Maps PS action item suffix → display name for assertion text matching
_ITEM_DISPLAY: dict[str, str] = {
    'potion':       'Potion',
    'superpotion':  'Super Potion',
    'hyperpotion':  'Hyper Potion',
    'maxpotion':    'Max Potion',
    'fullrestore':  'Full Restore',
    'fullheal':     'Full Heal',
}


def _state_diff(old, new) -> dict:
    """Recursive diff: returns only the sub-trees that changed.

    - Identical sub-trees are omitted entirely.
    - Dicts: recurse key-by-key; added/removed keys are noted.
    - Lists of equal length: recurse element-by-element; only changed
      indices are included, keyed by their string index. This ensures
      that a single Pokémon HP change inside sides[0].pokemon[2] is
      reported as {"sides": {"0": {"pokemon": {"2": {"hp": ...}}}}}
      rather than dumping the entire sides list.
    - Lists of different length or primitive leaves: {"old": x, "new": y}.
    """
    if old == new:
        return {}
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return {'old': old, 'new': new}
        diff = {}
        for i, (o, n) in enumerate(zip(old, new)):
            sub = _state_diff(o, n)
            if sub:
                diff[str(i)] = sub
        return diff
    if not isinstance(old, dict) or not isinstance(new, dict):
        return {'old': old, 'new': new}
    diff = {}
    for k in set(old) | set(new):
        if k not in old:
            diff[k] = {'added': new[k]}
        elif k not in new:
            diff[k] = {'removed': old[k]}
        elif old[k] != new[k]:
            sub = _state_diff(old[k], new[k])
            diff[k] = sub if sub else {'old': old[k], 'new': new[k]}
    return diff


def _norm(s: str) -> str:
    """Normalize text for substring matching: lowercase, collapse whitespace, strip punctuation."""
    s = s.lower()
    s = re.sub(r"[^\w\s]", '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


class BattleRecord:
    """Captures all events and diagnostic data during a single battle test run."""

    def __init__(self, savestate: str):
        self.savestate        = savestate
        self.turns:           list[BattleTurn] = []
        self.current_turn:    int              = 0
        self.faint_events:    list[dict]       = []
        self.output_lines:    list[str]        = []
        self.last_output_time: float           = time.monotonic()
        self.mismatch_count:  int              = 0
        self.outcome:         Optional[str]    = None
        self.error:           Optional[str]    = None
        self.assertions:      list[dict]       = []
        self._start_time:      float            = time.monotonic()
        self._pending_texts:   dict            = {}  # turn_number → list[str]
        self.faint_mismatches: list[dict]      = []
        self.state_snapshots:  list[dict]      = []  # {reason, turn, state}

    # ── Event callbacks ───────────────────────────────────────────────────────

    def on_reconcile(self, turn: int, p1: str, p2: str,
                     p1_name: Optional[str], p2_name: Optional[str],
                     skipped: bool = False) -> None:
        bt = BattleTurn(
            turn_number=turn,
            p1_action=p1,
            p2_action=p2,
            p1_move_name=p1_name,
            p2_move_name=p2_name,
            skipped_reconcile=skipped,
        )
        bt.battle_texts = self._pending_texts.pop(turn, [])
        self.turns.append(bt)

    def on_mcts_decision(self, turn: int, action: str, stats: list) -> None:
        # Store stats on the most recently recorded turn matching this turn number
        for t in reversed(self.turns):
            if t.turn_number == turn:
                t.mcts_action = action  # type: ignore[attr-defined]
                t.mcts_stats  = stats   # type: ignore[attr-defined]
                break

    def on_faint(self, turn: int, switch_action: str) -> None:
        self.faint_events.append({'turn': turn, 'switch_action': switch_action})

    def on_end(self, outcome: str) -> None:
        self.outcome = outcome

    def on_faint_mismatch(self, turn: int, attempt: int,
                          p1_action: str, p2_action: str,
                          pre_state: dict, ps_after: dict,
                          gba_player: list, gba_enemy: list,
                          p1_gba_order: list, p2_gba_order: list,
                          diffs: list) -> None:
        """Record a single faint mismatch retry event."""
        sides_after = ps_after['battle'].get('sides', [{}, {}])
        entry: dict = {
            'turn':           turn,
            'attempt':        attempt,
            'p1_action':      p1_action,
            'p2_action':      p2_action,
            'diffs':          diffs,
            'gba_player_hps': [p.get('current_hp', 0) for p in gba_player],
            'gba_enemy_hps':  [p.get('current_hp', 0) for p in gba_enemy],
            'ps_player_hps':  [p.get('hp', 0) for p in
                               sides_after[0].get('pokemon', [])] if sides_after else [],
            'ps_enemy_hps':   [p.get('hp', 0) for p in
                               sides_after[1].get('pokemon', [])] if len(sides_after) > 1 else [],
            'p1_gba_order':   list(p1_gba_order),
            'p2_gba_order':   list(p2_gba_order),
        }
        # Full state snapshots only on the first attempt per reconcile event — they're large
        if attempt == 1:
            entry['pre_battle']      = pre_state['battle']
            entry['ps_after_battle'] = ps_after['battle']
        self.faint_mismatches.append(entry)

    def on_state_update(self, reason: str, turn: int, state: dict) -> None:
        """Record a full ps_state snapshot whenever ps_state is reassigned."""
        self.state_snapshots.append({
            'reason': reason,
            'turn':   turn,
            'state':  state['battle'],
        })

    def add_battle_text(self, text: str) -> None:
        """Called from the frame loop when the GBA battle text changes."""
        if not text:
            return
        bucket = self._pending_texts.setdefault(self.current_turn, [])
        if not bucket or bucket[-1] != text:
            bucket.append(text)

    def add_output(self, line: str) -> None:
        """Called by OutputCapture for each line written to stdout."""
        self.output_lines.append(line)
        self.last_output_time = time.monotonic()
        if 'Faint mismatch' in line or 'faint mismatch' in line.lower():
            self.mismatch_count += 1

    # ── Assertions ────────────────────────────────────────────────────────────

    def run_assertions(self) -> None:
        """Cross-reference recorded turns against captured GBA battle texts."""
        for turn in self.turns:
            if turn.skipped_reconcile:
                continue

            all_texts_norm = [_norm(t) for t in turn.battle_texts]

            # Player move
            if turn.p1_move_name:
                expected = _norm(f'used {turn.p1_move_name}')
                passed = any(expected in t for t in all_texts_norm)
                self.assertions.append({
                    'turn': turn.turn_number,
                    'check': 'player move',
                    'passed': passed,
                    'expected': expected,
                    'actual': turn.battle_texts,
                })

            # Opponent move or item
            if turn.p2_action.startswith('item '):
                item_key  = turn.p2_action.split(' ', 1)[1]  # e.g. 'superpotion'
                item_disp = _ITEM_DISPLAY.get(item_key, item_key.replace('_', ' ').title())
                expected  = _norm(f'used {item_disp}')
                passed    = any(expected in t for t in all_texts_norm)
                self.assertions.append({
                    'turn': turn.turn_number,
                    'check': 'opponent item',
                    'passed': passed,
                    'expected': expected,
                    'actual': turn.battle_texts,
                })
            elif turn.p2_move_name:
                # Skip if the opponent fainted before they could act — no "used X"
                # text is ever shown in that case, so absence is not a failure.
                opp_fainted_first = any('fainted' in t for t in all_texts_norm)

                move_norm = _norm(turn.p2_move_name)
                if move_norm == 'focus punch':
                    # Focus Punch shows "tightening its focus" when charged,
                    # "lost its focus" when disrupted, or "used Focus Punch" when
                    # it lands. Any of these confirms the move was chosen.
                    passed = (
                        any('used focus punch' in t for t in all_texts_norm)
                        or any('tightening' in t for t in all_texts_norm)
                        or any('lost its focus' in t for t in all_texts_norm)
                        or opp_fainted_first
                    )
                    expected = 'used focus punch / tightening its focus / lost its focus'
                else:
                    expected = _norm(f'used {turn.p2_move_name}')
                    passed = (
                        any(expected in t for t in all_texts_norm)
                        or opp_fainted_first
                    )
                self.assertions.append({
                    'turn':     turn.turn_number,
                    'check':    'opponent move',
                    'passed':   passed,
                    'expected': expected,
                    'actual':   turn.battle_texts,
                })

        # Faint events
        for evt in self.faint_events:
            t = evt['turn']
            # Gather texts from the faint turn and adjacent turns
            nearby = []
            for turn in self.turns:
                if abs(turn.turn_number - t) <= 1:
                    nearby.extend(turn.battle_texts)
            nearby_norm = [_norm(x) for x in nearby]

            faint_passed = any('fainted' in x for x in nearby_norm)
            self.assertions.append({
                'turn': t,
                'check': 'faint text',
                'passed': faint_passed,
                'expected': 'fainted',
                'actual': nearby,
            })

            switch_action = evt.get('switch_action', '')
            if switch_action and switch_action.startswith('switch '):
                # We can't easily resolve the Pokémon name here without the PS state,
                # so just verify that a switch happened (marked by presence of "go" in texts)
                switch_passed = any('go' in x for x in nearby_norm)
                self.assertions.append({
                    'turn': t,
                    'check': 'forced switch',
                    'passed': switch_passed,
                    'expected': 'go (switch text)',
                    'actual': nearby,
                })

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        duration = time.monotonic() - self._start_time
        turns_data = []
        for t in self.turns:
            d = {
                'turn':              t.turn_number,
                'p1_action':         t.p1_action,
                'p2_action':         t.p2_action,
                'p1_move_name':      t.p1_move_name,
                'p2_move_name':      t.p2_move_name,
                'skipped_reconcile': t.skipped_reconcile,
                'battle_texts':      t.battle_texts,
            }
            if hasattr(t, 'mcts_stats'):
                d['mcts_stats'] = t.mcts_stats  # type: ignore[attr-defined]
            turns_data.append(d)
        return {
            'savestate':      self.savestate,
            'outcome':        self.outcome,
            'error':          self.error,
            'duration_s':     round(duration, 1),
            'turns':          turns_data,
            'faint_events':   self.faint_events,
            'mismatch_count':   self.mismatch_count,
            'assertions':       self.assertions,
            'faint_mismatches': self.faint_mismatches,
            'output_lines':     self.output_lines,
        }

    def save_log(self, base_path: str) -> None:
        """Write {base_path}.json and {base_path}.txt summary files."""
        import os
        os.makedirs(os.path.dirname(base_path) or '.', exist_ok=True)

        data = self.to_dict()

        # JSON
        with open(base_path + '.json', 'w') as f:
            json.dump(data, f, indent=2)

        # Human-readable .txt
        passed  = sum(1 for a in self.assertions if a['passed'])
        failed  = sum(1 for a in self.assertions if not a['passed'])
        duration = data['duration_s']
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        lines = [
            f"=== {self.savestate} — {ts} ===",
            f"Outcome: {self.outcome or 'unknown'}  |  Turns: {len(self.turns)}"
            f"  |  Duration: {duration}s",
            "",
        ]

        if self.error:
            lines.append(f"ERROR: {self.error}")
            lines.append("")

        for turn in self.turns:
            p1_disp = turn.p1_move_name or turn.p1_action
            p2_disp = turn.p2_move_name or turn.p2_action
            lines.append(f"Turn {turn.turn_number:<3}  p1={p1_disp:<14}  p2={p2_disp}")
            if turn.battle_texts:
                lines.append(f"  Texts: {turn.battle_texts}")
            turn_asserts = [a for a in self.assertions if a['turn'] == turn.turn_number]
            if turn_asserts:
                checks = '  |  '.join(
                    f'[{"PASS" if a["passed"] else "FAIL"}] {a["check"]}'
                    for a in turn_asserts
                )
                lines.append(f"  {checks}")
            lines.append("")

        for evt in self.faint_events:
            lines.append(f"Faint @ turn {evt['turn']} → {evt['switch_action']}")
            faint_asserts = [a for a in self.assertions
                             if a['turn'] == evt['turn']
                             and a['check'] in ('faint text', 'forced switch')]
            if faint_asserts:
                checks = '  |  '.join(
                    f'[{"PASS" if a["passed"] else "FAIL"}] {a["check"]}'
                    for a in faint_asserts
                )
                lines.append(f"  {checks}")
            lines.append("")

        lines.append(f"Assertions: {passed} passed, {failed} failed")

        if self.output_lines:
            lines.append("")
            lines.append("── Output ──────────────────────────────────────────")
            lines.extend(self.output_lines)

        if self.faint_mismatches:
            lines.append("")
            lines.append("── Faint Mismatches ────────────────────────────────")
            reconcile_turns = sorted({m['turn'] for m in self.faint_mismatches})
            for t in reconcile_turns:
                events = [m for m in self.faint_mismatches if m['turn'] == t]
                lines.append(f"Turn {t}  ({len(events)} attempts)  "
                             f"p1={events[0]['p1_action']}  p2={events[0]['p2_action']}")
                for m in events:
                    for d in m['diffs']:
                        direction = ('PS alive GBA dead' if not d['ps_fainted'] and d['gba_fainted']
                                     else 'PS dead GBA alive')
                        lines.append(f"  attempt {m['attempt']} [{d['side']} ps{d['ps_idx']} "
                                     f"gba{d['gba_idx']}] "
                                     f"ps_hp={d['ps_hp']} gba_hp={d['gba_hp']}  ← {direction}")
            lines.append(f"Total: {len(self.faint_mismatches)} mismatch events "
                        f"across {len(reconcile_turns)} reconcile(s)")

        with open(base_path + '.txt', 'w') as f:
            f.write('\n'.join(lines) + '\n')

        # ── State history files ───────────────────────────────────────────────
        extra_files = [base_path + '.json', base_path + '.txt']

        if self.state_snapshots:
            states_path = base_path + '_states.json'
            with open(states_path, 'w') as f:
                json.dump(self.state_snapshots, f, indent=2)
            extra_files.append(states_path)

            diff_path = base_path + '_state_diff.json'
            initial   = self.state_snapshots[0]['state']
            changes   = []
            prev      = initial
            for snap in self.state_snapshots[1:]:
                changes.append({
                    'reason': snap['reason'],
                    'turn':   snap['turn'],
                    'diff':   _state_diff(prev, snap['state']),
                })
                prev = snap['state']
            with open(diff_path, 'w') as f:
                json.dump({'initial': initial, 'changes': changes}, f, indent=2)
            extra_files.append(diff_path)

        print(f'[battle_record] Logs saved: {" ".join(extra_files)}')
