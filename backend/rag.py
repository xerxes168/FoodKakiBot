"""
rag.py
------
Core Retrieval-Augmented Generation (RAG) module for FoodKakiBot.

Pipeline:
  1. Embed the user query using Google text-embedding-004.
  2. Call a Supabase RPC (match_places) that runs pgvector cosine similarity.
  3. Optionally intersect with tag-based hard filters (cuisine/location/budget).
  4. Return ranked candidates with similarity scores for grounded LLM generation.

Requires:
  - pgvector enabled on Supabase (see supabase_migration.sql).
  - Places table populated with embeddings (run embed_places.py once).
"""

from __future__ import annotations

import os
import re
import json
import time
import logging
from typing import Any, Optional

import requests as _http
import google.generativeai as genai
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM   = 3072
RETRIEVAL_TOP_K = 15
MIN_SIMILARITY  = 0.3

GEMINI_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
# genai still used for chat generation only (embeddings go via REST)
genai.configure(api_key=GEMINI_API_KEY)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

_supabase: Optional[Client] = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


# ── Embedding helpers ──────────────────────────────────────────────────────────

def _call_embedding_api(text: str, task_type: str, retries: int = 3) -> list[float]:
    """
    Call the Gemini Embedding REST API directly on the v1 endpoint.
    The google-generativeai SDK < 0.9 routes to v1beta which does not support
    text-embedding-004; using requests bypasses that entirely.
    """
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{EMBEDDING_MODEL}:embedContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "model": f"models/{EMBEDDING_MODEL}",
        "content": {"parts": [{"text": text}]},
        "taskType": task_type,
    }
    for attempt in range(retries):
        try:
            resp = _http.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.json()["embedding"]["values"]
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            logger.warning(
                "Embedding API failed (attempt %d): %s — retrying in %ds",
                attempt + 1, exc, wait,
            )
            time.sleep(wait)
    return []


def embed_document(text: str, retries: int = 3) -> list[float]:
    """Embed a restaurant document (used at indexing time)."""
    return _call_embedding_api(text, "RETRIEVAL_DOCUMENT", retries)


def embed_query(text: str, retries: int = 3) -> list[float]:
    """Embed a user query (asymmetric — different task_type from document)."""
    return _call_embedding_api(text, "RETRIEVAL_QUERY", retries)


# ── Document builder ───────────────────────────────────────────────────────────

def build_place_document(place: dict[str, Any], tags: list[str] | None = None) -> str:
    """
    Construct a rich text document from a place row + its tags.
    This is what gets embedded and stored in Supabase.

    Format is deliberately keyword-rich so cosine similarity
    rewards semantically relevant restaurants.
    """
    parts: list[str] = []

    name = (place.get("name") or "").strip()
    if name:
        parts.append(f"Restaurant: {name}")

    address = (place.get("address") or "").strip()
    if address:
        parts.append(f"Location: {address}")

    summary = (place.get("editorial_summary") or "").strip()
    if summary:
        parts.append(f"About: {summary}")

    if tags:
        parts.append(f"Tags: {', '.join(tags)}")

    # Supabase may store types as a JSON list string or actual list
    raw_types = place.get("types")
    if isinstance(raw_types, str):
        try:
            raw_types = json.loads(raw_types)
        except Exception:
            raw_types = []
    if isinstance(raw_types, list) and raw_types:
        parts.append(f"Google types: {', '.join(str(t) for t in raw_types)}")

    rating = place.get("rating")
    if rating is not None:
        parts.append(f"Rating: {rating}/5")

    price_level = place.get("price_level")
    if price_level is not None:
        price_label = {0: "Free", 1: "Budget", 2: "Mid-Range", 3: "Expensive", 4: "Premium"}.get(int(price_level), "Unknown")
        parts.append(f"Price range: {price_label}")

    return "\n".join(parts)


# ── Vector retrieval ───────────────────────────────────────────────────────────

