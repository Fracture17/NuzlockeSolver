# ─── External dependency paths — edit these for your machine ──────────────────
import os as _os, sys as _sys

ROM_PATH    = "/home/Fracture/Downloads/emerald.gba"
SAVE_FILE   = "/home/Fracture/Downloads/emerald.sav"
NODE_SCRIPT = "/home/Fracture/WebstormProjects/pokemon-showdown-master/Connection.js"

# Prefer mgba_build/ bundled inside this project; fall back to a system build path.
_mgba_bundled   = _os.path.join(_os.path.dirname(__file__), 'mgba_build')
MGBA_BUILD_PATH = _mgba_bundled if _os.path.exists(_mgba_bundled) else \
    "/home/Fracture/mgba/build/python/lib.linux-x86_64-cpython-314"
del _mgba_bundled

# ─── Badge boosts — set True for each badge earned that boosts stats ──────────
BADGE_BOOST_ATK = True   # Stone Badge   → Attack
BADGE_BOOST_DEF = True  # Balance Badge → Defense
BADGE_BOOST_SP  = True  # Mind Badge    → Sp. Attack + Sp. Defense
BADGE_BOOST_SPE = True  # Dynamo Badge  → Speed

# ─── Opponent bag items ────────────────────────────────────────────────────────
# Set to the PS item ID string that the current trainer carries, or None to
# disable item detection entirely.
# Valid values: 'potion', 'superpotion', 'hyperpotion', 'maxpotion', 'fullrestore', 'fullheal'
APPROVED_OPPONENT_ITEMS: str | None = "fullrestore"

# Number of that item the trainer has (used by simulation to track uses remaining).
OPP_ITEMS: int = 4

# ─── Available player items & TMs ─────────────────────────────────────────────
# Non-berry items available for assignment (PS item ID → quantity).
# Berries are unlimited and are not listed here.
#AVAILABLE_ITEMS: dict[str, int] = {"nevermeltice": 1, "poisonbarb": 1, "blackglasses": 1, "shellbell": 1, "mysticwater": 1}
AVAILABLE_ITEMS: dict[str, int] = {}

# TM and tutor moves available to teach (PS move ID → quantity).
#AVAILABLE_TMS: dict[str, int] = {"shadowball": 1, "sludgebomb": 1, "rest": 1, "sleeptalk": 1, "explosion": 1, "substitute": 1, "doubleedge": 1, "overheat": 1, "surf": 100, "rocktomb": 1, "aerialace": 1, "earthquake": 1, "waterpulse": 1, "calmmind": 1, "psychic": 2, "toxic": 1, "bulkup": 1, "bulletseed": 1, "icebeam": 2, "return": 2, "shockwave": 1}
AVAILABLE_TMS: dict[str, int] = {}

# ─── Trainer identity ──────────────────────────────────────────────────────────
# Set to the current opponent trainer's name to enable trainer-specific AI flags.
# Recognised values: 'Winona', 'Sidney'.  None → baseline flags [0, 1, 2] only.
TRAINER_NAME: str | None = None

# ─── Path existence checks ─────────────────────────────────────────────────────
def _warn_missing_paths():
    for _label, _path in [
        ('ROM',               ROM_PATH),
        ('Save file',         SAVE_FILE),
        ('Node.js script',    NODE_SCRIPT),
        ('mgba build',        MGBA_BUILD_PATH),
    ]:
        if not _os.path.exists(_path):
            print(f'[config] WARNING: {_label} not found: {_path}', file=_sys.stderr)
_warn_missing_paths()
del _warn_missing_paths, _os, _sys
