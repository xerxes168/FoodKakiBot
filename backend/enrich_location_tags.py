"""
enrich_location_tags.py
-----------------------
Enriches every place in Supabase with location tags derived from:

  1. Address string parsing → planning area (from known area keywords / postal code)
  2. MRT proximity          → "Near X MRT" tag for stations within --mrt-radius metres
  3. OneMap reverse geocode → authoritative planning area (when --use-onemap is set)

Run:
    python enrich_location_tags.py --dry-run           # preview only
    python enrich_location_tags.py --apply             # write to Supabase
    python enrich_location_tags.py --apply --limit 20  # test on 20 places first
    python enrich_location_tags.py --apply --mrt-radius 800   # 800 m walk radius
    python enrich_location_tags.py --apply --use-onemap       # authoritative geocode

Requires:
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY) in backend/.env
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Any

from dotenv import load_dotenv
from supabase import create_client

from location_data import (
    MRT_STATIONS,
    AREA_ALIAS_TO_PLANNING_AREA,
    get_planning_area_from_postal,
)

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_ANON_KEY"]

DEFAULT_MRT_RADIUS_M = 600   # metres — a comfortable walk
ONEMAP_DELAY         = 0.15  # seconds between OneMap requests


# ── Haversine ─────────────────────────────────────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Address parsing ───────────────────────────────────────────────────────────

_POSTAL_RE = re.compile(r"\b(S|s)?(\d{6})\b")


def _extract_postal_code(address: str) -> str | None:
    m = _POSTAL_RE.search(address or "")
    return m.group(2) if m else None


def area_from_address(address: str) -> str | None:
    """
    Try to extract a planning area from an address string by:
    1. Keyword scan (longest match first)
    2. Postal code → district → planning area
    """
    if not address:
        return None

    norm = address.lower()

    # Longest-phrase-first keyword scan
    for phrase in sorted(AREA_ALIAS_TO_PLANNING_AREA, key=len, reverse=True):
        if phrase in norm:
            return AREA_ALIAS_TO_PLANNING_AREA[phrase]

    # Postal code fallback
    postal = _extract_postal_code(address)
    if postal:
        return get_planning_area_from_postal(postal)

    return None


# ── OneMap reverse geocode ────────────────────────────────────────────────────

_ONEMAP_TOKEN: str | None = os.environ.get("ONEMAP_TOKEN")


def _get_onemap_token() -> str | None:
    return _ONEMAP_TOKEN


def onemap_planning_area(lat: float, lng: float) -> str | None:
    """Query OneMap reverse-geocode to get planning area name."""
    token = _get_onemap_token()
    try:
        params = urllib.parse.urlencode({"location": f"{lat},{lng}"})
        url = f"https://www.onemap.gov.sg/api/public/revgeocodexy?{params}"
        req = urllib.request.Request(url, headers={"Authorization": token or ""})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())

        # The endpoint returns { "GeocodeInfo": [{ "BUILDINGNAME": ..., "BLOCK": ..., "ROAD": ..., ... }] }
        info_list = payload.get("GeocodeInfo") or []
        if info_list:
            # Try to extract planning area from POSTALCODE
            postal = str(info_list[0].get("POSTALCODE") or "")
            if postal and len(postal) >= 2:
                area = get_planning_area_from_postal(postal)
                if area:
                    return area
        return None
    except Exception as e:
        print(f"  [warn] OneMap reverse geocode failed ({lat},{lng}): {e}")
        return None


# ── MRT proximity ─────────────────────────────────────────────────────────────

def nearby_mrt_tags(lat: float, lng: float, radius_m: float) -> list[str]:
    """Return station name tags for all MRT stations within radius_m metres."""
    tags = []
    for name, slat, slng in MRT_STATIONS:
        dist = haversine_m(lat, lng, slat, slng)
        if dist <= radius_m:
            tags.append(f"Near {name} MRT")
    return tags


# ── Supabase helpers ──────────────────────────────────────────────────────────

def fetch_all_places(supabase) -> list[dict[str, Any]]:
    rows: list[dict] = []
    offset = 0
    PAGE = 1000
    while True:
        res = (
            supabase.table("places")
            .select("id, name, address, latitude, longitude")
            .range(offset, offset + PAGE - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


def fetch_tags_map(supabase) -> dict[str, int]:
    """Return {tag_name_lower: tag_id}."""
    res = supabase.table("tags").select("id, name").execute()
    return {row["name"].lower(): row["id"] for row in (res.data or [])}


def fetch_existing_links(supabase, place_ids: list[int]) -> set[tuple[int, int]]:
    """Return set of (place_id, tag_id) already in place_tags."""
    pairs: set[tuple[int, int]] = set()
    for i in range(0, len(place_ids), 500):
        batch = place_ids[i : i + 500]
        res = supabase.table("place_tags").select("place_id, tag_id").in_("place_id", batch).execute()
        for row in res.data or []:
            pairs.add((row["place_id"], row["tag_id"]))
    return pairs


def ensure_tag(supabase, name: str, tags_map: dict[str, int], apply: bool) -> int | None:
    """Return the tag_id for `name`, creating it if necessary (when apply=True)."""
    key = name.lower()
    if key in tags_map:
        return tags_map[key]
    if not apply:
        # Assign a fake negative ID for dry-run counting
        fake = -(len(tags_map) + 1)
        tags_map[key] = fake
        return fake
    try:
        res = supabase.table("tags").insert({"name": name}).execute()
        new_id = (res.data or [{}])[0].get("id")
        if new_id:
            tags_map[key] = new_id
            return new_id
    except Exception:
        pass
    # Maybe it was inserted by another process — re-fetch
    res2 = supabase.table("tags").select("id").ilike("name", name).limit(1).execute()
    row = (res2.data or [None])[0]
    if row and row.get("id"):
        tags_map[key] = row["id"]
        return row["id"]
    return None


def insert_links(supabase, links: list[dict], apply: bool) -> int:
    if not apply or not links:
        return 0
    inserted = 0
    for i in range(0, len(links), 250):
        batch = links[i : i + 250]
        try:
            supabase.table("place_tags").insert(batch).execute()
            inserted += len(batch)
        except Exception:
            for row in batch:
                try:
                    supabase.table("place_tags").insert(row).execute()
                    inserted += 1
                except Exception:
                    pass
    return inserted


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich Supabase places with location tags")
    parser.add_argument("--apply",       action="store_true", help="Write changes to Supabase (default: dry-run)")
    parser.add_argument("--limit",       type=int, default=0,   help="Process only first N places (0=all)")
    parser.add_argument("--mrt-radius",  type=int, default=DEFAULT_MRT_RADIUS_M, help="MRT proximity radius in metres")
    parser.add_argument("--use-onemap",  action="store_true", help="Use OneMap reverse geocode for planning area")
    parser.add_argument("--onemap-token", type=str, default=None,  help="OneMap API token (overrides ONEMAP_TOKEN env var)")
    parser.add_argument("--skip-mrt",    action="store_true", help="Skip MRT proximity tagging")
    parser.add_argument("--skip-area",   action="store_true", help="Skip planning area tagging")
    args = parser.parse_args()

    # Allow token via CLI flag or env var
    global _ONEMAP_TOKEN
    if args.onemap_token:
        _ONEMAP_TOKEN = args.onemap_token

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Loading places…")
    places = fetch_all_places(supabase)
    if args.limit > 0:
        places = places[: args.limit]
    print(f"  {len(places)} place(s) to process")

    print("Loading existing tags…")
    tags_map = fetch_tags_map(supabase)
    print(f"  {len(tags_map)} existing tag(s)")

    print("Loading existing place_tag links…")
    place_ids = [p["id"] for p in places if p.get("id") is not None]
    existing_links = fetch_existing_links(supabase, place_ids)
    print(f"  {len(existing_links)} existing link(s)")

    new_tags_count  = 0
    new_links_total = 0
    links_to_insert: list[dict] = []

    for idx, place in enumerate(places, 1):
        pid  = place.get("id")
        name = place.get("name", "")
        addr = place.get("address", "")
        lat  = place.get("latitude")
        lng  = place.get("longitude")

        proposed: list[str] = []

        # ── Planning area ──────────────────────────────────────────────────
        if not args.skip_area:
            area = None

            if args.use_onemap and lat and lng:
                area = onemap_planning_area(float(lat), float(lng))
                time.sleep(ONEMAP_DELAY)

            if not area:
                area = area_from_address(addr)

            if area:
                proposed.append(area)

        # ── MRT proximity ──────────────────────────────────────────────────
        if not args.skip_mrt and lat and lng:
            mrt_tags = nearby_mrt_tags(float(lat), float(lng), float(args.mrt_radius))
            proposed.extend(mrt_tags)

        if not proposed:
            print(f"  [{idx}/{len(places)}] {name} — no tags derived")
            continue

        # Deduplicate (case-insensitive)
        seen: set[str] = set()
        deduped: list[str] = []
        for t in proposed:
            if t.lower() not in seen:
                seen.add(t.lower())
                deduped.append(t)

        # Ensure tags exist and collect new links
        new_for_place = 0
        for tag_name in deduped:
            before = len(tags_map)
            tag_id = ensure_tag(supabase, tag_name, tags_map, apply=args.apply)
            if tag_id is None:
                print(f"    [warn] Could not ensure tag '{tag_name}'")
                continue
            if len(tags_map) > before:
                new_tags_count += 1

            if pid is not None:
                pair = (pid, tag_id)
                if pair not in existing_links:
                    existing_links.add(pair)
                    links_to_insert.append({"place_id": pid, "tag_id": tag_id})
                    new_links_total += 1
                    new_for_place   += 1

        mode = "APPLY" if args.apply else "DRY-RUN"
        print(
            f"  [{idx}/{len(places)}] {name[:40]:<40} | "
            f"tags: {', '.join(deduped[:5])}{'…' if len(deduped) > 5 else ''} | "
            f"new links: {new_for_place}"
        )

    # Flush inserts
    if links_to_insert:
        inserted = insert_links(supabase, links_to_insert, apply=args.apply)
        print(f"\nInserted {inserted} link(s) into place_tags" if args.apply else f"\n(Dry-run) Would insert {len(links_to_insert)} link(s)")

    print("\n── Summary ─────────────────────────────────────────────────")
    print(f"  Mode              : {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"  Places processed  : {len(places)}")
    print(f"  New tags planned  : {new_tags_count}")
    print(f"  New links planned : {new_links_total}")
    print(f"  MRT radius        : {args.mrt_radius} m")
    print(f"  OneMap geocode    : {args.use_onemap}")
    if not args.apply:
        print("\n  Run with --apply to write changes.")


if __name__ == "__main__":
    main()