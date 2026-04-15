"""
One-time script: Create the "Wink" tag and assign it to ALL existing places.

Usage:
    python add_wink_tag.py

This is safe to re-run — it skips places that already have the tag.
"""
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_ANON_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

WINK_TAG_NAME = "Wink"
BATCH_SIZE = 250


def main():
    # 1. Ensure the "Wink" tag exists
    existing = supabase.table("tags").select("id, name").eq("name", WINK_TAG_NAME).execute()
    if existing.data:
        wink_tag_id = existing.data[0]["id"]
        print(f"Wink tag already exists (id={wink_tag_id})")
    else:
        res = supabase.table("tags").insert({"name": WINK_TAG_NAME}).execute()
        wink_tag_id = res.data[0]["id"]
        print(f"Created Wink tag (id={wink_tag_id})")

    # 2. Get all place IDs
    all_places = supabase.table("places").select("id").execute()
    all_place_ids = {r["id"] for r in (all_places.data or [])}
    print(f"Total places in database: {len(all_place_ids)}")

    # 3. Find places that already have the Wink tag
    already_tagged = supabase.table("place_tags").select("place_id").eq("tag_id", wink_tag_id).execute()
    already_tagged_ids = {r["place_id"] for r in (already_tagged.data or [])}
    print(f"Already tagged with Wink: {len(already_tagged_ids)}")

    # 4. Insert Wink tag for remaining places
    to_tag = all_place_ids - already_tagged_ids
    if not to_tag:
        print("All places already have the Wink tag. Nothing to do.")
        return

    print(f"Tagging {len(to_tag)} places with Wink...")
    rows = [{"place_id": pid, "tag_id": wink_tag_id} for pid in to_tag]

    inserted = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        try:
            supabase.table("place_tags").insert(batch).execute()
            inserted += len(batch)
            print(f"  Inserted batch {i // BATCH_SIZE + 1} ({len(batch)} rows)")
        except Exception as e:
            print(f"  Batch insert failed, trying one-by-one: {e}")
            for row in batch:
                try:
                    supabase.table("place_tags").insert(row).execute()
                    inserted += 1
                except Exception as e2:
                    print(f"    Skipped place_id={row['place_id']}: {e2}")

    print(f"Done. Tagged {inserted} places with Wink.")


if __name__ == "__main__":
    main()
