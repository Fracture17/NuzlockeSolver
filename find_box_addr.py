"""
find_box_addr.py

Cross-references two EWRAM dumps to uniquely identify the gPokemonStorage address:
  - ram_ewram_EmptyBox.bin : box was fully empty (all 420 slots species == 0)
  - ram_ewram.bin          : box had Lotad in slot 0, rest empty

The correct address satisfies BOTH constraints simultaneously.

Usage:
    python3 find_box_addr.py

Once confirmed, set PLAYER_BOX_ADDR to the printed address in emerald_reader.py.
"""

import struct
import os

from emerald_reader import (
    SUBORDER,
    _get_substructure,
    EWRAM_BASE,
)

# ─── Constants ────────────────────────────────────────────────────────────────
BOX_OFFSET        = 4    # 1 byte currentBox + 3 bytes ARM alignment padding
BOX_POKEMON_SIZE  = 80
TOTAL_SLOTS       = 14 * 30   # 420
SEARCH_ALIGN      = 4
LOTAD_INTERNAL_ID = 295  # Lotad's GBA internal species ID (national dex 270)

EMPTY_DUMP = os.path.join(os.path.dirname(__file__), "ram_ewram_EmptyBox.bin")
LOTAD_DUMP = os.path.join(os.path.dirname(__file__), "ram_ewram.bin")


# ─── Decryption helper ────────────────────────────────────────────────────────

def _species_from_slot(data: bytes, slot_offset: int) -> int:
    """Decrypt one 80-byte BoxPokemon and return its species ID. Returns -1 on error."""
    try:
        raw  = data[slot_offset : slot_offset + BOX_POKEMON_SIZE]
        pid  = struct.unpack_from('<I', raw, 0)[0]
        otid = struct.unpack_from('<I', raw, 4)[0]
        key  = pid ^ otid
        enc  = bytearray(raw[32:80])
        for i in range(0, 48, 4):
            word = struct.unpack_from('<I', enc, i)[0]
            struct.pack_into('<I', enc, i, word ^ key)
        order = SUBORDER[pid % 24]
        g     = _get_substructure(bytes(enc), order, 0)
        return struct.unpack_from('<H', g, 0)[0]
    except Exception:
        return -1


# ─── Search functions ─────────────────────────────────────────────────────────

def _scan(data: bytes, slot0_expected: int) -> set[int]:
    """
    Return the set of byte offsets (into data) where:
      - data[offset] is 0–13  (currentBox sanity check)
      - slot 0 decrypts to slot0_expected
      - slots 1–419 all decrypt to species == 0

    slot0_expected == 0  → searching for all-empty box
    slot0_expected == N  → searching for specific species in first slot
    """
    min_size = BOX_OFFSET + TOTAL_SLOTS * BOX_POKEMON_SIZE
    results  = set()

    for offset in range(0, len(data) - min_size, SEARCH_ALIGN):
        if data[offset] > 13:
            continue

        box_start = offset + BOX_OFFSET

        # Check slot 0 against the expected species
        s0 = _species_from_slot(data, box_start)
        #if s0 != slot0_expected:
        #    continue
        if slot0_expected == 0:
            if s0 != slot0_expected:
                continue
        elif s0 == 0:
            continue

        # Check remaining slots are all empty
        ok = True
        for slot in range(1, TOTAL_SLOTS):
            if _species_from_slot(data, box_start + slot * BOX_POKEMON_SIZE) != 0:
                ok = False
                break

        if ok:
            results.add(offset)

    return results


def _load(path: str) -> bytes:
    print(f"Reading {path} ...")
    with open(path, 'rb') as f:
        data = f.read()
    print(f"  Loaded {len(data):,} bytes ({len(data) // 1024} KB)")
    return data


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    missing = [p for p in (EMPTY_DUMP, LOTAD_DUMP) if not os.path.exists(p)]
    if missing:
        for p in missing:
            print(f"ERROR: {p} not found.")
        print("Press F3 in-game to dump EWRAM (rename as needed), then re-run.")
        return

    empty_data = _load(EMPTY_DUMP)
    lotad_data = _load(LOTAD_DUMP)
    print()

    print(f"Scanning empty-box dump (all {TOTAL_SLOTS} slots == 0)...")
    empty_offsets = _scan(empty_data, slot0_expected=0)
    print(f"  Empty-box candidates:   {len(empty_offsets)}")

    print(f"Scanning Lotad dump (slot 0 == Lotad [{LOTAD_INTERNAL_ID}], rest == 0)...")
    lotad_offsets = _scan(lotad_data, slot0_expected=LOTAD_INTERNAL_ID)
    print(f"  Lotad-first candidates: {len(lotad_offsets)}")

    matches = empty_offsets & lotad_offsets
    print(f"  Intersection:           {len(matches)}")
    print()

    if not matches:
        print("No confirmed address found. Possible causes:")
        print("  - Lotad internal ID may differ (check _GEN3_INTERNAL_TO_NATIONAL in emerald_reader.py)")
        print("  - Try BOX_OFFSET = 1 instead of 4 (edit constant at top of this script)")
        print(f"  Empty-only addresses: {sorted(EWRAM_BASE + o for o in empty_offsets)}")
        print(f"  Lotad-only addresses: {sorted(EWRAM_BASE + o for o in lotad_offsets)}")
    elif len(matches) == 1:
        addr = EWRAM_BASE + next(iter(matches))
        print(f"CONFIRMED: 0x{addr:08X}")
        print(f"\n  → Set PLAYER_BOX_ADDR = 0x{addr:08X} in emerald_reader.py")
    else:
        print(f"{len(matches)} addresses matched both constraints (expected 1):")
        for offset in sorted(matches):
            print(f"  CONFIRMED: 0x{EWRAM_BASE + offset:08X}")


if __name__ == "__main__":
    main()
