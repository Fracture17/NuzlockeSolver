"""
emerald_reader.py

Reads the opponent's Pokémon team from a Pokémon Emerald game via the mgba
Python bindings (built from source at ~/mgba/build/).

Usage as a module (from a game loop):
    from emerald_reader import read_enemy_team
    team = read_enemy_team(core)       # core = mgba.core.Core instance
    log_team(team)

Usage standalone (for testing):
    python3 emerald_reader.py
    # Loads ROM + save file, reads enemy party, writes OpponentTeam.txt.
    # Set ROM_PATH and SAVE_FILE below before running.

Prerequisites:
    .venv must have mgba reachable — the .pth file at
    .venv/lib/python3.14/site-packages/mgba_build.pth points to
    /home/Fracture/mgba/build/python/lib.linux-x86_64-cpython-314/

Memory addresses are for Pokémon Emerald (US, v1.0).
Verify against the pret/pokeemerald decompilation symbol map if they seem wrong.

"""

import struct
import json
import os
from datetime import datetime

# ─── Standalone-mode file paths (dummy values — update before running directly) ─
ROM_PATH  = "/path/to/pokemon_emerald.gba"
SAVE_FILE = "/path/to/save.sav"   # .sav battery-save file; must be at a battle-start state

_HERE      = os.path.dirname(os.path.abspath(__file__))
LOG_FILE       = os.path.join(_HERE, "OpponentTeam.txt")
SPECIES_JSON   = os.path.join(_HERE, "gen3_species.json")
MOVES_JSON     = os.path.join(_HERE, "gen3_move_names.json")
ITEMS_JSON     = os.path.join(_HERE, "gen3_items.json")
ABILITIES_JSON = os.path.join(_HERE, "gen3_abilities.json")

# ─── Pokémon Emerald (US v1.0) memory addresses ──────────────────────────────
# gEnemyParty: 6 consecutive 100-byte encrypted Pokémon structs
ENEMY_PARTY_ADDR       = 0x02024744
# gEnemyPartyCount: u8
ENEMY_PARTY_COUNT_ADDR = 0x02024A2C

POKEMON_SIZE = 100  # bytes per party struct
MAX_VALID_SPECIES = 440  # species IDs above this after decryption are garbage

# ─── Player party ─────────────────────────────────────────────────────────────
# gPlayerParty: 6 × 100-byte encrypted Pokémon structs
PLAYER_PARTY_ADDR       = 0x020244EC
# gPlayerPartyCount: u8
PLAYER_PARTY_COUNT_ADDR = 0x020244E9

# ─── PC box storage ───────────────────────────────────────────────────────────
# gPokemonStoragePtr: stable IWRAM pointer that always holds the current EWRAM address
# of PokemonStorage. Emerald's DMA randomization shifts save blocks in EWRAM on every
# warp/menu transition — there is no fixed EWRAM address; always dereference this pointer.
# Layout: +0x0000 = u8 currentBox, +0x0001 = boxes[14][30] (80 bytes × 420 slots).
POKEMON_STORAGE_PTR_ADDR = 0x03005D94   # gPokemonStoragePtr (stable IWRAM pointer)
BOX_OFFSET        = 4     # boxes[14][30] start at struct_base + 4 (1 byte currentBox + 3 bytes ARM alignment padding)
PLAYER_BOX_COUNT  = 14
BOX_SLOTS_PER_BOX = 30
BOX_POKEMON_SIZE  = 80    # bytes per BoxPokemon (no stats block in PC)

PLAYER_LOG_FILE = os.path.join(_HERE, "PlayerPokemon.txt")
BOX_DIAG_FILE   = os.path.join(_HERE, "BoxAddrDiag.txt")

# ─── EWRAM dump ───────────────────────────────────────────────────────────────
EWRAM_BASE    = 0x02000000
EWRAM_SIZE    = 0x40000      # 256 KB
RAM_DUMP_FILE = os.path.join(_HERE, "ram_ewram.bin")

