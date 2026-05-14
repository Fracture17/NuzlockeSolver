"""
extract_learnsets.py

Parse pokeemerald TM/HM and tutor learnset data into gen3_learnsets.json.

Output: { "zangoose": ["shadowball", "brickbreak", ...], ... }
Keys:   lowercase PS species IDs (display name, lowercased, spaces stripped).
Values: sorted list of PS move IDs (lowercase, underscores/spaces stripped).
"""

import json
import os
import re

_HERE    = os.path.dirname(os.path.abspath(__file__))
_PKMN    = '/home/Fracture/Downloads/pokeemerald-master'
_SPECIES = os.path.join(os.path.dirname(_HERE), 'gen3_species.json')
_OUTPUT  = os.path.join(os.path.dirname(_HERE), 'gen3_learnsets.json')

_TMHM_LEARNSETS = os.path.join(_PKMN, 'src/data/pokemon/tmhm_learnsets.h')
_TUTOR_FILE     = os.path.join(_PKMN, 'src/data/pokemon/tutor_learnsets.h')
_TMS_HMS_H      = os.path.join(_PKMN, 'include/constants/tms_hms.h')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_ps_id(emerald_name: str) -> str:
    """Convert EMERALD_MOVE_NAME → ps move id (lowercase, no underscores)."""
    return emerald_name.replace('_', '').lower()


def _species_display_to_key(display_name: str) -> str:
    """Convert 'Zangoose' → 'zangoose'."""
    return display_name.replace(' ', '').replace('-', '').lower()


# ---------------------------------------------------------------------------
# Step 1: Build ordered TM/HM move list from FOREACH_TM / FOREACH_HM macros
# ---------------------------------------------------------------------------

def _parse_tmhm_order(path: str) -> list[str]:
    """Return ordered list of PS move IDs for TM01–TM50, HM01–HM08."""
    text = open(path).read()
    # Extract all F(MOVE_NAME) arguments from FOREACH_TM and FOREACH_HM
    moves = re.findall(r'F\((\w+)\)', text)
    return [_to_ps_id(m) for m in moves]


# ---------------------------------------------------------------------------
# Step 2: Parse tutor move list (gTutorMoves array)
# ---------------------------------------------------------------------------

def _parse_tutor_moves(path: str) -> list[str]:
    """Return ordered list of PS move IDs for tutor moves (index = bit position)."""
    text = open(path).read()
    # Lines like: [TUTOR_MOVE_BODY_SLAM] = MOVE_BODY_SLAM,
    entries = re.findall(r'\[TUTOR_MOVE_(\w+)\]\s*=\s*MOVE_(\w+)', text)
    # entries is ordered by appearance (which matches index order in the array)
    ordered = [None] * len(entries)
    # Build index from TUTOR_MOVE_ names in order of appearance
    tutor_order = [name for name, _ in entries]
    for i, (tutor_name, move_name) in enumerate(entries):
        ordered[i] = _to_ps_id(move_name)
    return ordered


# ---------------------------------------------------------------------------
# Step 3: Parse TMHM learnsets (.MOVE_NAME = TRUE struct fields per species)
# ---------------------------------------------------------------------------

def _parse_tmhm_learnsets(path: str, tmhm_moves: list[str]) -> dict[str, set[str]]:
    """Return {species_name: set of learnable PS move IDs} from tmhm_learnsets.h."""
    tmhm_set = set(tmhm_moves)
    text = open(path).read()

    # Split into per-species blocks by finding [SPECIES_XYZ] = { ... } patterns.
    # Each block starts with [SPECIES_NAME] = { .learnset = {
    # and ends at the closing }},
    species_blocks = re.findall(
        r'\[SPECIES_(\w+)\]\s*=\s*\{[^}]*\.learnset\s*=\s*\{([^}]*)\}',
        text,
        re.DOTALL
    )

    result: dict[str, set[str]] = {}
    for species_raw, block in species_blocks:
        if species_raw == 'NONE':
            continue
        # Extract all .MOVE_NAME = TRUE entries
        move_names = re.findall(r'\.(\w+)\s*=\s*TRUE', block)
        learnable = {_to_ps_id(m) for m in move_names if _to_ps_id(m) in tmhm_set}
        result[species_raw] = learnable

    return result


# ---------------------------------------------------------------------------
# Step 4: Parse tutor learnsets (bitfield OR chains per species)
# ---------------------------------------------------------------------------

