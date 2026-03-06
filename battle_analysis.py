"""Battle analysis and reporting for recorded MCTS battles.

Reads a JSON file written by play_game(record_path=...) and produces a
human-readable analysis report (.txt file).

Usage:
    python battle_analysis.py <battle.json>
    → writes <battle>_analysis.txt

Library:
    from battle_analysis import write_analysis, format_battle_report
    write_analysis("run_001.json")
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# PS battle log parser
# ---------------------------------------------------------------------------

_STAT_NAMES = {
    "atk": "Attack", "def": "Defense",
    "spa": "Sp. Atk", "spd": "Sp. Def",
    "spe": "Speed", "accuracy": "Accuracy", "evasion": "Evasion",
}

_STATUS_NAMES = {
    "brn": "burned", "par": "paralyzed",
    "psn": "poisoned", "tox": "badly poisoned",
    "slp": "put to sleep", "frz": "frozen",
}

_WEATHER_NAMES = {
    "RainDance": "Rain", "SunnyDay": "Harsh Sunlight",
    "Sandstorm": "Sandstorm", "Hail": "Hail",
    "none": "(none)",
}


def _poke_name(ps_ref: str) -> str:
    """Extract display name from PS reference like 'p1a: Swampert' → 'Swampert'."""
    return ps_ref.split(": ", 1)[1] if ": " in ps_ref else ps_ref


def _player_label(ps_ref: str) -> str:
    """'p1a: ...' → 'p1', 'p2a: ...' → 'p2'."""
    return ps_ref[:2] if len(ps_ref) >= 2 else "??"


def _hp_str(hp_field: str) -> str:
    """'213/323' → '213/323 (66%)', '0 fnt' → 'fainted'."""
    if "fnt" in hp_field:
        return "fainted"
    try:
        clean = hp_field.split()[0]  # strip trailing tags like " fnt"
        a, b = clean.split("/")
        hp, maxhp = float(a), float(b)
        return f"{int(hp)}/{int(maxhp)} ({100 * hp / maxhp:.0f}%)"
    except Exception:
        return hp_field


def _parse_hp_nums(hp_field: str) -> tuple:
    """Parse '213/323' → (213, 323). Returns (0, 1) for fainted or unparseable."""
    if "fnt" in hp_field:
        return (0, 1)
    try:
        clean = hp_field.split()[0]
        a, b = clean.split("/")
        return (int(float(a)), int(float(b)))
    except Exception:
        return (0, 1)


def parse_ps_log_line(line: str) -> Optional[str]:
    """Parse one PS battle protocol line into readable English.

    Returns None for lines that should be silently skipped.
    """
    if not line.startswith("|"):
        return None
    parts = line.split("|")
    # parts[0]="" parts[1]=cmd parts[2..]=args
    if len(parts) < 2:
        return None
    cmd = parts[1]
    args = parts[2:]

    # Silent skips
    if cmd in ("", "upkeep", "t:", "seed", "sentchoice", "inactive",
               "inactiveoff", "choice", "bigerror", "c", "chat",
               "player", "gametype", "gen", "tier", "rated", "rule",
               "teampreview", "start", "poke", "clearpoke"):
        return None

    if cmd == "turn":
        n = args[0] if args else "?"
        return f"\n{'─' * 52}\nTurn {n}\n{'─' * 52}"

    if cmd == "move":
        user = _poke_name(args[0]) if args else "?"
        move = args[1] if len(args) > 1 else "?"
        target = _poke_name(args[2]) if len(args) > 2 and args[2] else ""
        if target and target != user:
            return f"  {user} used {move} → {target}"
        return f"  {user} used {move}"

    if cmd in ("switch", "drag"):
        who = _player_label(args[0]) if args else "??"
        species = _poke_name(args[0]) if args else "?"
        hp = _hp_str(args[2]) if len(args) > 2 else ""
        verb = "sent out" if cmd == "switch" else "was forced in:"
        return f"  {who} {verb} {species}  [{hp}]"

    if cmd == "-damage":
        name = _poke_name(args[0]) if args else "?"
        hp = _hp_str(args[1]) if len(args) > 1 else ""
        src = f"  (from {args[2]})" if len(args) > 2 and args[2] else ""
        return f"  {name} took damage → {hp}{src}"

    if cmd == "-heal":
        name = _poke_name(args[0]) if args else "?"
        hp = _hp_str(args[1]) if len(args) > 1 else ""
        return f"  {name} healed → {hp}"

    if cmd == "-sethp":
        name = _poke_name(args[0]) if args else "?"
        hp = _hp_str(args[1]) if len(args) > 1 else ""
        return f"  {name} HP → {hp}"

    if cmd == "faint":
        name = _poke_name(args[0]) if args else "?"
        return f"  *** {name} fainted! ***"

    if cmd == "-status":
        name = _poke_name(args[0]) if args else "?"
        st = _STATUS_NAMES.get(args[1] if len(args) > 1 else "", args[1] if args[1:] else "?")
        return f"  {name} was {st}"

    if cmd == "-curestatus":
        name = _poke_name(args[0]) if args else "?"
        return f"  {name} was cured of its status"

    if cmd == "-cureteam":
        return f"  [{_poke_name(args[0]) if args else '?'}'s team had status cured]"

    if cmd == "-boost":
        name = _poke_name(args[0]) if args else "?"
        stat = _STAT_NAMES.get(args[1] if len(args) > 1 else "", "stat")
        n = args[2] if len(args) > 2 else "1"
        return f"  {name}'s {stat} rose by {n}!"

    if cmd == "-unboost":
        name = _poke_name(args[0]) if args else "?"
        stat = _STAT_NAMES.get(args[1] if len(args) > 1 else "", "stat")
        n = args[2] if len(args) > 2 else "1"
        return f"  {name}'s {stat} fell by {n}!"

    if cmd == "-setboost":
        name = _poke_name(args[0]) if args else "?"
        stat = _STAT_NAMES.get(args[1] if len(args) > 1 else "", "stat")
        n = args[2] if len(args) > 2 else "?"
        return f"  {name}'s {stat} set to {n}"

    if cmd == "-clearboost":
        name = _poke_name(args[0]) if args else "?"
        return f"  {name}'s stat changes were cleared"

    if cmd == "-supereffective":
        return "    (super effective!)"

    if cmd == "-resisted":
        return "    (not very effective...)"

    if cmd == "-immune":
        name = _poke_name(args[0]) if args else "?"
        return f"    ({name} is immune!)"

    if cmd == "-crit":
        return "    (critical hit!)"

    if cmd == "-miss":
        user = _poke_name(args[0]) if args else "?"
        return f"  {user}'s attack missed!"

    if cmd == "-fail":
        name = _poke_name(args[0]) if args else "?"
        return f"  {name}'s move failed"

    if cmd == "-weather":
        weather = args[0] if args else "none"
        label = _WEATHER_NAMES.get(weather, weather)
        if weather in ("none", ""):
            return "  [Weather cleared]"
        return f"  [Weather: {label}]"

    if cmd == "-fieldstart":
        condition = args[0] if args else "?"
        return f"  [Field: {condition} started]"

    if cmd == "-fieldend":
        condition = args[0] if args else "?"
        return f"  [Field: {condition} ended]"

    if cmd == "-sidestart":
        side = args[0] if args else "?"
        condition = args[1] if len(args) > 1 else "?"
        return f"  [{side}: {condition}]"

    if cmd == "-sideend":
        side = args[0] if args else "?"
        condition = args[1] if len(args) > 1 else "?"
        return f"  [{side}: {condition} ended]"

    if cmd == "-enditem":
        name = _poke_name(args[0]) if args else "?"
        item = args[1] if len(args) > 1 else "?"
        return f"  {name}'s {item} was consumed"

    if cmd == "-item":
        name = _poke_name(args[0]) if args else "?"
        item = args[1] if len(args) > 1 else "?"
        return f"  {name} obtained {item}"

    if cmd == "-ability":
        name = _poke_name(args[0]) if args else "?"
        ability = args[1] if len(args) > 1 else "?"
        return f"  [{name}'s {ability} activated]"

    if cmd == "-activate":
        name = _poke_name(args[0]) if args else "?"
        effect = args[1] if len(args) > 1 else "?"
        return f"  [{name}: {effect}]"

    if cmd == "-transform":
        name = _poke_name(args[0]) if args else "?"
        target = _poke_name(args[1]) if len(args) > 1 else "?"
        return f"  {name} transformed into {target}!"

    if cmd == "-start":
        name = _poke_name(args[0]) if args else "?"
        effect = args[1] if len(args) > 1 else "?"
        return f"  [{name}: {effect} started]"

    if cmd == "-end":
        name = _poke_name(args[0]) if args else "?"
        effect = args[1] if len(args) > 1 else "?"
        return f"  [{name}: {effect} ended]"

    if cmd == "win":
        winner = args[0] if args else "?"
        return f"\n{'═' * 52}\n  {winner} won!\n{'═' * 52}"

    if cmd == "tie":
        return f"\n{'═' * 52}\n  It's a tie!\n{'═' * 52}"

    if cmd == "error":
        return f"  [ERROR: {' | '.join(args)}]"

    # Generic fallback for unknown commands
    if args:
        return f"  [{cmd}: {' | '.join(a for a in args if a)}]"
    return f"  [{cmd}]"


def _init_hp_from_full_log(full_log: list) -> tuple:
    """Pre-populate HP trackers from initial switch-ins in the full battle log.

    Scans from the start until the first |turn| line. Returns (raw_hp, pct_hp):
      raw_hp: ref → (hp, maxhp) for lines where maxhp != 100 (exact HP)
      pct_hp: ref → (hp, 100)   for lines where maxhp == 100 (percentage form)
    """
    raw_hp: dict = {}
    pct_hp: dict = {}
    for line in full_log:
        if not line.startswith("|"):
            continue
        parts = line.split("|")
        cmd = parts[1] if len(parts) > 1 else ""
        if cmd == "turn":
            break
        if cmd in ("switch", "drag") and len(parts) >= 5:
            ref = parts[2]
            hp_field = parts[4]
            new_hp, max_hp = _parse_hp_nums(hp_field)
            if max_hp == 100:
                pct_hp[ref] = (new_hp, 100)
            else:
                raw_hp[ref] = (new_hp, max_hp)
    return raw_hp, pct_hp


def parse_ps_log(log_lines: list, raw_hp: dict = None, pct_hp: dict = None) -> tuple:
    """Parse a list of PS log lines into a readable battle narrative.

    Maintains two stateful HP trackers:
      raw_hp: ref → (hp, maxhp) for lines where maxhp != 100 (exact HP)
      pct_hp: ref → (hp, 100)   for lines where maxhp == 100 (percentage form)

    Args:
        log_lines: PS protocol lines for this turn.
        raw_hp: Pre-populated exact-HP tracker (pass in from previous turn).
        pct_hp: Pre-populated percentage-HP tracker (pass in from previous turn).

    Returns:
        (text, raw_hp, pct_hp) — updated trackers to pass to the next turn.
    """
    if raw_hp is None:
        raw_hp = {}
    if pct_hp is None:
        pct_hp = {}
    out = []

    for line in log_lines:
        if not line.startswith("|"):
            continue
        parts = line.split("|")
        if len(parts) < 2:
            continue
        cmd = parts[1]
        args = parts[2:]

        # Update HP trackers for switch/drag (initialize or refresh entry)
        if cmd in ("switch", "drag") and len(args) >= 3:
            ref = args[0]
            new_hp, max_hp = _parse_hp_nums(args[2])
            if max_hp == 100:
                pct_hp[ref] = (new_hp, 100)
            else:
                raw_hp[ref] = (new_hp, max_hp)

        # Handle -damage with delta, keeping raw and pct trackers separate
        if cmd == "-damage" and args:
            ref = args[0]
            hp_field = args[1] if len(args) > 1 else ""
            new_hp, max_hp = _parse_hp_nums(hp_field)
            name = _poke_name(ref)
            src = f"  (from {args[2]})" if len(args) > 2 and args[2] else ""
            is_pct = max_hp == 100
            tracker = pct_hp if is_pct else raw_hp
            if ref in tracker:
                prev_hp, _ = tracker[ref]
                delta = prev_hp - new_hp
                tracker[ref] = (new_hp, max_hp)
                if is_pct:
                    out.append(f"  {name} took {delta}% → {_hp_str(hp_field)}{src}")
                else:
                    out.append(f"  {name} took {delta} damage → {_hp_str(hp_field)}{src}")
            else:
                tracker[ref] = (new_hp, max_hp)
                out.append(f"  {name} took damage → {_hp_str(hp_field)}{src}")
            continue

        # Handle -heal with delta
        if cmd == "-heal" and args:
            ref = args[0]
            hp_field = args[1] if len(args) > 1 else ""
            new_hp, max_hp = _parse_hp_nums(hp_field)
            name = _poke_name(ref)
            is_pct = max_hp == 100
            tracker = pct_hp if is_pct else raw_hp
            if ref in tracker:
                prev_hp, _ = tracker[ref]
                delta = new_hp - prev_hp
                tracker[ref] = (new_hp, max_hp)
                if is_pct:
                    out.append(f"  {name} healed {delta}% → {_hp_str(hp_field)}")
                else:
                    out.append(f"  {name} healed {delta} HP → {_hp_str(hp_field)}")
            else:
                tracker[ref] = (new_hp, max_hp)
                out.append(f"  {name} healed → {_hp_str(hp_field)}")
            continue

        # Handle -sethp (update tracker, use default display)
        if cmd == "-sethp" and args:
            ref = args[0]
            hp_field = args[1] if len(args) > 1 else ""
            new_hp, max_hp = _parse_hp_nums(hp_field)
            tracker = pct_hp if max_hp == 100 else raw_hp
            tracker[ref] = (new_hp, max_hp)

        parsed = parse_ps_log_line(line)
        if parsed is not None:
            out.append(parsed)

    return "\n".join(out), raw_hp, pct_hp


# ---------------------------------------------------------------------------
# ASCII bar chart
# ---------------------------------------------------------------------------

_BAR_WIDTH = 24


def _bar(fraction: float, width: int = _BAR_WIDTH) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# Decision interpreter
# ---------------------------------------------------------------------------

def interpret_decision(action_stats: list[dict], total_iterations: int) -> list[str]:
    """Return a list of human-readable interpretation strings for one decision.

    Args:
        action_stats: list of dicts with keys: action, display, visits, avg_value, chosen.
        total_iterations: total MCTS iterations for this decision.
    """
    if not action_stats or total_iterations == 0:
        return []

    chosen = next((s for s in action_stats if s.get("chosen")), action_stats[0])
    others = [s for s in action_stats if not s.get("chosen")]

    lines = []
    chosen_pct = chosen["visits"] / total_iterations * 100

    # How dominant was the choice?
    if len(action_stats) == 1:
        lines.append(f"→ Only option: {chosen['display']}")
    elif chosen_pct >= 75:
        lines.append(
            f"→ Strongly preferred {chosen['display']} ({chosen_pct:.1f}% of iterations)"
        )
    elif chosen_pct >= 50:
        lines.append(
            f"→ Preferred {chosen['display']} ({chosen_pct:.1f}% of iterations)"
        )
    else:
        if others:
            second = max(others, key=lambda s: s["visits"])
            second_pct = second["visits"] / total_iterations * 100
            if abs(chosen_pct - second_pct) <= 20:
                lines.append(
                    f"→ Contested: {chosen['display']} ({chosen_pct:.1f}%) vs "
                    f"{second['display']} ({second_pct:.1f}%)"
                )
            else:
                lines.append(
                    f"→ Leaned toward {chosen['display']} ({chosen_pct:.1f}%) "
                    f"over {second['display']} ({second_pct:.1f}%)"
                )

    # Highlight cases where a non-chosen action had higher average value
    meaningful_others = [s for s in others if s["visits"] > max(5, total_iterations * 0.02)]
    better_avg = [s for s in meaningful_others if s["avg_value"] > chosen["avg_value"]]
    if better_avg:
        alt = max(better_avg, key=lambda s: s["avg_value"])
        lines.append(
            f"  ⚠ {alt['display']} showed higher avg value "
            f"({alt['avg_value']:+.3f} vs chosen {chosen['avg_value']:+.3f}) "
            f"with {alt['visits']} visits — may warrant more exploration"
        )

    # Note barely-explored options
    threshold = max(3, total_iterations * 0.03)
    barely = [s for s in action_stats if s["visits"] < threshold and not s.get("chosen")]
    if barely:
        names = ", ".join(s["display"] for s in barely[:4])
        lines.append(f"  (barely explored: {names})")

    return lines


# ---------------------------------------------------------------------------
# Turn formatter
# ---------------------------------------------------------------------------

def format_turn(turn: dict, parsed_log_text: str = None,
                level_map: dict = None) -> str:
    """Render one decision turn as a formatted string.

    Args:
        turn: Turn data dict from the battle JSON.
        parsed_log_text: Pre-parsed log narrative (from parse_ps_log with threaded
            tracker). If None, falls back to parsing log_lines without tracker state.
        level_map: Optional dict mapping pokemon display name → level int, used to
            show "(LvN)" next to each pokemon in the party board.
    """
    lines = []
    dec = turn.get("decision", "?")
    p1_active = turn.get("p1_active", "?")
    p2_active = turn.get("p2_active", "?")

    lines.append(f"\n{'═' * 56}")
    lines.append(f"  Decision {dec:<3}  │  {p1_active}  vs  {p2_active}")
    lines.append(f"{'═' * 56}")

    # Board state
    def _party_str(party: list, active_name: str) -> str:
        parts = []
        for p in party:
            name = p.get("name", "?")
            hp = p.get("hp_pct", 0.0)
            fainted = p.get("fainted", False)
            tag = "FNT" if fainted else f"{hp:.0f}%"
            marker = "*" if name == active_name else " "
            if level_map and name in level_map:
                label = f"{name}(Lv{level_map[name]})"
            else:
                label = name
            parts.append(f"{label}{marker} {tag}")
        return "  ".join(parts)

    p1_party = turn.get("p1_party", [])
    p2_party = turn.get("p2_party", [])
    lines.append(f"\n  p1:  {_party_str(p1_party, p1_active)}")
    lines.append(f"  p2:  {_party_str(p2_party, p2_active)}")

    # MCTS decision
    action_stats = turn.get("action_stats", [])
    total_iters = turn.get("total_iterations", 0)

    if action_stats:
        lines.append(f"\n  MCTS ({total_iters} iterations):")
        max_disp_len = max(len(s.get("display", "")) for s in action_stats)
        max_disp_len = max(max_disp_len, 8)

        for s in action_stats:
            action = s.get("action", "?")
            display = s.get("display", "?")
            visits = s.get("visits", 0)
            avg_val = s.get("avg_value", 0.0)
            chosen = s.get("chosen", False)
            marker = "✓" if chosen else " "
            frac = visits / total_iters if total_iters > 0 else 0.0
            bar = _bar(frac)
            lines.append(
                f"  {marker} {action:<8}  {display:<{max_disp_len}}  "
                f"[{bar}] {visits:>6}/{total_iters}  avg: {avg_val:+.3f}"
            )

        interp = interpret_decision(action_stats, total_iters)
        if interp:
            lines.append("")
            for interp_line in interp:
                lines.append(f"  {interp_line}")

    # What happened (pre-parsed or fall back to parsing without tracker state)
    log_lines = turn.get("log_lines", [])
    if parsed_log_text is None and log_lines:
        parsed_log_text, _, _ = parse_ps_log(log_lines)
    if parsed_log_text and parsed_log_text.strip():
        lines.append("\n  What happened:")
        for log_line in parsed_log_text.splitlines():
            lines.append(f"  {log_line}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Battle-level summary
# ---------------------------------------------------------------------------

def _build_summary(data: dict) -> str:
    """Generate a summary section for the full battle."""
    lines = []
    lines.append(f"\n{'═' * 56}")
    lines.append("  SUMMARY")
    lines.append(f"{'═' * 56}")

    turns = data.get("turns", [])
    total_iters_list = [t.get("total_iterations", 0) for t in turns]

    # Contested decisions
    contested = []
    underexplored = []
    for t in turns:
        stats = t.get("action_stats", [])
        total = t.get("total_iterations", 0)
        if not stats or total == 0:
            continue
        chosen = next((s for s in stats if s.get("chosen")), None)
        if chosen is None:
            continue
        chosen_pct = chosen["visits"] / total * 100
        if chosen_pct < 55 and len(stats) > 1:
            contested.append(t["decision"])
        others = [s for s in stats if not s.get("chosen") and s["visits"] > 5]
        better = [s for s in others if s["avg_value"] > chosen["avg_value"]]
        if better:
            underexplored.append(t["decision"])

    if contested:
        lines.append(f"\n  Most contested decisions:  {', '.join(f'#{d}' for d in contested)}")
    else:
        lines.append("\n  No highly contested decisions (MCTS was generally confident)")

    if underexplored:
        lines.append(
            f"  Decisions with higher avg-value alternative:  "
            f"{', '.join(f'#{d}' for d in underexplored)}"
        )

    # Final board state from last turn
    last_turn = turns[-1] if turns else None
    if last_turn:
        lines.append("\n  Final board state:")
        for label, party_key in [("p1", "p1_party"), ("p2", "p2_party")]:
            party = last_turn.get(party_key, [])
            if party:
                parts = []
                for p in party:
                    name = p.get("name", "?")
                    if p.get("fainted"):
                        parts.append(f"{name} FNT")
                    else:
                        parts.append(f"{name} {p.get('hp_pct', 0):.0f}%")
                lines.append(f"    {label}:  {', '.join(parts)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Team info header
# ---------------------------------------------------------------------------

def _format_team_info(team: list, label: str, matchup_scores: dict = None) -> list:
    """Render level, stats, moves, item, and optional matchup scores for each pokemon."""
    lines = [f"\n  {'─' * 54}", f"  {label} TEAM"]
    for p in team:
        name = p.get("name", "?")
        level = p.get("level", "?")
        item = p.get("item", "") or "—"
        ability = p.get("ability", "") or "—"
        moves = [m for m in p.get("moves", []) if m]
        maxhp = p.get("maxhp", 0)
        atk = p.get("atk", 0)
        def_ = p.get("def", 0)
        spa = p.get("spa", 0)
        spd = p.get("spd", 0)
        spe = p.get("spe", 0)
        lines.append(f"  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄")
        lines.append(f"  {name}  (Lv{level})   Item: {item}   Ability: {ability}")
        lines.append(
            f"    HP:{maxhp:>4}  Atk:{atk:>4}  Def:{def_:>4}"
            f"  SpA:{spa:>4}  SpD:{spd:>4}  Spe:{spe:>4}"
        )
        if moves:
            lines.append(f"    Moves: {' | '.join(moves)}")
        scores = (matchup_scores or {}).get(name, [])
        if scores:
            parts = []
            for s in scores:
                opp = s["opponent"]
                raw = s["raw"]
                norm = s["normalized"]
                star = " ★" if s.get("assigned") else ""
                parts.append(f"{opp}: {raw:+.2f} ({norm:.2f}){star}")
            lines.append(f"    Matchup:  {' │ '.join(parts)}")
    return lines


# ---------------------------------------------------------------------------
# Top-level formatters
# ---------------------------------------------------------------------------

def format_battle_report(data: dict) -> str:
    """Render a complete battle analysis report as a string.

    Args:
        data: dict loaded from a battle JSON file written by play_game(record_path=...).
    """
    lines = []

    winner = data.get("winner", "unknown").upper()
    turns = data.get("turns", [])
    total_decisions = len(turns)

    lines.append("╔" + "═" * 54 + "╗")
    lines.append("║" + "  BATTLE ANALYSIS REPORT".center(54) + "║")
    lines.append(f"║  Winner: {winner:<8} │  Total decisions: {total_decisions:<14}║")
    lines.append("╚" + "═" * 54 + "╝")

    # Team info header (only present in battles recorded with the new JSON format)
    p1_team = data.get("p1_team")
    p2_team = data.get("p2_team")
    matchup_scores = data.get("matchup_scores")
    if p1_team:
        lines.extend(_format_team_info(p1_team, "P1", matchup_scores=matchup_scores))
    if p2_team:
        lines.extend(_format_team_info(p2_team, "P2"))

    # Build level_map for party board display
    level_map: dict = {}
    for team_data in [p1_team or [], p2_team or []]:
        for p in team_data:
            name = p.get("name", "")
            level = p.get("level")
            if name and level is not None:
                level_map[name] = level

    # Initialize HP trackers from the pre-battle switch-ins in full_log
    full_log = data.get("full_log", [])
    raw_hp, pct_hp = _init_hp_from_full_log(full_log)

    # Render each turn, threading tracker state through all decisions
    for turn in turns:
        log_lines = turn.get("log_lines", [])
        parsed_log_text, raw_hp, pct_hp = parse_ps_log(log_lines, raw_hp, pct_hp)
        lines.append(format_turn(turn, parsed_log_text=parsed_log_text,
                                 level_map=level_map or None))

    lines.append(_build_summary(data))
    lines.append("")

    return "\n".join(lines)


def write_analysis(json_path: str) -> str:
    """Load a battle JSON file, generate the analysis, and write a .txt report.

    The output file is named `{stem}_analysis.txt` in the same directory.

    Args:
        json_path: Path to the battle JSON file.

    Returns:
        Path of the written analysis file.
    """
    json_path = Path(json_path)
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    report = format_battle_report(data)

    out_path = json_path.with_name(json_path.stem + "_analysis.txt")
    with out_path.open("w", encoding="utf-8") as f:
        f.write(report)

    return str(out_path)


def print_analysis(json_path: str) -> None:
    """Print a battle analysis report to stdout.

    Args:
        json_path: Path to the battle JSON file.
    """
    json_path = Path(json_path)
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    print(format_battle_report(data))


# ---------------------------------------------------------------------------
# Command-line entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """if len(sys.argv) < 2:
        print("Usage: python battle_analysis.py <battle.json>")
        sys.exit(1)"""

    #path = sys.argv[1]
    path = "test_50_3.json"
    out = write_analysis(path)
    print(f"Analysis written to: {out}")
