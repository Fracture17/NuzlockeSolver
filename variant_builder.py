"""
variant_builder.py

Builds all (item, moveset) variant combinations for a player's pokemon.

A variant represents a single simulation identity — a specific pokemon with a
specific item assignment and a specific available move pool.  Each variant is
treated as a distinct entity for team-building purposes.

Variant key format: "{species}|{item_tag}|{move_tag}"
  item_tag:  'orig'      — original held item (or no item, unchanged)
             'lum'       — Lum Berry assigned
             '<item_id>' — a specific non-berry item from AVAILABLE_ITEMS
  move_tag:  'orig'      — only the pokemon's original 4 moves
             'expanded'  — original 4 + learnable TMs/tutors from AVAILABLE_TMS
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from item_meta import ITEM_TYPE_BOOST, TYPE_BOOST_ITEMS

_HERE           = os.path.dirname(os.path.abspath(__file__))
_LEARNSETS_JSON = os.path.join(_HERE, 'gen3_learnsets.json')
_MOVES_JSON     = os.path.join(_HERE, 'gen3_moves.json')


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class VariantInfo:
    key:            str           # "{species}|{item_tag}|{move_tag}"
    species:        str           # lowercase PS species ID (e.g. 'zangoose')
    pipe_string:    str           # full PS pipe string with item set
    move_pool:      list[str]     # all available moves (4 for orig, >4 for expanded)
    original_moves: list[str]     # the pokemon's original 4 moves
    item_tag:       str           # 'orig', 'lum', or specific item id
    move_tag:       str           # 'orig' or 'expanded'
    is_lum:         bool          # True when item_tag == 'lum'


# ---------------------------------------------------------------------------
# Pipe string helpers
# ---------------------------------------------------------------------------

def _pipe_fields(pipe: str) -> list[str]:
    """Split a PS pipe string into its 11 fields."""
    return pipe.split('|')


def _set_pipe_item(pipe: str, item_id: str) -> str:
    """Return a new pipe string with field 2 (item) replaced by item_id."""
    fields = pipe.split('|')
    fields[2] = item_id
    return '|'.join(fields)


def _set_pipe_moves(pipe: str, moves: list[str]) -> str:
    """Return a new pipe string with field 4 (moves) replaced."""
    fields = pipe.split('|')
    fields[4] = ','.join(moves)
    return '|'.join(fields)


def _parse_pipe(pipe: str) -> tuple[str, str, list[str]]:
    """Return (species_key, current_item, original_4_moves) from a pipe string."""
    fields = _pipe_fields(pipe)
    species = fields[0].lower().replace(' ', '').replace('-', '').replace("'", '').replace('.', '')
    item    = fields[2]
    moves   = [m for m in fields[4].split(',') if m]
    return species, item, moves


# ---------------------------------------------------------------------------
# Move type checking
# ---------------------------------------------------------------------------

def _load_move_types() -> dict[str, str]:
    """Return {ps_move_id: type_name} from gen3_moves.json."""
    with open(_MOVES_JSON) as f:
        raw = json.load(f)
    result = {}
    for move_id, data in raw.items():
        if isinstance(data, dict):
            ps_id = move_id.lower().replace(' ', '').replace('-', '').replace("'", '')
            result[ps_id] = data.get('type', '')
    return result


def _has_damage_move_of_type(moves: list[str], type_name: str,
                              move_types: dict[str, str]) -> bool:
    """Return True if any move in `moves` is a damage move of the given type."""
    for move_id in moves:
        if move_types.get(move_id) == type_name:
            return True
    return False


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_variants(
    pipe_strings: list[str],
    available_items: dict[str, int],
    available_tms: dict[str, int],
    learnsets_path: str = _LEARNSETS_JSON,
) -> dict[str, VariantInfo]:
    """Build all (item, move) variant combinations for every player pokemon.

    Returns:
        dict mapping variant_key → VariantInfo
    """
    with open(learnsets_path) as f:
        learnsets: dict[str, list[str]] = json.load(f)

    move_types = _load_move_types()

    variants: dict[str, VariantInfo] = {}

    for pipe in pipe_strings:
        species, current_item, orig_moves = _parse_pipe(pipe)

        # ------------------------------------------------------------------
        # Item variants
        # ------------------------------------------------------------------
        # item_tag → pipe string with that item set
        item_variants: list[tuple[str, str]] = []

        if current_item:
            # Pokemon already has a held item — no item substitution
            item_variants.append(('orig', pipe))
        else:
            # No item: try Lum Berry + every AVAILABLE_ITEMS entry
            item_variants.append(('lum', _set_pipe_item(pipe, 'lumberry')))
            for item_id in available_items:
                # Type-boosting items: require at least one damage move of that type
                if item_id in TYPE_BOOST_ITEMS:
                    required_type = ITEM_TYPE_BOOST[item_id]
                    if not _has_damage_move_of_type(orig_moves, required_type, move_types):
                        continue
                item_variants.append((item_id, _set_pipe_item(pipe, item_id)))

        # ------------------------------------------------------------------
        # Move variants
        # ------------------------------------------------------------------
        # Find which AVAILABLE_TMS this species can learn
        species_learnset = learnsets.get(species, [])
        learnable_tms    = [m for m in available_tms if m in species_learnset
                            and m not in orig_moves]

        move_variants: list[tuple[str, list[str]]] = [('orig', orig_moves)]
        if learnable_tms:
            expanded_pool = orig_moves + learnable_tms
            move_variants.append(('expanded', expanded_pool))

        # ------------------------------------------------------------------
        # Cross-product: every item × every move variant
        # ------------------------------------------------------------------
        for item_tag, item_pipe in item_variants:
            for move_tag, move_pool in move_variants:
                key = f'{species}|{item_tag}|{move_tag}'
                # For the pipe string: always start with original 4 moves;
                # the expanded pool is tracked in move_pool and used during simulation.
                final_pipe = _set_pipe_moves(item_pipe, move_pool) if move_tag == 'expanded' else item_pipe

                variants[key] = VariantInfo(
                    key            = key,
                    species        = species,
                    pipe_string    = final_pipe,
                    move_pool      = move_pool,
                    original_moves = orig_moves,
                    item_tag       = item_tag,
                    move_tag       = move_tag,
                    is_lum         = (item_tag == 'lum'),
                )

    return variants