# ─── Internal species ID → name (GBA internal ordering, NOT national dex) ────
# Gen 1+2 (IDs 1–251): internal ID == national dex, looked up via species_db at runtime.
# IDs 252–276: legacy Unown letter forms (B–Z) inserted before Gen 3 Pokemon.
# Gen 3 (IDs 277–411): follow Hoenn regional order, NOT national dex order.
# Source: pret/pokeemerald include/constants/species.h
_UNOWN_LETTERS = "BCDEFGHIJKLMNOPQRSTUVWXYZ"
_GEN3_INTERNAL_NAMES: dict[int, str] = {
    # Legacy Unown forms (252–276)
    **{252 + i: f"Unown-{_UNOWN_LETTERS[i]}" for i in range(25)},
    # Gen 3 Pokemon in internal order (277–411)
    277: "Treecko",    278: "Grovyle",    279: "Sceptile",
    280: "Torchic",    281: "Combusken",  282: "Blaziken",
    283: "Mudkip",     284: "Marshtomp",  285: "Swampert",
    286: "Poochyena",  287: "Mightyena",
    288: "Zigzagoon",  289: "Linoone",
    290: "Wurmple",    291: "Silcoon",    292: "Beautifly",
    293: "Cascoon",    294: "Dustox",
    295: "Lotad",      296: "Lombre",     297: "Ludicolo",
    298: "Seedot",     299: "Nuzleaf",    300: "Shiftry",
    301: "Nincada",    302: "Ninjask",    303: "Shedinja",
    304: "Taillow",    305: "Swellow",
    306: "Shroomish",  307: "Breloom",
    308: "Spinda",
    309: "Wingull",    310: "Pelipper",
    311: "Surskit",    312: "Masquerain",
    313: "Wailmer",    314: "Wailord",
    315: "Skitty",     316: "Delcatty",
    317: "Kecleon",
    318: "Baltoy",     319: "Claydol",
    320: "Nosepass",
    321: "Torkoal",
    322: "Sableye",
    323: "Barboach",   324: "Whiscash",
    325: "Luvdisc",
    326: "Corphish",   327: "Crawdaunt",
    328: "Feebas",     329: "Milotic",
    330: "Carvanha",   331: "Sharpedo",
    332: "Trapinch",   333: "Vibrava",    334: "Flygon",
    335: "Makuhita",   336: "Hariyama",
    337: "Electrike",  338: "Manectric",
    339: "Numel",      340: "Camerupt",
    341: "Spheal",     342: "Sealeo",     343: "Walrein",
    344: "Cacnea",     345: "Cacturne",
    346: "Snorunt",    347: "Glalie",
    348: "Lunatone",   349: "Solrock",
    350: "Azurill",
    351: "Spoink",     352: "Grumpig",
    353: "Plusle",     354: "Minun",
    355: "Mawile",
    356: "Meditite",   357: "Medicham",
    358: "Swablu",     359: "Altaria",
    360: "Wynaut",
    361: "Duskull",    362: "Dusclops",
    363: "Roselia",
    364: "Slakoth",    365: "Vigoroth",   366: "Slaking",
    367: "Gulpin",     368: "Swalot",
    369: "Tropius",
    370: "Whismur",    371: "Loudred",    372: "Exploud",
    373: "Clamperl",   374: "Huntail",    375: "Gorebyss",
    376: "Absol",
    377: "Shuppet",    378: "Banette",
    379: "Seviper",    380: "Zangoose",
    381: "Relicanth",
    382: "Aron",       383: "Lairon",     384: "Aggron",
    385: "Castform",
    386: "Volbeat",    387: "Illumise",
    388: "Lileep",     389: "Cradily",
    390: "Anorith",    391: "Armaldo",
    392: "Ralts",      393: "Kirlia",     394: "Gardevoir",
    395: "Bagon",      396: "Shelgon",    397: "Salamence",
    398: "Beldum",     399: "Metang",     400: "Metagross",
    401: "Regirock",   402: "Regice",     403: "Registeel",
    404: "Kyogre",     405: "Groudon",    406: "Rayquaza",
    407: "Latias",     408: "Latios",
    409: "Jirachi",    410: "Deoxys",
    411: "Chimecho",
    412: "Egg",
}