def retrieve_by_vector(
    query: str,
    limit: int = RETRIEVAL_TOP_K,
    required_tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Run cosine-similarity search against the places embedding column (optionally with an ALL-tags hard filter).

    Args:
        query:         Raw user message / search intent string.
        limit:         Max number of candidates to return.
        required_tags: Optional list of tag names. If given, only places
                       that have ALL of these tags are returned (hard filter).

    Returns:
        List of place dicts, each enriched with a 'similarity' float.
        Returns [] if pgvector / RPC is unavailable.
    """
    try:
        query_vec = embed_query(query)
    except Exception as exc:
        logger.error("Failed to embed query: %s", exc)
        return []

    supabase = get_supabase()

    try:
        params: dict[str, Any] = {
            "query_embedding": query_vec,
            "match_count": limit,
        }
        if required_tags:
            params["filter_tags"] = required_tags
            rpc_name = "match_places_with_tags"
        else:
            rpc_name = "match_places"

        result = supabase.rpc(rpc_name, params).execute()
        candidates = result.data or []

        # Filter out very low-similarity results
        candidates = [c for c in candidates if (c.get("similarity") or 0) >= MIN_SIMILARITY]

        # Sort by similarity descending (RPC should do this, but be safe)
        candidates.sort(key=lambda x: x.get("similarity", 0), reverse=True)

        logger.info("Vector search returned %d candidates for query: %s", len(candidates), query[:80])
        return candidates

    except Exception as exc:
        logger.warning("Vector search RPC failed: %s", exc)
        return []


# ── Tag retrieval (fallback) ───────────────────────────────────────────────────

def retrieve_by_tags(required_tags: list[str], limit: int = 10) -> list[dict[str, Any]]:
    """
    Pure tag-intersection retrieval — used as fallback when embeddings
    are not yet populated, or to supplement vector results.
    """
    if not required_tags:
        return []

    supabase = get_supabase()

    try:
        place_id_sets: list[set] = []
        for tag in required_tags:
            res = supabase.table("place_tags").select(
                "place_id, tags!inner(name)"
            ).eq("tags.name", tag).execute()
            place_id_sets.append({r["place_id"] for r in (res.data or [])})

        if not place_id_sets:
            return []

        valid_ids = set.intersection(*place_id_sets)
        if not valid_ids:
            return []

        res = supabase.table("places").select(
        "id, name, address, gmaps_uri, editorial_summary, rating, opening_hours, price_level, photo_url, latitude, longitude"
        ).in_("id", list(valid_ids)).limit(limit).execute()

        return res.data or []
    except Exception as exc:
        logger.error("Tag retrieval failed: %s", exc)
        return []


# ── Hybrid retrieval (vector + tag filter) ─────────────────────────────────────

def retrieve_hybrid(
    query: str,
    required_tags: list[str] | None = None,
    limit: int = RETRIEVAL_TOP_K,
    *,
    location_tags: list[str] | None = None,
    budget_tags: list[str] | None = None,
    cuisine_tags: list[str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """
    Best-effort retrieval strategy.

    Tag logic options:
      A) Legacy mode (backwards compatible):
         - If `required_tags` is provided and `location_tags` is None, retrieval uses
           an ALL-tags hard filter (location + budget + etc.) exactly as before.

      B) New paired-location mode:
         - If `location_tags` is provided, then at least ONE location tag is required,
           and it must be paired with EITHER a budget tag OR a cuisine tag.
           (location AND (budget OR cuisine))

    Retrieval order:
      1) Try vector search first (with tag filters applied per mode).
      2) If vector yields nothing, fall back to tag-only intersection (per mode).

    Returns:
      (candidates, strategy_used)
    """

    # ── Mode B: location required + (budget OR cuisine) ───────────────────
    if location_tags is not None:
        locs = [t for t in (location_tags or []) if t]
        budgets = [t for t in (budget_tags or []) if t]
        cuisines = [t for t in (cuisine_tags or []) if t]

        # Enforce: location required AND must be paired with either budget or cuisine
        if not locs or (not budgets and not cuisines):
            return [], "none"

        tag_sets: list[list[str]] = []
        # Build (location + budget) tag sets
        for loc in locs:
            for b in budgets:
                tag_sets.append([loc, b])
            for c in cuisines:
                tag_sets.append([loc, c])

        # 1) Vector searches for each tag-set, then merge + rank
        merged: dict[Any, dict[str, Any]] = {}
        for ts in tag_sets:
            vec_results = retrieve_by_vector(query, limit=limit, required_tags=ts)
            for r in vec_results:
                pid = r.get("id")
                if pid is None:
                    continue
                # Keep the best similarity if duplicates appear across tag-sets
                prev = merged.get(pid)
                if prev is None or (r.get("similarity") or 0) > (prev.get("similarity") or 0):
                    merged[pid] = r

        if merged:
            results = sorted(merged.values(), key=lambda x: x.get("similarity", 0), reverse=True)
            return results[:limit], "vector"

        # 2) Fallback tag-only for each tag-set, then merge
        merged_tags: dict[Any, dict[str, Any]] = {}
        for ts in tag_sets:
            tag_results = retrieve_by_tags(ts, limit=limit)
            for r in tag_results:
                pid = r.get("id")
                if pid is None:
                    continue
                r.setdefault("similarity", None)
                merged_tags[pid] = r

        if merged_tags:
            return list(merged_tags.values())[:limit], "tags"

        return [], "none"

    # ── Mode A: legacy ALL-tags hard filter (previous behaviour) ─────────
    vec_results = retrieve_by_vector(query, limit=limit, required_tags=required_tags)
    if vec_results:
        return vec_results, "vector"

    if required_tags:
        tag_results = retrieve_by_tags(required_tags, limit=limit)
        if tag_results:
            for r in tag_results:
                r.setdefault("similarity", None)
            return tag_results, "tags"

    return [], "none"


# ── Context builder for LLM ────────────────────────────────────────────────────

def build_rag_context(candidates: list[dict[str, Any]], tags_map: dict[int, list[str]] | None = None) -> str:
    """
    Serialise retrieved candidates into a context block for the LLM.
    Each restaurant is clearly numbered and delimited.
    """
    if not candidates:
        return "No restaurants found in the database matching this query."

    lines: list[str] = ["=== RETRIEVED RESTAURANTS FROM DATABASE ===\n"]
    for i, place in enumerate(candidates, 1):
        pid  = place.get("id")
        name = place.get("name", "Unknown")
        addr = place.get("address", "N/A")
        uri  = place.get("gmaps_uri") or place.get("gmaps_uri", "N/A")
        sim  = place.get("similarity")
        summ = (place.get("editorial_summary") or "").strip()
        rating = place.get("rating")
        price  = place.get("price_level")

        price_label = {0: "Free", 1: "Budget ($)", 2: "Mid-Range ($$)", 3: "Expensive ($$$)", 4: "Premium ($$$$)"}.get(
            int(price) if price is not None else -1, "N/A"
        )

        tags = (tags_map or {}).get(pid, [])

        block = [f"[{i}] {name}"]
        block.append(f"    Address : {addr}")
        if rating:
            block.append(f"    Rating  : {rating}/5")
        block.append(f"    Price   : {price_label}")
        dist = place.get("distance_km")
        if dist is not None:
            block.append(f"    Distance: {dist} km")
        if tags:
            block.append(f"    Tags    : {', '.join(tags)}")
        if summ:
            block.append(f"    About   : {summ}")
        block.append(f"    Maps URL: {uri}")
        if sim is not None:
            block.append(f"    Relevance score: {sim:.2f}")
        lines.append("\n".join(block))

    lines.append("\n=== END OF DATABASE RESULTS ===")
    return "\n\n".join(lines)


# ── Grounded generation prompt ─────────────────────────────────────────────────

GROUNDED_SYSTEM_PROMPT = """You are FoodKakiBot, a helpful food recommendation assistant for Singapore.

