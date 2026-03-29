"""
fetch_place_details.py
----------------------
Fetches editorial_summary, opening_hours, and photo_url for places in Supabase
using the Google Places API, then writes the results back in one pass.

Pre-requisites:
  1. Add columns to your `places` table:
       ALTER TABLE places ADD COLUMN IF NOT EXISTS photo_reference TEXT;
       ALTER TABLE places ADD COLUMN IF NOT EXISTS photo_url TEXT;
       ALTER TABLE places ADD COLUMN IF NOT EXISTS editorial_summary TEXT;
       ALTER TABLE places ADD COLUMN IF NOT EXISTS opening_hours JSONB;
  2. Create a public Supabase Storage bucket for place photos.
     Default bucket name: restaurants-photos

Usage:
  python fetch_place_details.py                  # fill any missing photo/details
  python fetch_place_details.py --limit 10       # process first 10 matching places
  python fetch_place_details.py --skip-photos    # only fetch summary + hours
  python fetch_place_details.py --skip-details   # only fetch + upload photos
  python fetch_place_details.py --force          # refresh all requested fields
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Any

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
PLACES_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
BUCKET = os.getenv("SUPABASE_PHOTO_BUCKET", "restaurants-photos")

TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
PHOTO_URL = "https://maps.googleapis.com/maps/api/place/photo"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def require_env(name: str, value: str | None) -> str:
    if value:
        return value
    raise RuntimeError(f"Missing required environment variable: {name}")


def needs_photo(place: dict[str, Any], *, force: bool, skip_photos: bool) -> bool:
    return not skip_photos and (force or not place.get("photo_url"))


def needs_summary(place: dict[str, Any], *, force: bool, skip_details: bool) -> bool:
    return not skip_details and (force or not place.get("editorial_summary"))


def needs_hours(place: dict[str, Any], *, force: bool, skip_details: bool) -> bool:
    return not skip_details and (force or not place.get("opening_hours"))


def should_process(place: dict[str, Any], *, force: bool, skip_photos: bool, skip_details: bool) -> bool:
    return any(
        (
            needs_photo(place, force=force, skip_photos=skip_photos),
            needs_summary(place, force=force, skip_details=skip_details),
            needs_hours(place, force=force, skip_details=skip_details),
        )
    )


def search_place_id(name: str, address: str) -> str | None:
    query = " ".join(part.strip() for part in (name, address) if part and part.strip())
    resp = requests.get(TEXT_SEARCH_URL, params={"query": query, "key": PLACES_KEY}, timeout=20)
    if resp.status_code != 200:
        print(f"    x Text search failed (HTTP {resp.status_code})")
        return None

    data = resp.json()
    if data.get("status") != "OK" or not data.get("results"):
        print(f"    x No text-search result for '{name}': {data.get('status')}")
        return None

    return data["results"][0].get("place_id")


def fetch_place_details(place_id: str) -> dict[str, Any] | None:
    resp = requests.get(
        DETAILS_URL,
        params={
            "place_id": place_id,
            "fields": "editorial_summary,opening_hours,photos",
            "key": PLACES_KEY,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"    x Details fetch failed (HTTP {resp.status_code})")
        return None

    data = resp.json()
    if data.get("status") != "OK":
        print(f"    x Details fetch failed: {data.get('status')}")
        return None

    return data.get("result") or {}


def parse_editorial_summary(details: dict[str, Any]) -> str | None:
    summary = details.get("editorial_summary") or details.get("editorialSummary")
    if isinstance(summary, dict):
        text = summary.get("overview") or summary.get("text") or ""
        text = str(text).strip()
        return text or None
    if isinstance(summary, str):
        summary = summary.strip()
        return summary or None
    return None


def parse_opening_hours(details: dict[str, Any]) -> dict[str, Any] | None:
    hours = details.get("opening_hours") or details.get("regularOpeningHours")
    if not isinstance(hours, dict):
        return None

    weekday_text = hours.get("weekday_text") or hours.get("weekdayDescriptions") or []
    periods = hours.get("periods") or []

    result: dict[str, Any] = {}
    if weekday_text:
        result["weekday_text"] = weekday_text
    if periods:
        result["periods"] = periods
    return result or None


def download_photo(photo_ref: str) -> tuple[bytes, str] | None:
    resp = requests.get(
        PHOTO_URL,
        params={
            "maxwidth": 800,
            "photo_reference": photo_ref,
            "key": PLACES_KEY,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"    x Failed to download image (HTTP {resp.status_code})")
        return None

    content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
    return resp.content, content_type


def upload_photo(content: bytes, content_type: str, place_id: str) -> str | None:
    ext = "png" if "png" in content_type.lower() else "jpg"
    filename = f"{place_id}.{ext}"
    try:
        supabase.storage.from_(BUCKET).upload(
            filename,
            content,
            {"content-type": content_type, "upsert": "true"},
        )
    except Exception as exc:
        print(f"    x Upload failed: {exc}")
        return None
    return supabase.storage.from_(BUCKET).get_public_url(filename)


def main() -> None:
    require_env("SUPABASE_URL", SUPABASE_URL)
    require_env("SUPABASE_SERVICE_ROLE_KEY", SUPABASE_KEY)
    require_env("GOOGLE_PLACES_API_KEY", PLACES_KEY)

    parser = argparse.ArgumentParser(
        description="Fetch place photos, editorial summaries, and opening hours from Google Places."
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Only process the first N matching places (default: all)",
    )
    parser.add_argument(
        "--skip-photos",
        action="store_true",
        help="Do not fetch or upload photos",
    )
    parser.add_argument(
        "--skip-details",
        action="store_true",
        help="Do not fetch editorial_summary or opening_hours",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh requested fields even when they are already populated",
    )
    args = parser.parse_args()

    if args.skip_photos and args.skip_details:
        parser.error("Nothing to do: both --skip-photos and --skip-details were set.")

    print("=" * 60)
    print("FoodKakiBot - Place Details Fetcher")
    print("=" * 60)

    response = supabase.table("places").select(
        "id, name, address, photo_url, editorial_summary, opening_hours"
    ).execute()
    all_places = response.data or []
    print(f"Found {len(all_places)} total places")

    matching = [
        place
        for place in all_places
        if should_process(
            place,
            force=args.force,
            skip_photos=args.skip_photos,
            skip_details=args.skip_details,
        )
    ]
    already = len(all_places) - len(matching)
    to_process = matching[: args.limit] if args.limit is not None else matching

    if args.limit is not None:
        print(
            f"Already complete: {already} | Matching: {len(matching)} | "
            f"Processing: {len(to_process)} (--limit {args.limit})\n"
        )
    else:
        print(f"Already complete: {already} | Matching (to process): {len(to_process)}\n")

    updated_rows = 0
    failed = 0
    no_changes = 0
    photo_updates = 0
    summary_updates = 0
    hours_updates = 0

    for place in to_process:
        name = (place.get("name") or "").strip()
        place_id = str(place.get("id"))
        address = place.get("address") or ""

        want_photo = needs_photo(place, force=args.force, skip_photos=args.skip_photos)
        want_summary = needs_summary(place, force=args.force, skip_details=args.skip_details)
        want_hours = needs_hours(place, force=args.force, skip_details=args.skip_details)

        print(f"  Processing '{name}'...")

        google_place_id = search_place_id(name, address)
        if not google_place_id:
            failed += 1
            time.sleep(0.2)
            continue

        details = fetch_place_details(google_place_id)
        if details is None:
            failed += 1
            time.sleep(0.2)
            continue

        update_payload: dict[str, Any] = {}

        if want_summary:
            summary = parse_editorial_summary(details)
            if summary:
                update_payload["editorial_summary"] = summary
                summary_updates += 1
                print(f"    + Editorial summary: {summary[:60]}...")
            else:
                print("    - No editorial summary available")

        if want_hours:
            hours = parse_opening_hours(details)
            if hours:
                update_payload["opening_hours"] = hours
                hours_updates += 1
                print(f"    + Opening hours: {len(hours.get('weekday_text', []))} day entries")
            else:
                print("    - No opening hours available")

        if want_photo:
            photos = details.get("photos") or []
            if photos and photos[0].get("photo_reference"):
                photo_ref = photos[0]["photo_reference"]
                downloaded = download_photo(photo_ref)
                if downloaded is None:
                    print("    - Photo download failed")
                else:
                    content, content_type = downloaded
                    public_url = upload_photo(content, content_type, place_id)
                    if public_url:
                        update_payload["photo_reference"] = photo_ref
                        update_payload["photo_url"] = public_url
                        photo_updates += 1
                        print(f"    + Photo uploaded: {public_url}")
            else:
                print("    - No photos available")

        if update_payload:
            supabase.table("places").update(update_payload).eq("id", place_id).execute()
            updated_rows += 1
        else:
            no_changes += 1
            print(f"    - Nothing to update for '{name}'")

        time.sleep(0.2)

    print("\n" + "=" * 60)
    print(
        f"Done. Updated rows: {updated_rows} | "
        f"Photo updates: {photo_updates} | "
        f"Summary updates: {summary_updates} | "
        f"Hours updates: {hours_updates} | "
        f"Failed: {failed} | "
        f"No changes: {no_changes} | "
        f"Skipped (already complete): {already}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