# ─── Internal species ID → national dex number ───────────────────────────────
# gen3_species.json and gen3_abilities.json are keyed by *national dex* number.
# For Gen 1/2 Pokémon (IDs 1–251) the internal ID equals the national dex.
# For Gen 3 Pokémon (IDs 277–411) the internal Hoenn ordering diverges from national dex.
_GEN3_INTERNAL_TO_NATIONAL: dict[int, int] = {
    # Unown letter forms (252–276) all share national dex 201
    **{252 + i: 201 for i in range(25)},
    # Gen 3 Pokémon (internal Hoenn order → national dex)
    277: 252, 278: 253, 279: 254,   # Treecko line
    280: 255, 281: 256, 282: 257,   # Torchic line
    283: 258, 284: 259, 285: 260,   # Mudkip line
    286: 261, 287: 262,             # Poochyena line
    288: 263, 289: 264,             # Zigzagoon line
    290: 265, 291: 266, 292: 267,   # Wurmple/Silcoon/Beautifly
    293: 268, 294: 269,             # Cascoon/Dustox
    295: 270, 296: 271, 297: 272,   # Lotad line
    298: 273, 299: 274, 300: 275,   # Seedot line
    301: 290, 302: 291, 303: 292,   # Nincada line
    304: 276, 305: 277,             # Taillow line
    306: 285, 307: 286,             # Shroomish line
    308: 327,                       # Spinda
    309: 278, 310: 279,             # Wingull line
    311: 283, 312: 284,             # Surskit line
    313: 320, 314: 321,             # Wailmer line
    315: 300, 316: 301,             # Skitty line
    317: 352,                       # Kecleon
    318: 343, 319: 344,             # Baltoy line
    320: 299,                       # Nosepass
    321: 324,                       # Torkoal
    322: 302,                       # Sableye
    323: 339, 324: 340,             # Barboach line
    325: 370,                       # Luvdisc
    326: 341, 327: 342,             # Corphish line
    328: 349, 329: 350,             # Feebas line
    330: 318, 331: 319,             # Carvanha line
    332: 328, 333: 329, 334: 330,   # Trapinch line
    335: 296, 336: 297,             # Makuhita line
    337: 309, 338: 310,             # Electrike line
    339: 322, 340: 323,             # Numel line
    341: 363, 342: 364, 343: 365,   # Spheal line
    344: 331, 345: 332,             # Cacnea line
    346: 361, 347: 362,             # Snorunt line
    348: 337, 349: 338,             # Lunatone/Solrock
    350: 298,                       # Azurill
    351: 325, 352: 326,             # Spoink line
    353: 311, 354: 312,             # Plusle/Minun
    355: 303,                       # Mawile
    356: 307, 357: 308,             # Meditite line
    358: 333, 359: 334,             # Swablu line
    360: 360,                       # Wynaut
    361: 355, 362: 356,             # Duskull line
    363: 315,                       # Roselia
    364: 287, 365: 288, 366: 289,   # Slakoth line
    367: 316, 368: 317,             # Gulpin line
    369: 357,                       # Tropius
    370: 293, 371: 294, 372: 295,   # Whismur line
    373: 366, 374: 367, 375: 368,   # Clamperl line
    376: 359,                       # Absol
    377: 353, 378: 354,             # Shuppet line
    379: 336, 380: 335,             # Seviper/Zangoose
    381: 369,                       # Relicanth
    382: 304, 383: 305, 384: 306,   # Aron line
    385: 351,                       # Castform
    386: 313, 387: 314,             # Volbeat/Illumise
    388: 345, 389: 346,             # Lileep line
    390: 347, 391: 348,             # Anorith line
    392: 280, 393: 281, 394: 282,   # Ralts line
    395: 371, 396: 372, 397: 373,   # Bagon line
    398: 374, 399: 375, 400: 376,   # Beldum line
    401: 377, 402: 378, 403: 379,   # Regis
    404: 382, 405: 383, 406: 384,   # Weather trio
    407: 380, 408: 381,             # Lati@s
    409: 385, 410: 386,             # Jirachi/Deoxys
    411: 358,                       # Chimecho
}


def _internal_to_national_dex(sid: int) -> int:
    """Convert a GBA internal species ID to national dex number for JSON lookups."""
    return _GEN3_INTERNAL_TO_NATIONAL.get(sid, sid)


def internal_species_name(sid: int, species_db: dict) -> str:
    """Translate a GBA internal species ID to a display name.

    For IDs 1–251 the internal ID matches the national dex, so we fall back
    to species_db (which is keyed by national dex number).  For 252+ the
    internal ordering diverges from national dex and we use _GEN3_INTERNAL_NAMES.
    """
    if sid in _GEN3_INTERNAL_NAMES:
        return _GEN3_INTERNAL_NAMES[sid]
    return species_db.get(str(sid), f"Species#{sid}")