CRITICAL RULES — follow these exactly:
1. You MUST only recommend restaurants that appear in the "RETRIEVED RESTAURANTS FROM DATABASE" section.
2. NEVER invent, fabricate, or hallucinate restaurant names, addresses, or details.
3. If the retrieved restaurants do not match the user's request, say so honestly. Do NOT suggest alternatives from your training knowledge.
4. Use the exact name, address, and Maps URL from the database entry.
5. Keep recommendations concise: name, description of the food sold there, why it fits, price range, address, and Maps URL.
6. If fewer than 3 restaurants are found, recommend only what is available.
7. If 0 restaurants are found, politely say so and suggest the user refine their search.
8. If Distance is present in the database context, mention it and prioritize nearer restaurants.

You may use your conversational ability to explain WHY each restaurant fits the request, but all factual details must come only from the database context provided.
"""


def generate_grounded_response(
    user_message: str,
    context: str,
    conversation_history: list[dict[str, str]] | None = None,
    model: Any = None,
) -> str:
    """
    Generate a response grounded strictly in the retrieved context.

    Args:
        user_message:          The user's current query.
        context:               Output of build_rag_context().
        conversation_history:  Previous turns (list of {role, content}).
        model:                 A configured Gemini GenerativeModel instance.

    Returns:
        LLM response string, guaranteed to only reference context data.
    """
    if model is None:
        model = genai.GenerativeModel("gemini-2.5-flash-lite")

    # Build the full prompt
    history_text = ""
    if conversation_history:
        for turn in conversation_history[-20:]:  # keep up to 20 turns for session memory
            role = "User" if turn["role"] == "user" else "Assistant"
            history_text += f"{role}: {turn['content']}\n"

    prompt = f"""{GROUNDED_SYSTEM_PROMPT}

--- CONVERSATION HISTORY ---
{history_text.strip() or "(No prior conversation)"}

--- DATABASE CONTEXT ---
{context}

--- USER REQUEST ---
{user_message}

--- YOUR RESPONSE ---
Recommend restaurants from the database context above. Remember: only use information from the database context."""

    try:
        response = model.generate_content(prompt)
        return (response.text or "").strip()
    except Exception as exc:
        logger.error("LLM generation failed: %s", exc)
        return "Sorry, I encountered an error generating a response. Please try again."


# ── Tag-map helper (used by app.py for context enrichment) ────────────────────

def fetch_place_tags_map_rag(place_ids: list[int], supabase_client=None) -> dict[int, list[str]]:
    """
    Fetch tag names for a list of place IDs.
    Returns {place_id: [tag_name, ...]}
    """
    if not place_ids:
        return {}
    client = supabase_client or get_supabase()
    try:
        # place_tags has (place_id, tag_id) — join to tags to get the name
        res = client.table("place_tags").select("place_id, tags(name)").in_("place_id", place_ids).execute()
        tag_map: dict[int, list[str]] = {}
        for row in (res.data or []):
            pid     = row.get("place_id")
            tag_obj = row.get("tags")  # nested: {"name": "Japanese"}
            name    = (tag_obj or {}).get("name") if isinstance(tag_obj, dict) else None
            if pid is not None and name:
                tag_map.setdefault(pid, []).append(name)
        return {pid: sorted(set(tags)) for pid, tags in tag_map.items()}
    except Exception as exc:
        logger.error("fetch_place_tags_map_rag failed: %s", exc)
        return {}