"""
berry_logic.py

Determines the optimal cure berry for a pokemon based on what statuses
the Lum Berry blocked during matchup simulations.
"""

from item_meta import STATUS_TO_BERRY, DEFAULT_BERRY


def most_common_status(statuses: list[str]) -> str | None:
    """Return the most-frequent status in the list.

    Uses first occurrence as a tiebreaker (earlier statuses win ties).
    Returns None if the list is empty.
    """
    if not statuses:
        return None
    # Build count dict preserving first-occurrence order
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for i, s in enumerate(statuses):
        if s not in counts:
            counts[s] = 0
            first_seen[s] = i
        counts[s] += 1
    # Sort by (-count, first_seen index) — highest count wins; earliest occurrence breaks ties
    best = min(counts, key=lambda s: (-counts[s], first_seen[s]))
    return best


def assign_berry(lum_results_per_opp: dict[str, list[str]]) -> dict[str, str]:
    """Return {opp_name: berry_ps_id} for each opponent matchup.

    Args:
        lum_results_per_opp: maps opponent name → list of statuses blocked
                             by Lum Berry across all battles of that matchup.

    Returns:
        dict mapping opponent name → PS berry item ID to equip for that matchup.
    """
    result = {}
    for opp, statuses in lum_results_per_opp.items():
        status = most_common_status(statuses)
        result[opp] = STATUS_TO_BERRY.get(status, DEFAULT_BERRY) if status else DEFAULT_BERRY
    return result


def best_berry_for_flex(scores_per_opp: dict[str, float],
                        berry_map_per_opp: dict[str, str]) -> str:
    """Return the berry from the highest-scoring opponent matchup.

    Args:
        scores_per_opp: {opp_name: matchup score}
        berry_map_per_opp: {opp_name: berry_ps_id}

    Returns:
        PS berry item ID, or DEFAULT_BERRY if no scores available.
    """
    if not scores_per_opp or not berry_map_per_opp:
        return DEFAULT_BERRY
    best_opp = max(scores_per_opp, key=scores_per_opp.__getitem__)
    return berry_map_per_opp.get(best_opp, DEFAULT_BERRY)