# ─── Nature table (PID % 25) ─────────────────────────────────────────────────
NATURES = [
    "Hardy",  "Lonely", "Brave",   "Adamant", "Naughty",
    "Bold",   "Docile", "Relaxed", "Impish",  "Lax",
    "Timid",  "Hasty",  "Serious", "Jolly",   "Naive",
    "Modest", "Mild",   "Quiet",   "Bashful", "Rash",
    "Calm",   "Gentle", "Sassy",   "Careful", "Quirky",
]

# ─── Substructure order table ────────────────────────────────────────────────
# SUBORDER[PID % 24][position] = substructure index
# Indices: Growth=0, Attacks=1, EVs=2, Misc=3
SUBORDER = [
    (0,1,2,3), (0,1,3,2), (0,2,1,3), (0,2,3,1), (0,3,1,2), (0,3,2,1),
    (1,0,2,3), (1,0,3,2), (1,2,0,3), (1,2,3,0), (1,3,0,2), (1,3,2,0),
    (2,0,1,3), (2,0,3,1), (2,1,0,3), (2,1,3,0), (2,3,0,1), (2,3,1,0),
    (3,0,1,2), (3,0,2,1), (3,1,0,2), (3,1,2,0), (3,2,0,1), (3,2,1,0),
]


# ─── mgba memory helpers ─────────────────────────────────────────────────────
# mgba Python API:
#   core.memory.u8[addr]   → int (1 byte)
#   core.memory.u32[addr]  → int (4 bytes, little-endian)

def _read_bytes(core, addr: int, n: int) -> bytes:
    """Read n bytes from GBA memory via the mgba core object."""
    buf = bytearray(n)
    i = 0
    while i + 4 <= n:
        struct.pack_into('<I', buf, i, core.memory.u32[addr + i])
        i += 4
    while i < n:
        buf[i] = core.memory.u8[addr + i]
        i += 1
    return bytes(buf)


# ─── Pokémon struct decryption ────────────────────────────────────────────────

def _get_substructure(data: bytes, order: tuple, idx: int) -> bytes:
    """Return the 12-byte substructure at logical index idx."""
    pos = order.index(idx)
    return data[pos * 12 : pos * 12 + 12]


