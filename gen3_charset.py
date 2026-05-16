"""
gen3_charset.py

Gen 3 (Emerald/FireRed/LeafGreen) single-byte character encoding decoder.

The encoding is a custom 1-byte-per-character table used by the GBA ROM.
0xFF terminates the string; 0xFE is a newline/page break.
All other unmapped bytes are decoded as '?'.

gDisplayedStringBattle is the buffer used for current battle message text.
"""

from config import GAME_MODE
_ADDR_OFFSET = 0xA54 if GAME_MODE == 'rnb' else 0

# Full English Gen 3 character table (pokeemerald charmap)
GEN3_CHARSET: dict[int, str] = {
    0x00: ' ',
    # Numbers: 0xA1–0xAA = '0'–'9'
    0xA0: ' ',
    0xA1: '0', 0xA2: '1', 0xA3: '2', 0xA4: '3', 0xA5: '4',
    0xA6: '5', 0xA7: '6', 0xA8: '7', 0xA9: '8', 0xAA: '9',
    # Punctuation
    0xAB: '!', 0xAC: '?', 0xAD: '.', 0xAE: '-', 0xAF: '·',
    0xB0: '…', 0xB1: '«', 0xB2: '»', 0xB3: "'",
    0xB4: '♂', 0xB5: '♀', 0xB6: '$', 0xB7: ',', 0xB8: '×', 0xB9: '/',
    # Uppercase A–Z: 0xBB–0xD4
    0xBB: 'A', 0xBC: 'B', 0xBD: 'C', 0xBE: 'D', 0xBF: 'E',
    0xC0: 'F', 0xC1: 'G', 0xC2: 'H', 0xC3: 'I', 0xC4: 'J',
    0xC5: 'K', 0xC6: 'L', 0xC7: 'M', 0xC8: 'N', 0xC9: 'O',
    0xCA: 'P', 0xCB: 'Q', 0xCC: 'R', 0xCD: 'S', 0xCE: 'T',
    0xCF: 'U', 0xD0: 'V', 0xD1: 'W', 0xD2: 'X', 0xD3: 'Y', 0xD4: 'Z',
    # Lowercase a–z: 0xD5–0xEE
    0xD5: 'a', 0xD6: 'b', 0xD7: 'c', 0xD8: 'd', 0xD9: 'e',
    0xDA: 'f', 0xDB: 'g', 0xDC: 'h', 0xDD: 'i', 0xDE: 'j',
    0xDF: 'k', 0xE0: 'l', 0xE1: 'm', 0xE2: 'n', 0xE3: 'o',
    0xE4: 'p', 0xE5: 'q', 0xE6: 'r', 0xE7: 's', 0xE8: 't',
    0xE9: 'u', 0xEA: 'v', 0xEB: 'w', 0xEC: 'x', 0xED: 'y', 0xEE: 'z',
    # Special/diacritic
    0xEF: 'é',
    0xF0: '\u2018', 0xF1: '\u201c', 0xF2: '\u201d',
    # Control bytes (handled separately, included here for completeness)
    0xFE: '\n',  # newline / page-break
    # 0xFF = string terminator (not in table; handled by decode_gen3_string)
}

# Address of gDisplayedStringBattle in GBA EWRAM (Pokémon Emerald)
_BATTLE_TEXT_ADDR = 0x02022E2C - _ADDR_OFFSET  # vanilla: 0x02022E2C
_BATTLE_TEXT_LEN  = 300


def decode_gen3_string(raw_bytes: bytes) -> str:
    """Decode a null-terminated Gen 3 encoded byte sequence to a Unicode string.

    Stops at 0xFF (terminator). Strips 0xFE newlines from the result.
    Unmapped bytes are replaced with '?'.
    """
    chars = []
    for b in raw_bytes:
        if b == 0xFF:
            break
        if b == 0xFE:
            chars.append(' ')
            continue
        chars.append(GEN3_CHARSET.get(b, '?'))
    return ''.join(chars).strip()


def read_battle_text(core) -> str:
    """Read and decode the current GBA battle message from gDisplayedStringBattle.

    Args:
        core: mgba core object with memory access.

    Returns:
        Decoded string, stripped of leading/trailing whitespace.
        Empty string if the buffer is empty or only whitespace.
    """
    raw = bytes(core.memory.u8[_BATTLE_TEXT_ADDR:_BATTLE_TEXT_ADDR + _BATTLE_TEXT_LEN])
    return decode_gen3_string(raw)
