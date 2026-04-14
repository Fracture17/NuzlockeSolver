# ─── Badge boosts — set True for each badge earned that boosts stats ──────────
BADGE_BOOST_ATK = True   # Stone Badge   → Attack
BADGE_BOOST_DEF = True  # Balance Badge → Defense
BADGE_BOOST_SP  = True  # Mind Badge    → Sp. Attack + Sp. Defense
BADGE_BOOST_SPE = True  # Dynamo Badge  → Speed

# ─── Opponent bag items ────────────────────────────────────────────────────────
# Set to the PS item ID string that the current trainer carries, or None to
# disable item detection entirely.
# Valid values: 'potion', 'superpotion', 'hyperpotion', 'maxpotion', 'fullrestore', 'fullheal'
APPROVED_OPPONENT_ITEMS: str | None = "hyperpotion"

# Number of that item the trainer has (used by simulation to track uses remaining).
OPP_ITEMS: int = 2

# ─── Trainer identity ──────────────────────────────────────────────────────────
# Set to the current opponent trainer's name to enable trainer-specific AI flags.
# Recognised values: 'Winona', 'Sidney'.  None → baseline flags [0, 1, 2] only.
TRAINER_NAME: str | None = None