def decrypt_pokemon(raw: bytes) -> dict:
    """
    Decrypt and parse a 100-byte Gen 3 Pokémon struct.

    Gen 3 struct layout:
      [0-3]   PID  (personality value, u32)
      [4-7]   OTID (original trainer ID, u32)  — encryption key = PID ^ OTID
      [8-31]  nickname, language, OT name, markings, checksum, padding
      [32-79] 4 × 12-byte substructures (encrypted, order = PID % 24)
      [80-99] unencrypted battle stats (status, level, HP, Atk, Def, Spe, SpA, SpD)

    Substructures (12 bytes each):
      Growth  (0): species u16, held_item u16, exp u32, pp_bonuses u8, friendship u8, pad u16
      Attacks (1): move[4] u16×4, pp[4] u8×4
      EVs     (2): hp/atk/def/spe/spatk/spdef u8×6, contest stats u8×6
      Misc    (3): pokerus u8, met_loc u8, origins u16,
                   iv_egg_ability u32 (bits 0-4 HP, 5-9 Atk, 10-14 Def, 15-19 Spe,
                                       20-24 SpA, 25-29 SpD, 30 egg, 31 ability_slot),
                   ribbons u32

    Returns a dict ready for to_showdown().
    """
    pid  = struct.unpack_from('<I', raw, 0)[0]
    otid = struct.unpack_from('<I', raw, 4)[0]
    key  = pid ^ otid

    # Decrypt the 48-byte data block
    data = bytearray(raw[32:80])
    for i in range(0, 48, 4):
        word = struct.unpack_from('<I', data, i)[0]
        struct.pack_into('<I', data, i, word ^ key)
    data = bytes(data)

    order = SUBORDER[pid % 24]

    # Growth substructure
    g         = _get_substructure(data, order, 0)
    species   = struct.unpack_from('<H', g, 0)[0]
    held_item = struct.unpack_from('<H', g, 2)[0]
    exp       = struct.unpack_from('<I', g, 4)[0]

    # Attacks substructure
    a     = _get_substructure(data, order, 1)
    moves = [struct.unpack_from('<H', a, i * 2)[0] for i in range(4)]
    pp    = [struct.unpack_from('<B', a, 8 + i)[0]  for i in range(4)]

    # EVs substructure
    e   = _get_substructure(data, order, 2)
    evs = {
        'hp':    e[0], 'atk':  e[1], 'def':   e[2],
        'spe':   e[3], 'spatk': e[4], 'spdef': e[5],
    }

    # Misc substructure
    m       = _get_substructure(data, order, 3)
    iv_word = struct.unpack_from('<I', m, 4)[0]
    ivs = {
        'hp':    (iv_word >>  0) & 0x1F,
        'atk':   (iv_word >>  5) & 0x1F,
        'def':   (iv_word >> 10) & 0x1F,
        'spe':   (iv_word >> 15) & 0x1F,
        'spatk': (iv_word >> 20) & 0x1F,
        'spdef': (iv_word >> 25) & 0x1F,
    }
    ability_slot = (iv_word >> 31) & 0x1

    # Unencrypted battle stats
    status_raw = struct.unpack_from('<I', raw, 80)[0]
    if   status_raw & 0x40: status = 'par'
    elif status_raw & 0x10: status = 'brn'
    elif status_raw & 0x20: status = 'frz'
    elif status_raw & 0x08: status = 'psn'
    elif status_raw & 0x80: status = 'tox'
    elif status_raw & 0x07: status = 'slp'
    else:                   status = ''

    level      = struct.unpack_from('<B', raw, 84)[0]
    current_hp = struct.unpack_from('<H', raw, 86)[0]
    max_hp     = struct.unpack_from('<H', raw, 88)[0]
    atk        = struct.unpack_from('<H', raw, 90)[0]
    def_       = struct.unpack_from('<H', raw, 92)[0]
    spe        = struct.unpack_from('<H', raw, 94)[0]
    spatk      = struct.unpack_from('<H', raw, 96)[0]
    spdef      = struct.unpack_from('<H', raw, 98)[0]

    return {
        'pid':          pid,
        'species':      species,
        'held_item':    held_item,
        'exp':          exp,
        'moves':        moves,
        'pp':           pp,
        'evs':          evs,
        'ivs':          ivs,
        'ability_slot': ability_slot,
        'nature':       NATURES[pid % 25],
        'level':        level,
        'current_hp':   current_hp,
        'max_hp':       max_hp,
        'status':       status,
        'stats': {
            'atk': atk, 'def': def_, 'spe': spe, 'spatk': spatk, 'spdef': spdef,
        },
    }


# ─── Pokémon Showdown export formatter ───────────────────────────────────────

def to_showdown(
    pkmn: dict,
    species_db:   dict,
    moves_db:     dict,
    items_db:     dict,
    abilities_db: dict,
) -> str:
    """Format a parsed Pokémon dict as a Pokémon Showdown team-export entry."""
    nat_dex = _internal_to_national_dex(pkmn['species'])
    name    = internal_species_name(pkmn['species'], species_db)

    item = ''
    if pkmn['held_item']:
        item = items_db.get(str(pkmn['held_item']), f"Item#{pkmn['held_item']}")

    ability_map = abilities_db.get(str(nat_dex), {})
    ability = ability_map.get(str(pkmn['ability_slot']),
              ability_map.get('0', 'Unknown'))

    ev_map = [
        ('HP',  pkmn['evs']['hp']),
        ('Atk', pkmn['evs']['atk']),
        ('Def', pkmn['evs']['def']),
        ('SpA', pkmn['evs']['spatk']),
        ('SpD', pkmn['evs']['spdef']),
        ('Spe', pkmn['evs']['spe']),
    ]
    ev_parts = [f"{v} {k}" for k, v in ev_map if v]

    iv_map = [
        ('HP',  pkmn['ivs']['hp']),
        ('Atk', pkmn['ivs']['atk']),
        ('Def', pkmn['ivs']['def']),
        ('SpA', pkmn['ivs']['spatk']),
        ('SpD', pkmn['ivs']['spdef']),
        ('Spe', pkmn['ivs']['spe']),
    ]
    iv_parts = [f"{v} {k}" for k, v in iv_map if v != 31]

    move_names = [
        moves_db.get(str(mid), f"Move#{mid}")
        for mid in pkmn['moves'] if mid != 0
    ]

    lines = []
    lines.append(f"{name} @ {item}" if item else name)
    lines.append(f"Ability: {ability}")
    lines.append(f"Level: {pkmn['level']}")
    if ev_parts:
        lines.append(f"EVs: {' / '.join(ev_parts)}")
    if iv_parts:
        lines.append(f"IVs: {' / '.join(iv_parts)}")
    lines.append(pkmn['nature'])
    for mv in move_names:
        lines.append(f"- {mv}")
    return '\n'.join(lines)