def _parse_tutor_learnsets(path: str, tutor_moves: list[str]) -> dict[str, set[str]]:
    """Return {species_name: set of learnable PS move IDs} from tutor_learnsets.h."""
    text = open(path).read()

    # Build lookup: MOVE_BODY_SLAM → 'bodyslam'
    raw_tutor_names = re.findall(r'\[TUTOR_MOVE_(\w+)\]\s*=\s*MOVE_(\w+)', text)
    tutor_lookup = {f'MOVE_{move_name}': _to_ps_id(move_name)
                    for _, move_name in raw_tutor_names}

    # Split into per-species blocks using [SPECIES_X] as delimiter.
    # Each entry spans multiple lines and contains nested parens (TUTOR(...)),
    # so we can't use a simple [^)] regex. Instead, split on the array entries.
    species_entries = re.split(r'\n\s*(?=\[SPECIES_)', text)

    result: dict[str, set[str]] = {}
    for entry in species_entries:
        m = re.match(r'\[SPECIES_(\w+)\]', entry.strip())
        if not m:
            continue
        species_raw = m.group(1)
        if species_raw == 'NONE':
            continue
        # Extract all TUTOR(MOVE_NAME) references within this entry
        refs = re.findall(r'TUTOR\((\w+)\)', entry)
        learnable = set()
        for ref in refs:
            ps_id = tutor_lookup.get(ref)
            if ps_id:
                learnable.add(ps_id)
        result[species_raw] = learnable

    return result


# ---------------------------------------------------------------------------
# Step 5: Build species name map: SPECIES_KEY → PS display key
#         e.g. 'BULBASAUR' → 'bulbasaur', 'MR_MIME' → 'mrmime'
# ---------------------------------------------------------------------------

def _build_species_map(species_json_path: str) -> dict[str, str]:
    """Return {EMERALD_SPECIES_KEY: ps_species_key}."""
    with open(species_json_path) as f:
        db = json.load(f)  # {str(nat_dex_id): display_name}

    # We need EMERALD internal name → PS key.
    # gen3_species.json is already ordered by national dex which matches
    # pokeemerald's SPECIES_ ordering (SPECIES_BULBASAUR=1, etc.).
    result = {}
    for nat_id, display in db.items():
        # Derive the emerald key: display name uppercased, spaces → _
        emerald_key = display.upper().replace(' ', '_').replace('-', '_').replace('.', '').replace("'", '')
        ps_key = _species_display_to_key(display)
        result[emerald_key] = ps_key

    # Handle known special cases that differ between display and emerald names
    _OVERRIDES = {
        'NIDORAN_F': _species_display_to_key('Nidoran-F'),
        'NIDORAN_M': _species_display_to_key('Nidoran-M'),
        'FARFETCHD':  _species_display_to_key("Farfetch'd"),
        'MR_MIME':    _species_display_to_key('Mr. Mime'),
        'HO_OH':      _species_display_to_key('Ho-Oh'),
        'MIME_JR':    _species_display_to_key('Mime Jr.'),
        'PORYGON_Z':  _species_display_to_key('Porygon-Z'),
    }
    result.update(_OVERRIDES)
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print('Parsing TM/HM order...')
    tmhm_moves = _parse_tmhm_order(_TMS_HMS_H)
    print(f'  {len(tmhm_moves)} TM/HM moves: {tmhm_moves[:5]}...')

    print('Parsing tutor moves...')
    tutor_moves = _parse_tutor_moves(_TUTOR_FILE)
    print(f'  {len(tutor_moves)} tutor moves: {tutor_moves[:5]}...')

    print('Parsing TMHM learnsets...')
    tmhm_data = _parse_tmhm_learnsets(_TMHM_LEARNSETS, tmhm_moves)
    print(f'  {len(tmhm_data)} species with TMHM data')

    print('Parsing tutor learnsets...')
    tutor_data = _parse_tutor_learnsets(_TUTOR_FILE, tutor_moves)
    print(f'  {len(tutor_data)} species with tutor data')

    print('Building species name map...')
    species_map = _build_species_map(_SPECIES)

    print('Merging...')
    all_species_keys = set(tmhm_data) | set(tutor_data)
    output: dict[str, list[str]] = {}
    unknown = []

    for emerald_key in sorted(all_species_keys):
        ps_key = species_map.get(emerald_key)
        if ps_key is None:
            unknown.append(emerald_key)
            continue
        learnable = (tmhm_data.get(emerald_key, set()) |
                     tutor_data.get(emerald_key, set()))
        output[ps_key] = sorted(learnable)

    if unknown:
        print(f'  WARNING: {len(unknown)} unmapped species keys: {unknown[:10]}')

    with open(_OUTPUT, 'w') as f:
        json.dump(output, f, indent=2)

    print(f'Wrote {len(output)} species to {_OUTPUT}')

    # Spot-check
    for name in ('zangoose', 'linoone'):
        if name in output:
            print(f'  {name}: {output[name]}')


if __name__ == '__main__':
    main()
