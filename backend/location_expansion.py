"""
location_expansion.py
---------------------
Drop-in helper for app.py.

Converts a single location tag string into an expanded list that includes
the tag itself plus geographically adjacent planning areas / MRT-based tags.

Import in app.py:
    from location_expansion import expand_location_tags
"""

from __future__ import annotations

import re
from location_data import AREA_ALIAS_TO_PLANNING_AREA, PLANNING_AREA_NEIGHBORS


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _canonical_planning_area(tag: str) -> str | None:
    """
    Map a raw location tag (from LLM extraction or user text) to a canonical
    planning area name. Returns None if the tag looks like an MRT proximity
    tag ("Near X MRT") — those are kept as-is.
    """
    norm = _normalize(tag)

    # MRT tags pass through unchanged
    if norm.startswith("near ") and "mrt" in norm:
        return None

    # Direct match against planning area keys
    for pa in PLANNING_AREA_NEIGHBORS:
        if _normalize(pa) == norm:
            return pa

    # Alias lookup
    return AREA_ALIAS_TO_PLANNING_AREA.get(norm)


def expand_location_tags(
    location_tag: str,
    *,
    include_neighbors: bool = True,
    max_neighbors: int = 4,
) -> list[str]:
    """
    Given a location tag (planning area name, alias, or MRT name), return
    an ordered list of tags for retrieval:

      [original_or_canonical, neighbor1, neighbor2, …]

    MRT-proximity tags ("Near Tampines MRT") are returned as a singleton —
    they can't be meaningfully expanded.

    Args:
        location_tag:      The location string from the resolved session tags.
        include_neighbors: Whether to add adjacent planning areas.
        max_neighbors:     Cap on how many neighbors to include.

    Returns:
        List of distinct tag strings (most-specific first).
    """
    if not location_tag:
        return []

    # Preserve MRT tags verbatim
    norm = _normalize(location_tag)
    if norm.startswith("near ") and "mrt" in norm:
        return [location_tag]

    canonical = _canonical_planning_area(location_tag)
    if not canonical:
        # Unknown location — return as-is, let retrieval do its best
        return [location_tag]

    result: list[str] = [canonical]

    if include_neighbors:
        neighbors = PLANNING_AREA_NEIGHBORS.get(canonical, [])
        result.extend(neighbors[:max_neighbors])

    # Deduplicate, order-preserving
    seen: set[str] = set()
    deduped: list[str] = []
    for t in result:
        if t not in seen:
            seen.add(t)
            deduped.append(t)

    return deduped


def build_location_tag_sets(
    location_tags: list[str],
    budget_tags: list[str],
    cuisine_tags: list[str],
    *,
    max_neighbors: int = 3,
) -> list[list[str]]:
    """
    Build the Cartesian-product of expanded location × (budget OR cuisine) pairs
    for retrieve_hybrid().

    Each returned list is a [location, budget_or_cuisine] pair — the same
    structure that retrieve_hybrid() in rag.py expects.

    Example:
        location_tags = ["Tampines"]  (expands to ["Tampines", "Pasir Ris", "Simei"])
        budget_tags   = ["Budget"]
        cuisine_tags  = []
        → [["Tampines", "Budget"], ["Pasir Ris", "Budget"], ["Simei", "Budget"]]
    """
    if not location_tags:
        return []

    expanded_locs: list[str] = []
    for lt in location_tags:
        for tag in expand_location_tags(lt, max_neighbors=max_neighbors):
            if tag not in expanded_locs:
                expanded_locs.append(tag)

    qualifiers = budget_tags + cuisine_tags
    if not qualifiers:
        return [[loc] for loc in expanded_locs]

    tag_sets: list[list[str]] = []
    for loc in expanded_locs:
        for q in qualifiers:
            tag_sets.append([loc, q])
    return tag_sets