# ─── Box Pokémon helpers ──────────────────────────────────────────────────────

def _level_from_exp(exp: int) -> int:
    """Approximate level from experience using the Medium Fast growth rate (level = cbrt(exp)).
    Exact for Medium Fast species; approximate for others (Fast, Slow, etc.).
    """
    if exp <= 0:
        return 1
    return max(1, min(100, int(round(exp ** (1 / 3)))))


def decrypt_box_pokemon(raw: bytes) -> dict | None:
    """
    Decrypt and parse an 80-byte Gen 3 BoxPokemon struct.

    Identical to decrypt_pokemon for the first 80 bytes (header + encrypted block).
    No unencrypted battle stats — level is approximated from experience points.

    Returns None for empty slots (species == 0), out-of-range species, or eggs.
    Wrapped in try/except so malformed/uninitialized memory is silently skipped.
    """
    try:
        pid  = struct.unpack_from('<I', raw, 0)[0]
        otid = struct.unpack_from('<I', raw, 4)[0]
        key  = pid ^ otid

        data = bytearray(raw[32:80])
        for i in range(0, 48, 4):
            word = struct.unpack_from('<I', data, i)[0]
            struct.pack_into('<I', data, i, word ^ key)
        data = bytes(data)

        order = SUBORDER[pid % 24]

        g         = _get_substructure(data, order, 0)
        species   = struct.unpack_from('<H', g, 0)[0]
        held_item = struct.unpack_from('<H', g, 2)[0]
        exp       = struct.unpack_from('<I', g, 4)[0]

        if species == 0 or species > MAX_VALID_SPECIES:
            return None

        a     = _get_substructure(data, order, 1)
        moves = [struct.unpack_from('<H', a, i * 2)[0] for i in range(4)]
        pp    = [struct.unpack_from('<B', a, 8 + i)[0]  for i in range(4)]

        e   = _get_substructure(data, order, 2)
        evs = {
            'hp':    e[0], 'atk':  e[1], 'def':   e[2],
            'spe':   e[3], 'spatk': e[4], 'spdef': e[5],
        }

        m       = _get_substructure(data, order, 3)
        iv_word = struct.unpack_from('<I', m, 4)[0]
        ivs = {
            'hp':    (iv_word >>  0) & 0x1F,
            'atk':   (iv_word >>  5) & 0x1F,
            'def':   (iv_word >> 10) & 0x1F,
            'spe':   (iv_word >> 15) & 0x1F,
            'spatk': (iv_word >> 20) & 0x1F,
            'spdef': (iv_word >> 25) & 0x1F,
        }
        ability_slot = (iv_word >> 31) & 0x1
        is_egg       = (iv_word >> 30) & 0x1

        if is_egg:
            return None

        return {
            'pid':          pid,
            'species':      species,
            'held_item':    held_item,
            'exp':          exp,
            'moves':        moves,
            'pp':           pp,
            'evs':          evs,
            'ivs':          ivs,
            'ability_slot': ability_slot,
            'nature':       NATURES[pid % 25],
            'level':        _level_from_exp(exp),
            'current_hp':   None,
            'max_hp':       None,
            'stats':        None,
        }
    except Exception:
        return None


# ─── Public API ───────────────────────────────────────────────────────────────

def read_enemy_team(core) -> list:
    """
    Read and decrypt all Pokémon in the enemy party from GBA memory.

    Scans all 6 slots and filters by valid species rather than using the count byte,
    because gEnemyPartyCount may return 0 mid-battle even when Pokémon are present.
    """
    team = []
    for i in range(6):
        raw = _read_bytes(core, ENEMY_PARTY_ADDR + i * POKEMON_SIZE, POKEMON_SIZE)
        try:
            p = decrypt_pokemon(raw)
        except Exception:
            continue
        if p['species'] == 0 or p['species'] > MAX_VALID_SPECIES:
            continue
        team.append(p)
    return team


