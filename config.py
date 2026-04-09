# ─── Badge boosts — set True for each badge earned that boosts stats ──────────
BADGE_BOOST_ATK = True   # Stone Badge   → Attack
BADGE_BOOST_DEF = False  # Balance Badge → Defense
BADGE_BOOST_SP  = False  # Mind Badge    → Sp. Attack + Sp. Defense
BADGE_BOOST_SPE = False  # Dynamo Badge  → Speed

# ─── Opponent bag items — only items in this set will be recognised as real ───
# Replace set() with a set literal of PS item names the current trainer carries.
# Valid values: 'potion', 'superpotion', 'hyperpotion', 'maxpotion', 'fullrestore', 'fullheal'
# When empty, no item usage will ever be detected (safe default).
APPROVED_OPPONENT_ITEMS: set[str] = {"superpotion"}
