"""
battle_text_validator.py

Parses accumulated GBA battle text messages and compares extracted game state
against memory-read values at each MCTS decision point.
"""

import re
from dataclasses import dataclass, field

# ─── Item names as they appear in decoded GBA text (uppercase) ────────────────
_ITEM_TEXT_NAMES = {
    'POTION', 'SUPER POTION', 'HYPER POTION', 'MAX POTION', 'FULL RESTORE', 'FULL HEAL',
}
_ITEM_TEXT_RE = re.compile(
    r'\b(' + '|'.join(re.escape(n) for n in sorted(_ITEM_TEXT_NAMES, key=len, reverse=True)) + r')\b'
)

# ─── Move-name normaliser (mirrors _find_opp_ps_action in battle_mode.py) ─────
def _norm(name: str) -> str:
    return name.lower().replace(' ', '').replace('-', '').replace("'", '')


@dataclass
class ParsedTextState:
    player_active: str | None = None      # species name from "What will X do?" / "Go! X!"
    opp_active:    str | None = None      # species name from "sent out X!" / "Foe X used"
    player_moves_used: list[str] = field(default_factory=list)
    opp_moves_used:    list[str] = field(default_factory=list)
    player_fainted: bool = False
    opp_fainted:    bool = False
    item_used:      str | None = None     # uppercase item name from text


@dataclass
class MemoryState:
    player_active_species: str | None = None
    opp_active_species:    str | None = None
    opp_last_move_name:    str | None = None   # from moves_db lookup
    last_p1_move_name:     str | None = None   # from last_p1_action resolution
    player_fainted: bool = False
    opp_fainted:    bool = False
    item_id: int = 0


# ─── Parsing ──────────────────────────────────────────────────────────────────

# "FOE POKEMON used MOVE!" — note: decoded text uses title case "Foe", not all-caps
_OPP_USED_RE    = re.compile(r'^Foe (.+?) used (.+?)!', re.IGNORECASE)
_PLAYER_USED_RE = re.compile(r'^(?!Foe\b)(.+?) used (.+?)!', re.IGNORECASE)
_OPP_FAINT_RE   = re.compile(r'^Foe (.+?) fainted', re.IGNORECASE)
_PLAYER_FAINT_RE= re.compile(r'^(?!Foe\b)(.+?) fainted', re.IGNORECASE)
_WHAT_WILL_RE   = re.compile(r'^What will (.+?) do\?', re.IGNORECASE)
_GO_RE          = re.compile(r'^Go(?:[ !]| for it.? )(.+?)!', re.IGNORECASE)
_SENT_OUT_RE    = re.compile(r'sent out (.+?)!', re.IGNORECASE)


def parse_battle_texts(texts: list[str]) -> ParsedTextState:
    """Extract game-state facts from a list of decoded GBA battle text messages."""
    state = ParsedTextState()
    for text in texts:
        text = text.strip()
        if not text:
            continue

        # Opponent move
        m = _OPP_USED_RE.match(text)
        if m:
            pkmn_name = m.group(1).strip()
            move_name = m.group(2).strip()
            state.opp_moves_used.append(move_name)
            if state.opp_active is None:
                state.opp_active = pkmn_name
            continue

        # Player move (only if doesn't start with "Foe")
        m = _PLAYER_USED_RE.match(text)
        if m:
            move_name = m.group(2).strip()
            state.player_moves_used.append(move_name)
            if state.player_active is None:
                state.player_active = m.group(1).strip()
            continue

        # Opponent faint
        m = _OPP_FAINT_RE.match(text)
        if m:
            state.opp_fainted = True
            continue

        # Player faint
        m = _PLAYER_FAINT_RE.match(text)
        if m:
            state.player_fainted = True
            continue

        # "What will X do?" — most reliable source of player active name
        m = _WHAT_WILL_RE.match(text)
        if m:
            state.player_active = m.group(1).strip()
            continue

        # "Go! X!" or "Go for it× X!" — player switch-in
        m = _GO_RE.match(text)
        if m:
            state.player_active = m.group(1).strip()
            continue

        # "TRAINER sent out X!" — opponent switch-in
        m = _SENT_OUT_RE.search(text)
        if m:
            state.opp_active = m.group(1).strip()
            continue

        # Trainer item usage — look for item name keywords anywhere in the text
        item_match = _ITEM_TEXT_RE.search(text)
        if item_match and state.item_used is None:
            state.item_used = item_match.group(1)

    return state


# ─── Validation ───────────────────────────────────────────────────────────────

def validate(parsed: ParsedTextState, mem: MemoryState) -> list[str]:
    """Compare text-extracted state against memory-read state.

    Returns a list of human-readable mismatch strings.  Empty list = clean.
    Only checks fields where both sides have data (avoids false positives when
    one side simply has no information for a given field).
    """
    mismatches: list[str] = []

    # Active Pokémon names
    if parsed.player_active and mem.player_active_species:
        if _norm(parsed.player_active) != _norm(mem.player_active_species):
            mismatches.append(
                f'player_active: text="{parsed.player_active}" '
                f'mem="{mem.player_active_species}"'
            )

    if parsed.opp_active and mem.opp_active_species:
        if _norm(parsed.opp_active) != _norm(mem.opp_active_species):
            mismatches.append(
                f'opp_active: text="{parsed.opp_active}" '
                f'mem="{mem.opp_active_species}"'
            )

    # Opponent move
    if parsed.opp_moves_used and mem.opp_last_move_name:
        normed_mem  = _norm(mem.opp_last_move_name)
        normed_text = [_norm(m) for m in parsed.opp_moves_used]
        if normed_mem not in normed_text:
            mismatches.append(
                f'opp_move: text={parsed.opp_moves_used} '
                f'mem="{mem.opp_last_move_name}"'
            )

    # Opponent move memory claims a move but text shows none (phantom turn)
    if not parsed.opp_moves_used and mem.opp_last_move_name:
        mismatches.append(
            f'opp_move: text has no opponent move but mem="{mem.opp_last_move_name}"'
        )

    # Player move
    if parsed.player_moves_used and mem.last_p1_move_name:
        normed_mem  = _norm(mem.last_p1_move_name)
        normed_text = [_norm(m) for m in parsed.player_moves_used]
        if normed_mem not in normed_text:
            mismatches.append(
                f'player_move: text={parsed.player_moves_used} '
                f'mem="{mem.last_p1_move_name}"'
            )

    # Faint state
    if parsed.opp_fainted and not mem.opp_fainted:
        mismatches.append('opp_fainted: text=True mem=False')
    elif not parsed.opp_fainted and mem.opp_fainted:
        mismatches.append('opp_fainted: text=False mem=True')

    if parsed.player_fainted and not mem.player_fainted:
        mismatches.append('player_fainted: text=True mem=False')
    elif not parsed.player_fainted and mem.player_fainted:
        mismatches.append('player_fainted: text=False mem=True')

    # Item used: text saw item but memory didn't register it
    if parsed.item_used and mem.item_id == 0:
        mismatches.append(
            f'item_used: text="{parsed.item_used}" mem=no item registered'
        )

    return mismatches