def read_player_team(core) -> list:
    """
    Read and decrypt all Pokémon in the player's active party from GBA memory.

    Scans all 6 slots and filters by valid species (same approach as read_enemy_team).
    """
    team = []
    for i in range(6):
        raw = _read_bytes(core, PLAYER_PARTY_ADDR + i * POKEMON_SIZE, POKEMON_SIZE)
        try:
            p = decrypt_pokemon(raw)
        except Exception:
            continue
        if p['species'] == 0 or p['species'] > MAX_VALID_SPECIES:
            continue
        team.append(p)
    return team


def read_player_box(core) -> list:
    """
    Read all Pokémon from the player's PC boxes.

    Dereferences gPokemonStoragePtr (stable IWRAM pointer) to find the current EWRAM
    location of PokemonStorage. Reads from the 4-byte-aligned storage_base so that
    all u32 reads in _read_bytes remain aligned; boxes are parsed at BOX_OFFSET within
    the buffer.

    IWRAM access uses core.memory.iwram.u32[offset] where offset = addr - 0x03000000.
    Address formula: [0x03005D94] + 1 + (box * 0x960) + (slot * 0x50)
    """
    # core.memory.u32 has size=0x100000000 (full 4GB GBA address space, base=0),
    # so it accepts any GBA address including IWRAM (0x03xxxxxx) — byte-indexed.
    storage_base = core.memory.u32[POKEMON_STORAGE_PTR_ADDR]
    print(f"[box_diag] IWRAM ptr 0x{POKEMON_STORAGE_PTR_ADDR:08X}  "
          f"storage_base=0x{storage_base:08X}  "
          f"valid_ewram={0x02000000 <= storage_base <= 0x0203FFFF}")

    # Read from storage_base (4-byte aligned) to avoid misaligned u32 reads in _read_bytes
    total_bytes = BOX_OFFSET + PLAYER_BOX_COUNT * BOX_SLOTS_PER_BOX * BOX_POKEMON_SIZE  # 33,601
    block = _read_bytes(core, storage_base, total_bytes)

    current_box = block[0]
    print(f"[box_diag] storage_base=0x{storage_base:08X}  currentBox={current_box}")

    boxes = []
    for b in range(PLAYER_BOX_COUNT):
        box_slots = []
        for s in range(BOX_SLOTS_PER_BOX):
            offset = BOX_OFFSET + (b * BOX_SLOTS_PER_BOX + s) * BOX_POKEMON_SIZE
            raw    = block[offset : offset + BOX_POKEMON_SIZE]
            pkmn   = decrypt_box_pokemon(raw)
            if pkmn is not None:
                box_slots.append(pkmn)
        if box_slots:
            print(f"[box_diag] Box {b+1}: {len(box_slots)} Pokémon found")
        boxes.append(box_slots)
    return boxes


def _write_box_section(boxes: list, lines: list, species_db, moves_db, items_db, abilities_db):
    """Append PC box entries (list of 14 slot-lists) to lines."""
    for b, box_slots in enumerate(boxes):
        if not box_slots:
            continue
        lines.append(f"=== PC Box {b + 1} ===")
        lines.append("")
        box_entries = [
            to_showdown(p, species_db, moves_db, items_db, abilities_db)
            for p in box_slots
        ]
        lines.append('\n\n'.join(box_entries))
        lines.append("")


def log_player_pokemon(
    team: list,
    boxes: list,            # list of 14 lists from read_player_box()
    log_file: str = PLAYER_LOG_FILE,
) -> None:
    """
    Write the player's active party and PC box Pokémon to log_file in Showdown format.

    team : list of dicts from read_player_team()
    boxes: list of 14 slot-lists from read_player_box()
    """
    with open(SPECIES_JSON)   as f: species_db   = json.load(f)
    with open(MOVES_JSON)     as f: moves_db     = json.load(f)
    with open(ITEMS_JSON)     as f: items_db     = json.load(f)
    with open(ABILITIES_JSON) as f: abilities_db = json.load(f)

    box_total = sum(len(sl) for sl in boxes)
    timestamp = datetime.now().isoformat(timespec='seconds')

    lines = [
        f"# Player Pokémon — captured {timestamp}",
        f"# {len(team)} in party, {box_total} in PC",
        "",
        "=== Active Party ===",
        "",
    ]

    if team:
        party_entries = [
            to_showdown(p, species_db, moves_db, items_db, abilities_db)
            for p in team
        ]
        lines.append('\n\n'.join(party_entries))
    else:
        lines.append("(empty)")
    lines.append("")

    _write_box_section(boxes, lines, species_db, moves_db, items_db, abilities_db)

    with open(log_file, 'w') as f:
        f.write('\n'.join(lines))

    print(f"[emerald_reader] Wrote {len(team)} party + {box_total} box Pokémon to {log_file}")


