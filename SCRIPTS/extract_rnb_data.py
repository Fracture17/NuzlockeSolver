# Parses runandbun.lua and emits five RnB-specific JSON lookup files:
# rnb_species.json, rnb_move_names.json, rnb_items.json, rnb_abilities.json, rnb_curves.json

import re
import json
from pathlib import Path

LUA_PATH = Path("/home/Fracture/Downloads/Pokémon Run & Bun/runandbun.lua")
OUT_DIR = Path("/home/Fracture/PycharmProjects/NuzlockeSolver")

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _find_table_start(lines: list[str], var_name: str) -> int:
    """Return 0-based line index of the line containing '<var_name> =' or '<var_name>='."""
    pattern = re.compile(rf"^\s*{re.escape(var_name)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            return i
    raise ValueError(f"Table '{var_name}' not found in Lua file")


def _extract_table_text(lines: list[str], start_line: int) -> str:
    """Return the raw text of the Lua table (from '{' to the matching '}')."""
    # Collect from start_line until we see a line that is just '}' (end of table).
    chunks = []
    for line in lines[start_line:]:
        chunks.append(line)
        stripped = line.strip()
        # A lone '}' (optionally followed by nothing or a comment) closes the table.
        if stripped == "}" or stripped.startswith("}") and len(stripped) == 1:
            break
    return "".join(chunks)


def _parse_string_list(table_text: str) -> list[str]:
    """Extract all quoted string literals (single or double) from table_text, in order."""
    return re.findall(r"""['"]([^'"]*?)['"]""", table_text)


def _parse_number_list(table_text: str) -> list[int]:
    """Extract all integer literals from inside the braces of table_text."""
    # Strip everything outside the outermost braces first.
    inner = re.search(r"\{(.*)\}", table_text, re.DOTALL)
    if not inner:
        raise ValueError("No braces found in table text")
    return [int(n) for n in re.findall(r"\d+", inner.group(1))]


# ---------------------------------------------------------------------------
# Per-table extractors
# ---------------------------------------------------------------------------

def extract_moves(lines: list[str]) -> dict[str, str]:
    """GBA move ID (0-based) → name. move[1]="" (ID 0), move[2]="Pound" (ID 1)."""
    start = _find_table_start(lines, "move")
    text = _extract_table_text(lines, start)
    names = _parse_string_list(text)
    # Lua index 1 = GBA ID 0, Lua index N+1 = GBA ID N.
    return {str(i): name for i, name in enumerate(names)}


def extract_species(lines: list[str]) -> dict[str, str]:
    """Internal species ID (1-based) → name. mons[1]=species 1. Skip empty strings."""
    start = _find_table_start(lines, "mons")
    text = _extract_table_text(lines, start)
    names = _parse_string_list(text)
    return {str(i + 1): name for i, name in enumerate(names) if name}


def extract_items(lines: list[str]) -> dict[str, str]:
    """GBA item ID (1-based) → name. item[1]="Poke Ball"."""
    start = _find_table_start(lines, "item")
    text = _extract_table_text(lines, start)
    names = _parse_string_list(text)
    return {str(i + 1): name for i, name in enumerate(names)}


def extract_abilities(lines: list[str]) -> dict[str, dict[str, str]]:
    """Internal species ID → {slot_index: ability_name}.
    ability[0] (Lua 1-indexed: 1,2,3) is the dummy species-0 row; species 1 starts at flat index 3.
    For species N: flat indices N*3, N*3+1, N*3+2 (0-based flat list).
    """
    start = _find_table_start(lines, "ability")
    text = _extract_table_text(lines, start)
    flat = _parse_string_list(text)
    # flat[0..2] = species 0 (dummy), flat[3..5] = species 1, etc.
    result: dict[str, dict[str, str]] = {}
    species_count = len(flat) // 3
    for species_id in range(1, species_count):
        base = species_id * 3
        result[str(species_id)] = {
            "0": flat[base],
            "1": flat[base + 1],
            "2": flat[base + 2],
        }
    return result


def extract_curves(lines: list[str]) -> dict[str, int]:
    """Internal species ID (1-based) → exp growth group integer."""
    start = _find_table_start(lines, "curve")
    text = _extract_table_text(lines, start)
    values = _parse_number_list(text)
    # curve[N] in Lua (1-based) = species ID N.
    return {str(i + 1): v for i, v in enumerate(values)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    lines = LUA_PATH.read_text(encoding="utf-8").splitlines(keepends=True)

    moves = extract_moves(lines)
    species = extract_species(lines)
    items = extract_items(lines)
    abilities = extract_abilities(lines)
    curves = extract_curves(lines)

    def write(filename: str, data: object) -> None:
        (OUT_DIR / filename).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    write("rnb_species.json", species)
    write("rnb_move_names.json", moves)
    write("rnb_items.json", items)
    write("rnb_abilities.json", abilities)
    write("rnb_curves.json", curves)

    print(f"Total species: {len(species)}")
    print(f"Total moves:   {len(moves)}")
    print(f"Total items:   {len(items)}")

    print("\nFirst 20 species (IDs 1-20):")
    for sid in range(1, 21):
        name = species.get(str(sid), "<missing>")
        print(f"  {sid:3d}: {name}")

    print("\nSpot-check species IDs 252, 277, 412, 500:")
    for sid in (252, 277, 412, 500):
        name = species.get(str(sid), "<not present>")
        print(f"  {sid}: {name}")

    print("\nFirst 3 curve values (species 1, 2, 3):")
    for sid in range(1, 4):
        print(f"  species {sid}: curve={curves.get(str(sid), '<missing>')}")


if __name__ == "__main__":
    main()
