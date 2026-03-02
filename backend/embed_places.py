"""
embed_places.py
---------------
One-time (and incremental) script to generate text embeddings for every place
in the Supabase `places` table and store them back in the `embedding` column.

Run this AFTER running the SQL migration (supabase_migration.sql).

Usage:
    python embed_places.py                  # embed all un-embedded places
    python embed_places.py --limit 50       # first 50 only (test run)
    python embed_places.py --force-all      # re-embed everything
    python embed_places.py --batch-size 20  # control API throughput
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from typing import Any

from dotenv import load_dotenv
from supabase import create_client

# Import our shared helpers
from rag import build_place_document, embed_document, EMBEDDING_DIM

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_ANON_KEY"]

PAGE_SIZE     = 1_000   # rows per Supabase SELECT page
EMBED_DELAY   = 0.05    # seconds between API calls (rate-limit buffer)


def fetch_place_tags(supabase, place_ids: list[int]) -> dict[int, list[str]]:
    """Fetch tag names for a batch of place IDs by joining through the tags table."""
    if not place_ids:
        return {}
    # place_tags has (place_id, tag_id) — join to tags to get the name
    res = supabase.table("place_tags").select("place_id, tags(name)").in_("place_id", place_ids).execute()
    tag_map: dict[int, list[str]] = {}
    for row in (res.data or []):
        pid = row.get("place_id")
        tag_obj = row.get("tags")  # nested object from the join: {"name": "Japanese"}
        tag = (tag_obj or {}).get("name") if isinstance(tag_obj, dict) else None
        if pid and tag:
            tag_map.setdefault(pid, []).append(tag)
    return tag_map


def fetch_places(supabase, force_all: bool, limit: int | None) -> list[dict[str, Any]]:
    """Fetch places that need embedding."""
    all_rows: list[dict[str, Any]] = []
    offset = 0

    while True:
        q = supabase.table("places").select(
            "id, name, address, editorial_summary, types, rating, price_level, gmaps_uri"
        )
        if not force_all:
            # Only fetch rows where embedding IS NULL
            q = q.is_("embedding", "null")

        q = q.range(offset, offset + PAGE_SIZE - 1)
        res = q.execute()
        rows = res.data or []
        all_rows.extend(rows)

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    if limit:
        all_rows = all_rows[:limit]

    return all_rows


def embed_and_store(
    supabase,
    places: list[dict[str, Any]],
    batch_size: int = 10,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Embed each place and upsert the vector back into Supabase.

    Returns a summary dict: {succeeded, failed, skipped}
    """
    stats = {"succeeded": 0, "failed": 0, "skipped": 0}
    total = len(places)

    # Fetch tags for all places in one query
    all_ids = [p["id"] for p in places if p.get("id") is not None]
    tags_map = fetch_place_tags(supabase, all_ids)

    for i, place in enumerate(places, 1):
        pid = place.get("id")
        name = place.get("name", "?")

        if pid is None:
            stats["skipped"] += 1
            continue

        tags = tags_map.get(pid, [])
        doc  = build_place_document(place, tags)

        if not doc.strip():
            logger.warning("[%d/%d] Skipping place %s (id=%s) — empty document", i, total, name, pid)
            stats["skipped"] += 1
            continue

        logger.info("[%d/%d] Embedding: %s (id=%s)", i, total, name, pid)

        if dry_run:
            logger.info("  DRY RUN — document preview:\n%s\n", doc[:300])
            stats["succeeded"] += 1
            continue

        try:
            vector = embed_document(doc)

            if len(vector) != EMBEDDING_DIM:
                raise ValueError(f"Unexpected embedding dimension: {len(vector)} (expected {EMBEDDING_DIM})")

            supabase.table("places").update({"embedding": vector}).eq("id", pid).execute()
            stats["succeeded"] += 1

        except Exception as exc:
            logger.error("  FAILED for id=%s: %s", pid, exc)
            stats["failed"] += 1

        # Rate-limit buffer between API calls
        if EMBED_DELAY > 0:
            time.sleep(EMBED_DELAY)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed places for FoodKakiBot RAG")
    parser.add_argument("--limit",      type=int,  default=0,     help="Max places to process (0 = all)")
    parser.add_argument("--batch-size", type=int,  default=10,    help="Update batch size (currently per-row)")
    parser.add_argument("--force-all",  action="store_true",       help="Re-embed even already-embedded places")
    parser.add_argument("--dry-run",    action="store_true",       help="Show document text without writing to DB")
    args = parser.parse_args()

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    logger.info("Fetching places from Supabase…")
    places = fetch_places(supabase, force_all=args.force_all, limit=args.limit or None)
    logger.info("Found %d place(s) to embed.", len(places))

    if not places:
        logger.info("Nothing to do. All places may already be embedded (use --force-all to re-embed).")
        return

    stats = embed_and_store(
        supabase,
        places,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )

    print("\n── Summary ──────────────────────────────")
    print(f"  Succeeded : {stats['succeeded']}")
    print(f"  Failed    : {stats['failed']}")
    print(f"  Skipped   : {stats['skipped']}")
    print("─────────────────────────────────────────")

    if stats["failed"] > 0:
        print(f"\nTip: Re-run the script to retry the {stats['failed']} failed place(s).")


if __name__ == "__main__":
    main()