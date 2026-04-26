"""
item_meta.py

Static metadata about Gen 3 held items relevant to variant optimization:
  - Type-boosting items and the type they boost
  - Status condition → cure berry mapping
"""

# ---------------------------------------------------------------------------
# Type-boosting items
# ---------------------------------------------------------------------------
# Maps PS item ID → type name string (as used in gen3_moves.json)
ITEM_TYPE_BOOST: dict[str, str] = {
    'blackglasses':  'Dark',
    'charcoal':      'Fire',
    'mysticwater':   'Water',
    'magnet':        'Electric',
    'miracleseed':   'Grass',
    'nevermeltice':  'Ice',
    'blackbelt':     'Fighting',
    'poisonbarb':    'Poison',
    'softsand':      'Ground',
    'sharpbeak':     'Flying',
    'twistedspoon':  'Psychic',
    'silverpowder':  'Bug',
    'hardstone':     'Rock',
    'spelltag':      'Ghost',
    'dragonscale':   'Dragon',
    'metalcoat':     'Steel',
    'silkscarf':     'Normal',
}

TYPE_BOOST_ITEMS: frozenset[str] = frozenset(ITEM_TYPE_BOOST)

# ---------------------------------------------------------------------------
# Status → cure berry mapping
# ---------------------------------------------------------------------------
# Maps PS status ID (as returned by p1LumBlocked) → PS berry item ID.
# 'confusion' is treated as a valid status for Lum Berry purposes.
STATUS_TO_BERRY: dict[str, str] = {
    'psn':       'pechaberry',
    'tox':       'pechaberry',
    'par':       'cheriberry',
    'brn':       'rawstberry',
    'frz':       'aspearberry',
    'slp':       'chestoberry',
    'confusion': 'persimberry',
}

# Berry to assign when no status was ever blocked.
DEFAULT_BERRY = 'sitrusberry'