def diagnose_box_addr(core, candidates=None, diag_file: str = BOX_DIAG_FILE) -> None:
    """
    Write diagnostic info for candidate gPokemonStorage addresses to a file.

    Press F3 in-game to trigger this. Open BoxAddrDiag.txt and find the address where
    currentBox is 0–13 AND slot0_species is 0 (empty box) or a valid species. That is the
    correct PLAYER_BOX_ADDR. Update the constant at the top of this file accordingly.
    """
    if candidates is None:
        candidates = [
            0x02034BCC,  # FireRed reference
            0x020328CC,  # alternate guess
            0x020353AC,  # alternate guess
            0x02035748,  # original (incorrect) guess
        ]
    lines = [f"# Box address diagnostic — {datetime.now().isoformat(timespec='seconds')}", ""]
    for addr in candidates:
        box_num = core.memory.u8[addr]
        raw = _read_bytes(core, addr + 4, BOX_POKEMON_SIZE)
        try:
            pid     = struct.unpack_from('<I', raw, 0)[0]
            otid    = struct.unpack_from('<I', raw, 4)[0]
            key     = pid ^ otid
            data    = bytearray(raw[32:80])
            for i in range(0, 48, 4):
                word = struct.unpack_from('<I', data, i)[0]
                struct.pack_into('<I', data, i, word ^ key)
            order   = SUBORDER[pid % 24]
            g       = _get_substructure(bytes(data), order, 0)
            species = struct.unpack_from('<H', g, 0)[0]
        except Exception:
            species = -1
        box_ok     = "valid box#"    if 0 <= box_num <= 13              else "INVALID BOX#"
        species_ok = "valid species" if 0 < species <= MAX_VALID_SPECIES else "zero/invalid"
        lines.append(
            f"0x{addr:08X}: currentBox={box_num:3d}  slot0_species={species:5d}"
            f"  [{box_ok}]  [{species_ok}]"
        )
    lines += ["", "Correct address: valid box# (0–13) AND slot0_species is 0 or valid."]
    with open(diag_file, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print(f"[emerald_reader] Box diagnostic written to {diag_file}")


def dump_ram(core, dump_file: str = RAM_DUMP_FILE) -> None:
    """Dump the full 256KB EWRAM to a binary file for offline address analysis."""
    data = _read_bytes(core, EWRAM_BASE, EWRAM_SIZE)
    with open(dump_file, 'wb') as f:
        f.write(data)
    print(f"[emerald_reader] EWRAM dumped ({EWRAM_SIZE // 1024} KB) to {dump_file}")


def log_team(team: list, log_file: str = LOG_FILE) -> None:
    """
    Format a team (from read_enemy_team) as a Showdown export and write to log_file.
    Loads the JSON lookup tables from the paths defined at module level.
    """
    with open(SPECIES_JSON)   as f: species_db   = json.load(f)
    with open(MOVES_JSON)     as f: moves_db     = json.load(f)
    with open(ITEMS_JSON)     as f: items_db     = json.load(f)
    with open(ABILITIES_JSON) as f: abilities_db = json.load(f)

    entries = [
        to_showdown(p, species_db, moves_db, items_db, abilities_db)
        for p in team
    ]

    timestamp = datetime.now().isoformat(timespec='seconds')
    output = (
        f"# Opponent team — captured {timestamp}\n"
        f"# {len(team)} Pokémon\n\n"
        + '\n\n'.join(entries)
        + '\n'
    )

    with open(log_file, 'w') as f:
        f.write(output)

    print(f"[emerald_reader] Wrote {len(team)} Pokémon to {log_file}")


# ─── Standalone entry point ───────────────────────────────────────────────────

def main():
    """Load a ROM + save file and capture the enemy team. For testing only."""
    import mgba.core as _mgba_core

    core = _mgba_core.load_path(ROM_PATH)
    core.config.init("emerald_reader")
    core.config.load()
    core.load_save_file(SAVE_FILE, False)
    core.reset()

    team = read_enemy_team(core)
    if not team:
        print("[emerald_reader] No enemy Pokémon found — are you at a battle start?")
        return

    log_team(team)


if __name__ == "__main__":
    main()
