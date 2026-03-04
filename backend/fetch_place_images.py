"""
fetch_place_images.py — Populates photo_url in Supabase by downloading images
from Google Places and uploading them to Supabase Storage.

Pre-requisites:
  1. Add columns to your `places` table:
       ALTER TABLE places ADD COLUMN IF NOT EXISTS photo_reference TEXT;
       ALTER TABLE places ADD COLUMN IF NOT EXISTS photo_url TEXT;
  2. Create a public Supabase Storage bucket named: restaurant-photos
     (Storage > New bucket > name: restaurant-photos > Public: ON)

Usage:
  python fetch_place_images.py              # process ALL places missing a photo
  python fetch_place_images.py --limit 10   # process only the first N missing places
  python fetch_place_images.py -l 10        # shorthand
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
BUCKET       = "restaurants-photos"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_photo_reference(name: str, address: str) -> str | None:
    """Text-search Google Places and return the first photo_reference."""
    search_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    r = requests.get(search_url, params={"query": f"{name} {address}", "key": PLACES_KEY}, timeout=20)
    data = r.json()

    if data.get("status") != "OK" or not data.get("results"):
        print(f"    x Text search failed for '{name}': {data.get('status')}")
        return None

    place_id = data["results"][0]["place_id"]

    details_url = "https://maps.googleapis.com/maps/api/place/details/json"
    dr = requests.get(details_url, params={"place_id": place_id, "fields": "photos", "key": PLACES_KEY}, timeout=20)
    ddata = dr.json()

    if ddata.get("status") != "OK":
        print(f"    x Details failed for '{name}': {ddata.get('status')}")
        return None

    photos = ddata.get("result", {}).get("photos", [])
    if not photos:
        print(f"    x No photos found for '{name}'")
        return None

    return photos[0]["photo_reference"]


def download_and_upload_photo(photo_ref: str, place_id: str) -> str | None:
    """
    Download the image from Google Places and upload it to Supabase Storage.
    Returns the public URL or None on failure.
    """
    # Download image from Google
    google_url = (
        f"https://maps.googleapis.com/maps/api/place/photo"
        f"?maxwidth=800&photo_reference={photo_ref}&key={PLACES_KEY}"
    )
    img_resp = requests.get(google_url, timeout=20)
    if img_resp.status_code != 200:
        print(f"    x Failed to download image (HTTP {img_resp.status_code})")
        return None

    # Detect content type (jpeg or png)
    content_type = img_resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
    ext = "png" if "png" in content_type else "jpg"
    filename = f"{place_id}.{ext}"

    # Upload to Supabase Storage (upsert so re-runs don't fail)
    try:
        supabase.storage.from_(BUCKET).upload(
            filename,
            img_resp.content,
            {"content-type": content_type, "upsert": "true"},
        )
    except Exception as e:
        print(f"    x Upload failed: {e}")
        return None

    # Get public URL (no API call — just constructs the URL)
    public_url = supabase.storage.from_(BUCKET).get_public_url(filename)
    return public_url


def main():
    parser = argparse.ArgumentParser(
        description="Fetch restaurant photos from Google Places and store in Supabase Storage."
    )
    parser.add_argument(
        "-l", "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Only process the first N places missing a photo_url (default: all)",
    )
    args = parser.parse_args()

    print("=" * 55)
    print("FoodKakiBot - Photo Downloader & Uploader")
    print("=" * 55)

    # Fetch all places
    response = supabase.table("places").select("id, name, address, photo_url").execute()
    all_places = response.data
    print(f"Found {len(all_places)} total places")

    # Only process those without a photo_url already
    missing = [p for p in all_places if not p.get("photo_url")]
    already = len(all_places) - len(missing)

    if args.limit is not None:
        to_process = missing[:args.limit]
        print(f"Already have photo: {already} | Missing: {len(missing)} | Processing: {len(to_process)} (--limit {args.limit})\n")
    else:
        to_process = missing
        print(f"Already have photo: {already} | Missing (to process): {len(missing)}\n")

    updated = 0
    failed  = 0

    for place in to_process:
        name = place.get("name", "")
        pid  = str(place.get("id"))

        print(f"  Processing '{name}'...")

        # Step 1: Get photo_reference from Google
        ref = get_photo_reference(name, place.get("address", ""))
        if not ref:
            failed += 1
            continue

        # Step 2: Download image and upload to Supabase Storage
        public_url = download_and_upload_photo(ref, pid)
        if not public_url:
            failed += 1
            continue

        # Step 3: Save public_url (and photo_reference) to Supabase table
        supabase.table("places").update({
            "photo_reference": ref,
            "photo_url": public_url,
        }).eq("id", pid).execute()

        print(f"    + Saved photo for '{name}'")
        print(f"      {public_url}")
        updated += 1

        # Respect Google Places API rate limit
        time.sleep(0.2)

    print("\n" + "=" * 55)
    print(f"Done. Saved: {updated} | Failed: {failed} | Skipped (had photo): {already}")
    print("=" * 55)


if __name__ == "__main__":
    main()