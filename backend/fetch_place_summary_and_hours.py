"""
fetch_place_details.py — Fetches editorial_summary and opening_hours from
Google Places API (New) and updates the Supabase places table.

Pre-requisites:
  1. Add opening_hours column to your places table:
       ALTER TABLE places ADD COLUMN IF NOT EXISTS opening_hours JSONB;
  2. Enable "Places API (New)" in Google Cloud Console.

Usage:
  python fetch_place_details.py              # process ALL places missing details
  python fetch_place_details.py --limit 10   # process only first N places
  python fetch_place_details.py -l 10        # shorthand
"""

import os
import time
import argparse
import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
PLACES_KEY   = os.getenv("GOOGLE_PLACES_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

PLACES_SEARCH_URL  = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"


def search_place_id(name: str, address: str) -> str | None:
    """Search for a place using the New Places API and return its resource name."""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": PLACES_KEY,
        "X-Goog-FieldMask": "places.id,places.displayName",
    }
    payload = {
        "textQuery": f"{name} {address}",
        "locationBias": {
            "circle": {
                "center": {"latitude": 1.3521, "longitude": 103.8198},  # Singapore centre
                "radius": 50000.0,
            }
        },
    }
    resp = requests.post(PLACES_SEARCH_URL, json=payload, headers=headers, timeout=20)
    data = resp.json()

    places = data.get("places", [])
    if not places:
        print(f"    x No results for '{name}'")
        return None
    return places[0]["id"]  # returns the place ID (e.g. ChIJ...)


def fetch_place_details(place_id: str) -> dict | None:
    """Fetch editorial summary and opening hours from the New Places API."""
    url = PLACES_DETAILS_URL.format(place_id=place_id)
    headers = {
        "X-Goog-Api-Key": PLACES_KEY,
        "X-Goog-FieldMask": "editorialSummary,regularOpeningHours",
    }
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code != 200:
        print(f"    x Details fetch failed (HTTP {resp.status_code}): {resp.text[:100]}")
        return None
    return resp.json()


def parse_opening_hours(details: dict) -> dict | None:
    """
    Extract opening hours into a clean structure:
    {
      "weekday_text": ["Monday: 9:00 AM - 10:00 PM", ...],
      "periods": [{"open": {"day": 0, "hour": 9}, "close": {...}}, ...]
    }
    """
    hours = details.get("regularOpeningHours")
    if not hours:
        return None
    return {
        "weekday_text": hours.get("weekdayDescriptions", []),
        "periods": hours.get("periods", []),
    }


def parse_editorial_summary(details: dict) -> str | None:
    """Extract the English editorial summary text."""
    summary = details.get("editorialSummary")
    if not summary:
        return None
    # New API returns {"text": "...", "languageCode": "en"}
    return summary.get("text") or None


def main():
    parser = argparse.ArgumentParser(
        description="Fetch editorial summaries and opening hours from Google Places API (New)."
    )
    parser.add_argument(
        "-l", "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Only process the first N places (default: all)",
    )
    args = parser.parse_args()

    print("=" * 55)
    print("FoodKakiBot - Place Details Fetcher (New API)")
    print("=" * 55)

    # Fetch all places — only process those missing editorial_summary OR opening_hours
    response = supabase.table("places").select("id, name, address, editorial_summary, opening_hours").execute()
    all_places = response.data
    print(f"Found {len(all_places)} total places")

    # Process if missing editorial_summary OR missing opening_hours
    missing = [
        p for p in all_places
        if not p.get("editorial_summary") or not p.get("opening_hours")
    ]
    already = len(all_places) - len(missing)

    if args.limit is not None:
        to_process = missing[:args.limit]
        print(f"Already complete: {already} | Missing details: {len(missing)} | Processing: {len(to_process)} (--limit {args.limit})\n")
    else:
        to_process = missing
        print(f"Already complete: {already} | Missing details (to process): {len(missing)}\n")

    updated = 0
    failed  = 0

    for place in to_process:
        name = place.get("name", "")
        pid  = place.get("id")

        print(f"  Processing '{name}'...")

        # Step 1: Find the Google Place ID
        gplace_id = search_place_id(name, place.get("address", ""))
        if not gplace_id:
            failed += 1
            time.sleep(0.2)
            continue

        # Step 2: Fetch details
        details = fetch_place_details(gplace_id)
        if not details:
            failed += 1
            time.sleep(0.2)
            continue

        # Step 3: Parse fields
        update_payload = {}

        if not place.get("editorial_summary"):
            summary = parse_editorial_summary(details)
            if summary:
                update_payload["editorial_summary"] = summary
                print(f"    + Editorial summary: {summary[:60]}...")
            else:
                print(f"    - No editorial summary available")

        if not place.get("opening_hours"):
            hours = parse_opening_hours(details)
            if hours:
                update_payload["opening_hours"] = hours
                print(f"    + Opening hours: {len(hours.get('weekday_text', []))} days")
            else:
                print(f"    - No opening hours available")

        # Step 4: Update Supabase
        if update_payload:
            supabase.table("places").update(update_payload).eq("id", pid).execute()
            updated += 1
        else:
            print(f"    - Nothing to update for '{name}'")

        # Respect rate limits
        time.sleep(0.2)

    print("\n" + "=" * 55)
    print(f"Done. Updated: {updated} | Failed: {failed} | Skipped (already complete): {already}")
    print("=" * 55)


if __name__ == "__main__":
    main()
