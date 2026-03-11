"""
app.py — FoodKakiBot backend
Updated to use a full Retrieval-Augmented Generation (RAG) pipeline.

Chat flow (per request):
  1. Rule-based tag extraction  (cuisine / location / budget)
  2. LLM tag extraction fallback (fills missing slots)
  3. Hybrid retrieval:
       a. Vector similarity search (pgvector, with tag pre-filter if 3 tags present)
       b. Fallback to tag-intersection query if embeddings unavailable
  4. Fetch tags for retrieved candidates (for context richness)
  5. Build grounded context block from candidates
  6. LLM generates a response that is strictly anchored to the context
     (NO hallucination — the prompt forbids inventing restaurants)
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import google.generativeai as genai
from datetime import datetime
import uuid
from supabase import create_client
from dotenv import load_dotenv
import requests
import re
import math
import json
import logging
import urllib.parse

from tagging import auto_tags_from_google

# ── RAG module ────────────────────────────────────────────────────────────────
from rag import (
        retrieve_hybrid,
        retrieve_by_tags,
        build_rag_context,
        generate_grounded_response,
        fetch_place_tags_map_rag,
    )
from location_expansion import expand_location_tags, build_location_tag_sets
from ranking import rank_candidates, detect_allow_closed


# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)
load_dotenv()

# ── Gemini ─────────────────────────────────────────────────────────────────────
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
model = genai.GenerativeModel("gemini-2.5-flash-lite")

# ── Supabase ───────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY")

# ── Google Places API key ──────────────────────────────────────────────────────
# Prefer the dedicated Places key; fall back to the shared Gemini key.
# The Gemini key may NOT have Places API enabled — use GOOGLE_PLACES_API_KEY
# (or GOOGLE_MAPS_API_KEY) in your .env for Places/Maps calls.
PLACES_KEY = (
    os.getenv("GOOGLE_PLACES_API_KEY")
    or os.getenv("GOOGLE_MAPS_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Per-session state ─────────────────────────────────────────────────────────
sessions: dict[str, dict] = {}

def get_session(session_id: str) -> dict:
    if session_id not in sessions:
        sessions[session_id] = {
            "history": [],
            "tags": {"cuisine": None, "location": None, "budget": None},
            "last_candidates": [],
        }
    return sessions[session_id]

# ── Taxonomy constants ────────────────────────────────────────────────────────
PRICE_LEVEL_TO_TAG = {0: "Free", 1: "Budget", 2: "Mid-Range", 3: "Expensive", 4: "Premium"}
PRICE_TAG_TO_LEVEL = {v: k for k, v in PRICE_LEVEL_TO_TAG.items()}

PRICE_TAG_ALIASES = {
    "Free":      ["free"],
    "Budget":    ["cheap", "budget", "affordable", "economical", "low cost", "low-cost"],
    "Mid-Range": ["mid range", "mid-range", "moderate", "reasonably priced", "not too expensive"],
    "Expensive": ["expensive", "pricey", "high price", "high-priced"],
    "Premium":   ["premium", "luxury", "high end", "high-end", "fine dining", "very expensive"],
}

IGNORED_QUERY_TAGS = {"Restaurant"}
BUDGET_TAGS = set(PRICE_LEVEL_TO_TAG.values())

CUISINE_TAGS = {
    "African", "American", "Asian", "Bakery", "Bar", "BBQ", "Brunch", "Bubble Tea",
    "Buffet", "Burgers", "Cafe", "Chinese", "Deli", "Dessert", "Dim Sum", "Diner",
    "Fast Food", "French", "Fusion", "Halal", "Hawaiian", "Hotpot / Steamboat",
    "Ice Cream", "Indian", "Indonesian", "Italian", "Japanese", "Juice", "Juice Bar",
    "Korean", "Mala", "Malay", "Mediterranean", "Mexican", "Middle Eastern",
    "Moroccan", "Pizza", "Ramen", "Salad Shop", "Sandwiches", "Seafood",
    "Singaporean", "Spanish", "Steakhouse", "Sushi", "Taiwanese", "Tea House",
    "Thai", "Vegetarian", "Vietnamese", "Western",
}

NON_LOCATION_TAGS = (
    BUDGET_TAGS | CUISINE_TAGS | {
        "Delivery", "Dine-In", "Takeaway", "Reservable", "Family-Friendly", "Good for Groups",
        "Outdoor Seating", "In Mall", "Food Court", "Live Music", "Museum", "Park",
        "Nightclub", "Indoor Playground", "Playground", "Restaurant",
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Tag utilities
# ─────────────────────────────────────────────────────────────────────────────

def normalize_text_for_match(text: str) -> str:
    text = (text or "").lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9$\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def contains_phrase(normalized_text: str, phrase: str) -> bool:
    p = normalize_text_for_match(phrase)
    return bool(p) and f" {p} " in f" {normalized_text} "


def detect_canonical_price_tag(message: str) -> str | None:
    raw = (message or "").lower()
    normalized = normalize_text_for_match(message or "")
    for symbol, canonical in [("$$$$", "Premium"), ("$$$", "Expensive"), ("$$", "Mid-Range"), ("$", "Budget")]:
        if re.search(rf"(?<!\$){re.escape(symbol)}(?!\$)", raw):
            return canonical
    for canonical, aliases in PRICE_TAG_ALIASES.items():
        if any(contains_phrase(normalized, alias) for alias in aliases):
            return canonical
    return None


def get_all_tag_names():
    response = supabase.table("tags").select("name").execute()
    tags = [t["name"] for t in response.data]
    return tags, {t.lower() for t in tags}


def extract_tags_from_message(user_message):
    tag_names, _ = get_all_tag_names()
    normalized_user_text = normalize_text_for_match(user_message or "")
    tag_lookup = {t.lower(): t for t in tag_names}
    matched_tags = []
    for tag in sorted(tag_names, key=len, reverse=True):
        if tag in IGNORED_QUERY_TAGS:
            continue
        if contains_phrase(normalized_user_text, tag):
            matched_tags.append(tag)
    canonical_price_tag = detect_canonical_price_tag(user_message or "")
    if canonical_price_tag:
        actual_tag = tag_lookup.get(canonical_price_tag.lower())
        if actual_tag and actual_tag not in matched_tags:
            matched_tags.append(actual_tag)
    return matched_tags


def classify_required_tags(matched_tags):
    selected = {"cuisine": None, "location": None, "budget": None}
    for tag in matched_tags:
        if selected["budget"] is None and tag in BUDGET_TAGS:
            selected["budget"] = tag; continue
        if selected["cuisine"] is None and tag in CUISINE_TAGS:
            selected["cuisine"] = tag; continue
        if selected["location"] is None and tag not in NON_LOCATION_TAGS:
            selected["location"] = tag; continue
    return selected


def extract_location_phrase_from_message(user_message):
    text = (user_message or "").strip()
    m = re.search(r"\b(?:in|at|near)\s+([a-zA-Z0-9\s\-]+)", text, flags=re.IGNORECASE)
    if not m:
        return None
    phrase = m.group(1).strip(" .,!?:;")
    phrase = re.split(
        r"\b(?:with|for|under|budget|cheap|affordable|mid-range|mid range|expensive|premium)\b",
        phrase, maxsplit=1, flags=re.IGNORECASE,
    )[0].strip(" .,!?:;")
    return phrase or None


def parse_json_from_llm_text(text):
    text = (text or "").strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    candidate = fence_match.group(1).strip() if fence_match else text
    try:
        return json.loads(candidate)
    except Exception:
        pass
    obj_match = re.search(r"\{[\s\S]*\}", text)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except Exception:
            return None
    return None


def get_location_tags_from_all_tags(tag_names):
    return sorted([t for t in tag_names if t not in NON_LOCATION_TAGS])


def get_tag_catalog():
    tag_names, _ = get_all_tag_names()
    return {
        "all": tag_names,
        "budgets":   sorted(t for t in tag_names if t in BUDGET_TAGS),
        "cuisines":  sorted(t for t in tag_names if t in CUISINE_TAGS),
        "locations": get_location_tags_from_all_tags(tag_names),
    }


def llm_extract_required_tags(user_message, current_selected=None):
    catalog = get_tag_catalog()
    current_selected = current_selected or {"cuisine": None, "location": None, "budget": None}
    prompt = f"""
You map a user food request into EXACT database tags.

Rules:
- Choose at most one tag per category: cuisine, location, budget.
- Output ONLY JSON with keys: cuisine, location, budget.
- Values must be exact strings from the allowed lists, or null.

User message: {user_message}

Rule-based hints: {json.dumps(current_selected)}

Allowed cuisine tags:  {json.dumps(catalog["cuisines"])}
Allowed location tags: {json.dumps(catalog["locations"])}
Allowed budget tags:   {json.dumps(catalog["budgets"])}
""".strip()
    try:
        resp = model.generate_content(prompt)
        payload = parse_json_from_llm_text(getattr(resp, "text", ""))
        if not isinstance(payload, dict):
            return None
        result = {"cuisine": None, "location": None, "budget": None}
        if isinstance(payload.get("cuisine"), str) and payload["cuisine"] in catalog["cuisines"]:
            result["cuisine"] = payload["cuisine"]
        if isinstance(payload.get("location"), str) and payload["location"] in catalog["locations"]:
            result["location"] = payload["location"]
        if isinstance(payload.get("budget"), str) and payload["budget"] in catalog["budgets"]:
            result["budget"] = payload["budget"]
        return result
    except Exception as e:
        logger.warning("LLM tag extraction failed: %s", e)
        return None


def merge_selected_tags(rule_selected, llm_selected):
    merged = dict(rule_selected or {"cuisine": None, "location": None, "budget": None})
    if not llm_selected:
        return merged
    for key in ("cuisine", "location", "budget"):
        if merged.get(key) is None and llm_selected.get(key):
            merged[key] = llm_selected[key]
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Intent classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_message_intent(user_message: str, conversation_history: list, has_last_candidates: bool) -> str:
    """
    Classify the user message into one of three intents:
      - "restaurant_search"  : user wants new restaurant recommendations
      - "followup_review"    : user is asking about quality/reviews/experience of
                               previously shown restaurants
      - "conversational"     : general chat, greetings, capability questions, etc.
    """
    history_text = ""
    if conversation_history:
        for turn in conversation_history[-6:]:
            role = "User" if turn["role"] == "user" else "Assistant"
            history_text += f"{role}: {turn['content']}\n"

    prompt = f"""You are a classifier for a Singapore food recommendation chatbot.

Conversation so far:
{history_text.strip() or "(none)"}

Latest user message: "{user_message}"

Classify the intent as exactly one of:
- "restaurant_search"  : user wants restaurant recommendations (mentions food + location, asks where to eat, etc.)
- "followup_review"    : user is asking about quality, reviews, opinions, atmosphere, or experience of restaurants already recommended in this conversation (e.g. "is the food good?", "what do people think?", "is it worth it?", "how are the reviews?", "is it nice there?")
- "conversational"     : greeting, asking what the bot can do, saying thanks, off-topic, or anything else

{"Note: The bot has previously recommended restaurants in this conversation, so follow-up questions about those places are likely." if has_last_candidates else "Note: No restaurants have been recommended yet, so follow-ups about specific places are unlikely."}

Reply with ONLY one of these exact strings: restaurant_search, followup_review, conversational
""".strip()

    try:
        resp = model.generate_content(prompt)
        answer = (getattr(resp, "text", "") or "").strip().lower()
        if "followup_review" in answer or "follow" in answer:
            return "followup_review"
        if "restaurant_search" in answer or "search" in answer:
            return "restaurant_search"
        if "conversational" in answer:
            return "conversational"
        # Keyword fallback
        review_keywords = [
            "good", "worth", "nice", "review", "opinion", "people say", "popular",
            "recommended", "quality", "experience", "atmosphere", "vibe",
            "how is", "how are", "is it", "are they", "do people", "what do", "thoughts",
        ]
        food_keywords = ["food", "eat", "restaurant", "hungry", "cuisine", "budget", "cheap", "near", "in "]
        msg_lower = user_message.lower()
        if has_last_candidates and any(kw in msg_lower for kw in review_keywords):
            return "followup_review"
        if any(kw in msg_lower for kw in food_keywords):
            return "restaurant_search"
        return "conversational"
    except Exception as e:
        logger.warning("Intent classification failed: %s", e)
        return "restaurant_search"


# ─────────────────────────────────────────────────────────────────────────────
# Review fetching
# ─────────────────────────────────────────────────────────────────────────────

def extract_place_id_from_uri(uri: str) -> str | None:
    """
    Extract a Google Place ID from a gmaps_uri string.
    Handles formats like:
      https://maps.google.com/?cid=123
      https://maps.google.com/maps?q=place_id:ChIJ...
      https://www.google.com/maps/place/.../@lat,lng,...
    Falls back to None if no ID can be found.
    """
    if not uri:
        return None
    try:
        parsed = urllib.parse.urlparse(uri)
        params = urllib.parse.parse_qs(parsed.query)

        # ?q=place_id:ChIJ...
        for q_val in params.get("q", []):
            if q_val.startswith("place_id:"):
                return q_val.split("place_id:", 1)[1]

        # ?place_id=ChIJ...
        for key in ("place_id", "placeid"):
            if key in params and params[key]:
                return params[key][0]
    except Exception:
        pass
    return None


def fetch_gmaps_place_ids(candidates: list[dict]) -> dict[int, str]:
    """
    Resolve a Google Place ID for each candidate, using (in priority order):
      1. gmaps_place_id column in Supabase
      2. Extracted from gmaps_uri stored on the candidate dict itself
      3. Extracted from gmaps_uri fetched from Supabase

    Returns {internal_place_id: google_place_id}
    """
    if not candidates:
        return {}

    place_ids = [c.get("id") for c in candidates if c.get("id") is not None]
    result: dict[int, str] = {}

    # --- Step 1 & 3: query Supabase for gmaps_place_id AND gmaps_uri together ---
    try:
        res = supabase.table("places").select("id, gmaps_place_id, gmaps_uri").in_("id", place_ids).execute()
        db_rows = {row["id"]: row for row in (res.data or [])}
    except Exception as e:
        logger.error("Supabase fetch for place IDs failed: %s", e)
        db_rows = {}

    for c in candidates:
        pid = c.get("id")
        if pid is None:
            continue

        db_row = db_rows.get(pid, {})

        # Priority 1: gmaps_place_id column
        gid = db_row.get("gmaps_place_id") or ""
        if gid:
            result[pid] = gid
            continue

        # Priority 2: extract from gmaps_uri on the candidate dict
        gid = extract_place_id_from_uri(c.get("gmaps_uri") or "")
        if gid:
            result[pid] = gid
            logger.info("Extracted place ID from candidate gmaps_uri for id=%s: %s", pid, gid)
            continue

        # Priority 3: extract from gmaps_uri stored in Supabase
        gid = extract_place_id_from_uri(db_row.get("gmaps_uri") or "")
        if gid:
            result[pid] = gid
            logger.info("Extracted place ID from Supabase gmaps_uri for id=%s: %s", pid, gid)
            continue

        logger.warning("No Google Place ID found for internal place id=%s (name=%s)", pid, c.get("name"))

    logger.info(
        "fetch_gmaps_place_ids: resolved %d/%d place IDs (key used: %s)",
        len(result), len(place_ids),
        "GOOGLE_PLACES_API_KEY" if os.getenv("GOOGLE_PLACES_API_KEY")
        else "GOOGLE_MAPS_API_KEY" if os.getenv("GOOGLE_MAPS_API_KEY")
        else "GOOGLE_API_KEY (fallback — may lack Places API access)",
    )
    return result


def fetch_reviews_for_place(gmaps_place_id: str) -> list[dict]:
    """Fetch up to 5 Google reviews for a place via the Places Details API (Legacy)."""
    if not PLACES_KEY:
        logger.error("No Places API key configured — cannot fetch reviews")
        return []
    try:
        url = "https://maps.googleapis.com/maps/api/place/details/json"
        params = {
            "place_id": gmaps_place_id,
            "fields":   "reviews,rating,user_ratings_total",
            "key":      PLACES_KEY,
        }
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        status = data.get("status")
        if status != "OK":
            logger.warning(
                "Places Details API returned status=%s for place_id=%s — error: %s",
                status, gmaps_place_id, data.get("error_message", ""),
            )
            return []
        result  = data.get("result", {})
        reviews = result.get("reviews", [])
        return [
            {
                "author": rv.get("author_name", "Anonymous"),
                "rating": rv.get("rating"),
                "text":   rv.get("text", "").strip(),
                "time":   rv.get("relative_time_description", ""),
            }
            for rv in reviews
            if rv.get("text", "").strip()
        ]
    except Exception as e:
        logger.error("Failed to fetch reviews for place_id=%s: %s", gmaps_place_id, e)
        return []


def build_reviews_context(candidates: list[dict], gmaps_id_map: dict[int, str]) -> str:
    """Build a context block containing Google reviews for the given candidates."""
    lines = ["=== RESTAURANT REVIEWS FROM GOOGLE ===\n"]
    any_reviews = False

    for place in candidates[:5]:  # cap at 5 to avoid oversized context
        pid    = place.get("id")
        name   = place.get("name", "Unknown")
        rating = place.get("rating")
        gmaps_place_id = gmaps_id_map.get(pid)

        block = [f"## {name}"]
        if rating:
            block.append(f"   Overall rating: {rating}/5")

        if not gmaps_place_id:
            block.append("   (No Google Place ID resolved — reviews unavailable)")
            lines.append("\n".join(block))
            continue

        reviews = fetch_reviews_for_place(gmaps_place_id)
        if not reviews:
            block.append("   (No reviews returned by the API)")
        else:
            any_reviews = True
            block.append(f"   Reviews ({len(reviews)} fetched):")
            for rv in reviews:
                stars = (
                    f"{'★' * int(rv['rating'])}{'☆' * (5 - int(rv['rating']))}"
                    if rv.get("rating") else ""
                )
                block.append(f"\n   [{rv['author']} — {rv['time']}] {stars}")
                text = rv["text"]
                if len(text) > 400:
                    text = text[:397] + "..."
                block.append(f'   "{text}"')

        lines.append("\n".join(block))

    lines.append("\n=== END OF REVIEWS ===")

    if not any_reviews:
        return ""

    return "\n\n".join(lines)


def generate_review_response(
    user_message: str,
    reviews_context: str,
    candidates: list[dict],
    conversation_history: list,
) -> str:
    """Generate a response summarising Google reviews for previously recommended restaurants."""
    history_text = ""
    if conversation_history:
        for turn in conversation_history[-10:]:
            role = "User" if turn["role"] == "user" else "Assistant"
            history_text += f"{role}: {turn['content']}\n"

    restaurant_names = ", ".join(c.get("name", "") for c in candidates[:5] if c.get("name"))

    prompt = f"""You are FoodKakiBot, a helpful food recommendation assistant for Singapore.

The user was previously shown these restaurants: {restaurant_names}

They are now asking: "{user_message}"

Here are the real Google reviews for those restaurants:

{reviews_context}

--- CONVERSATION HISTORY ---
{history_text.strip() or "(none)"}

Instructions:
- Summarise what reviewers are saying in a natural, conversational way.
- Highlight common themes (e.g. food quality, service, value for money, atmosphere).
- Be honest — if reviews are mixed, say so clearly.
- You may quote a reviewer briefly to support a point, but keep it concise.
- Do NOT invent any reviews or opinions not present in the context above.
- Keep your response friendly and 3-6 sentences long.
"""

    try:
        resp = model.generate_content(prompt)
        return (getattr(resp, "text", "") or "").strip()
    except Exception as e:
        logger.error("Review response generation failed: %s", e)
        return (
            "I wasn't able to fetch reviews right now. "
            "Try checking their Google Maps pages directly — the links are in the cards above!"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Conversational response
# ─────────────────────────────────────────────────────────────────────────────

def generate_conversational_response(user_message: str, conversation_history: list) -> str:
    """Generate a friendly conversational response without touching RAG."""
    history_text = ""
    if conversation_history:
        for turn in conversation_history[-10:]:
            role = "User" if turn["role"] == "user" else "Assistant"
            history_text += f"{role}: {turn['content']}\n"

    prompt = f"""You are FoodKakiBot, a friendly and helpful food recommendation chatbot for Singapore.
You help users discover great restaurants based on their location, budget, and cuisine preferences.

Your capabilities:
- Recommend restaurants across Singapore by location (e.g. Tampines, Orchard, Bugis)
- Filter by cuisine type (Japanese, Chinese, Indian, Western, Malay, Korean, Thai, Vietnamese, Italian, and many more)
- Filter by budget (Budget/cheap, Mid-Range, Expensive, Premium)
- Remember preferences within a conversation
- Show ratings, opening hours, photos, Google Maps links, and real customer reviews

Conversation so far:
{history_text.strip() or "(none)"}

User: {user_message}

Respond naturally and conversationally. Be warm and concise (2-4 sentences).
If appropriate, give a concrete example of how they can search (e.g. "Try asking: 'cheap Japanese food in Tampines'").
Do NOT ask for location unless they've clearly expressed intent to find food."""

    try:
        resp = model.generate_content(prompt)
        return (getattr(resp, "text", "") or "").strip()
    except Exception as e:
        logger.error("Conversational response failed: %s", e)
        return (
            "I'm here to help you find great food in Singapore! "
            "Try asking something like 'cheap Japanese food in Tampines' "
            "or 'best restaurants near Orchard'."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Haversine & distance reranking
# ─────────────────────────────────────────────────────────────────────────────

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geocode_location_to_latlng(location_text: str):
    if not location_text:
        return None
    results, err = google_text_search(f"{location_text} Singapore", limit=1)
    if err or not results:
        logger.warning("Geocode failed for '%s': err=%s", location_text, err)
        return None
    loc = results[0]["geometry"]["location"]
    return float(loc["lat"]), float(loc["lng"])


def rerank_candidates_by_distance(candidates: list[dict], user_lat: float, user_lng: float, top_n_walking: int = 5):
    enriched = []
    no_coords = []
    for c in candidates:
        lat = c.get("latitude")
        lng = c.get("longitude")
        if lat is None or lng is None:
            no_coords.append(c)
            continue
        c["distance_km"]   = round(haversine_km(user_lat, user_lng, float(lat), float(lng)), 2)
        c["distance_mode"] = "straight-line"
        enriched.append(c)

    enriched.sort(key=lambda x: x["distance_km"])
    top  = enriched[:top_n_walking]
    rest = enriched[top_n_walking:]

    destinations = "|".join(f"{float(c['latitude'])},{float(c['longitude'])}" for c in top)
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/distancematrix/json",
            params={
                "origins":      f"{user_lat},{user_lng}",
                "destinations": destinations,
                "mode":         "walking",
                "key":          PLACES_KEY,
            },
            timeout=10,
        )
        dm = resp.json()
        if dm.get("status") == "OK":
            elements = dm["rows"][0]["elements"]
            for i, (candidate, element) in enumerate(zip(top, elements)):
                if element.get("status") == "OK":
                    top[i]["distance_km"]   = round(element["distance"]["value"] / 1000, 2)
                    top[i]["distance_mode"] = "walking"
            top.sort(key=lambda x: x["distance_km"])
            logger.info("Walking distance rerank applied to top %d candidates", len(top))
        else:
            logger.warning("Distance Matrix status: %s", dm.get("status"))
    except Exception as exc:
        logger.warning("Distance Matrix failed, keeping Haversine order: %s", exc)

    return top + rest + no_coords

def apply_progressive_radius(candidates, min_results=3, radius_steps=None):
    """
    Keep expanding radius until we have enough nearby results.
    Returns the closest results found within the smallest radius that works.
    """
    if radius_steps is None:
        radius_steps = [1, 2, 4, 6, 10]

    # only keep candidates that already have a computed distance
    valid = [c for c in candidates if c.get("distance_km") is not None]
    valid.sort(key=lambda x: x["distance_km"])

    if not valid:
        return []

    for radius in radius_steps:
        within = [c for c in valid if c["distance_km"] <= radius]
        if len(within) >= min_results:
            return within[:min_results]

    # if even the largest radius doesn't have enough, return closest available
    return valid[:min_results]

def retrieve_nearby_from_db(
    *,
    user_lat: float,
    user_lng: float,
    budget_tag: str | None = None,
    cuisine_tag: str | None = None,
    limit: int = 50,
):
    """
    Distance-first retrieval from DB for 'near me' queries.
    Filters by budget/cuisine tags if provided, then sorts by haversine distance.
    """
    supabase_client = supabase

    required_tags = [t for t in [budget_tag, cuisine_tag] if t]

    # If no tags, pull a broad pool
    if not required_tags:
        rows = supabase_client.table("places").select(
            "id, name, address, gmaps_uri, editorial_summary, rating, opening_hours, price_level, photo_url, latitude, longitude"
        ).limit(1000).execute().data or []
    else:
        # Reuse your existing tag-based retrieval from rag.py
        rows = retrieve_by_tags(required_tags, limit=1000)

    enriched = []
    for r in rows:
        lat = r.get("latitude")
        lng = r.get("longitude")
        if lat is None or lng is None:
            continue
        r["distance_km"] = round(haversine_km(user_lat, user_lng, float(lat), float(lng)), 2)
        enriched.append(r)

    enriched.sort(key=lambda x: x["distance_km"])
    return enriched[:limit]

# ─────────────────────────────────────────────────────────────────────────────
# RAG-powered chat endpoint
# ─────────────────────────────────────────────────────────────────────────────
def _location_subsets(expanded: list[str]):
    """
    Yield progressively broader location subsets for retrieval attempts.

    Given expand_location_tags("Tampines") → ["Tampines", "Pasir Ris", "Simei", ...]

    Yields:
      ["Tampines"]                        — exact only
      ["Tampines", "Pasir Ris", "Simei"]  — + immediate neighbours
    """
    if not expanded:
        yield []
        return
    yield [expanded[0]]          # exact location only
    if len(expanded) > 1:
        yield expanded           # all (location + neighbours)

@app.route("/api/chat", methods=["POST"])
def chat():
    data         = request.json
    user_message = data.get("message")
    session_id   = data.get("session_id")

    raw_lat = data.get("lat")
    raw_lng = data.get("lng")

    has_gps = False
    gps_lat = None
    gps_lng = None

    try:
        if raw_lat is not None and raw_lng is not None:
            gps_lat = float(raw_lat)
            gps_lng = float(raw_lng)
            has_gps = True
    except (TypeError, ValueError):
        has_gps = False

    if not user_message or not session_id:
        return jsonify({"error": "Missing message or session_id"}), 400

    try:
        # ── 1. Load isolated session state ────────────────────────────────────
        session = get_session(session_id)

        session["history"].append({
            "role":      "user",
            "content":   user_message,
            "timestamp": datetime.now().isoformat(),
        })

        history_for_intent  = [
            {"role": m["role"], "content": m["content"]}
            for m in session["history"][:-1]
        ]
        has_last_candidates = bool(session.get("last_candidates"))

        # ── 1.5. Classify intent ──────────────────────────────────────────────
        intent = classify_message_intent(user_message, history_for_intent, has_last_candidates)
        logger.info("Session %s | intent=%s", session_id[:8], intent)

        # ── Path A: Conversational ────────────────────────────────────────────
        if intent == "conversational":
            reply = generate_conversational_response(user_message, history_for_intent)
            session["history"].append({
                "role": "assistant", "content": reply,
                "timestamp": datetime.now().isoformat(),
            })
            return jsonify({"response": reply, "restaurants": []})

        # ── Path B: Follow-up review question ─────────────────────────────────
        if intent == "followup_review" and has_last_candidates:
            candidates = session["last_candidates"]

            gmaps_id_map    = fetch_gmaps_place_ids(candidates)
            reviews_context = build_reviews_context(candidates, gmaps_id_map)

            if reviews_context:
                reply = generate_review_response(
                    user_message=user_message,
                    reviews_context=reviews_context,
                    candidates=candidates,
                    conversation_history=history_for_intent,
                )
            else:
                reply = (
                    "I wasn't able to pull up reviews for those restaurants right now. "
                    "You can check their Google Maps pages for the latest customer opinions — "
                    "the links are in the cards above!"
                )

            session["history"].append({
                "role": "assistant", "content": reply,
                "timestamp": datetime.now().isoformat(),
            })
            return jsonify({"response": reply, "restaurants": []})

        # ── Path C: Restaurant search — full RAG pipeline ─────────────────────

        # ── 2. Extract tags from THIS message only ────────────────────────────
        matched_tags = extract_tags_from_message(user_message)
        current_tags = classify_required_tags(matched_tags)

        needs_location = current_tags.get("location") is None
        needs_pair     = (current_tags.get("budget") is None and current_tags.get("cuisine") is None)
        if needs_location or needs_pair:
            llm_selected = llm_extract_required_tags(user_message, current_tags)
            current_tags = merge_selected_tags(current_tags, llm_selected)

        # ── 3. Merge with remembered tags from earlier in this session ────────
        remembered = session["tags"]
        resolved = {
            "cuisine":  current_tags.get("cuisine")  or remembered.get("cuisine"),
            "location": current_tags.get("location") or remembered.get("location"),
            "budget":   current_tags.get("budget")   or remembered.get("budget"),
        }
        session["tags"] = resolved

        required_tags = [t for t in [
            resolved.get("cuisine"),
            resolved.get("location"),
            resolved.get("budget"),
        ] if t]

        logger.info(
            "Session %s | current=%s | remembered=%s | resolved=%s",
            session_id[:8], current_tags, remembered, resolved,
        )

        # ── 4. Check we have enough info ──────────────────────────────────────
        has_location = bool(resolved.get("location")) or has_gps
        has_budget   = bool(resolved.get("budget"))
        has_cuisine  = bool(resolved.get("cuisine"))

        missing_response: str | None = None
        missing_reason:   str | None = None

        if not has_location:
            missing_reason = "location"
            known_parts = [f"{k}: {resolved[k]}" for k in ("budget", "cuisine") if resolved.get(k)]
            known_str   = (f" (I already know: {', '.join(known_parts)}.)" if known_parts else "")
            missing_response = (
                f"To find you the perfect restaurant, I still need your location.{known_str} "
                f"For example: 'cheap food in Tampines' or 'Japanese food in Tampines'."
            )
        elif not (has_budget or has_cuisine):
            missing_reason = "budget_or_cuisine"
            known_str = f" (I already know: location: {resolved.get('location')}.)"
            loc = resolved.get("location")
            missing_response = (
                f"To find you the perfect restaurant, tell me either your budget or preferred cuisine.{known_str} "
                f"For example: 'cheap food in {loc}' or 'Japanese food in {loc}'."
            )

        if missing_response:
            if missing_reason == "location":
                phrase = extract_location_phrase_from_message(user_message)
                if phrase:
                    missing_response += f" (I couldn't match '{phrase}' to a known Singapore area.)"

            session["history"].append({
                "role": "assistant", "content": missing_response,
                "timestamp": datetime.now().isoformat(),
            })
            return jsonify({
                "response": missing_response,
                "debug": {"required_tags": required_tags, "missing": missing_reason, "resolved": resolved},
            })

        # ── 5. Hybrid RAG retrieval ───────────────────────────────────────────

        use_gps_only = has_gps and not resolved.get("location")

        if use_gps_only:
            # For "near me", do distance-first retrieval instead of RAG-first
            candidates = retrieve_nearby_from_db(
                user_lat=gps_lat,
                user_lng=gps_lng,
                budget_tag=resolved.get("budget"),
                cuisine_tag=resolved.get("cuisine"),
                limit=50,
            )
            strategy = "nearby_db"
        else:
            raw_loc = resolved.get("location")

            # Expand location to include neighbouring planning areas so that
            # a search for "Tampines" also surfaces results tagged "Simei",
            # "Pasir Ris", etc. — trying most-specific first via multiple
            # tag-set attempts inside retrieve_hybrid.
            expanded_locs = expand_location_tags(raw_loc) if raw_loc else []
            logger.info(
                "Session %s | location expansion: %s → %s",
                session_id[:8], raw_loc, expanded_locs,
            )

            # Try retrieval with progressively broader location sets:
            # 1st: original location only (exact match, most precise)
            # 2nd: original + immediate neighbours
            # This avoids flooding results with distant neighbours when
            # there are already enough local candidates.
            candidates: list = []
            strategy = "none"

            for loc_subset in _location_subsets(expanded_locs):
                candidates, strategy = retrieve_hybrid(
                    user_message,
                    limit=80,
                    location_tags=loc_subset,
                    budget_tags=[resolved.get("budget")] if resolved.get("budget") else [],
                    cuisine_tags=[resolved.get("cuisine")] if resolved.get("cuisine") else [],
                )
                if len(candidates) >= 3:
                    break

            if not candidates:
                # Final fallback: ignore location constraint entirely,
                # rely purely on distance reranking below.
                candidates, strategy = retrieve_hybrid(
                    user_message,
                    limit=80,
                    location_tags=[],
                    budget_tags=[resolved.get("budget")] if resolved.get("budget") else [],
                    cuisine_tags=[resolved.get("cuisine")] if resolved.get("cuisine") else [],
                )
                if candidates:
                    strategy = strategy + "_no_loc_fallback"

        logger.info("Retrieval strategy: %s | candidates: %d", strategy, len(candidates))

        # ── Distance rerank ───────────────────────────────────────────────────
        latlng = None

        if has_gps:
            latlng = (gps_lat, gps_lng)
        else:
            user_loc = resolved.get("location")
            latlng = geocode_location_to_latlng(user_loc)

        if latlng:
            user_lat, user_lng = latlng
            candidates = rerank_candidates_by_distance(candidates, user_lat, user_lng)

            # progressive radius fallback for "near me" / GPS-based search
            if has_gps:
                candidates = apply_progressive_radius(
                    candidates,
                    min_results=3,
                    radius_steps=[1, 2, 4, 6, 10]
                )

            logger.info("Distance rerank enabled (%s,%s)", user_lat, user_lng)
        else:
            logger.info("Distance rerank skipped (no GPS and geocode failed)")

        # ── 6. Fetch tags and build grounded context ──────────────────────────
        place_ids = [p.get("id") for p in candidates if p.get("id") is not None]
        tags_map  = fetch_place_tags_map_rag(place_ids, supabase) if place_ids else {}

        # Attach tags to each candidate so ranking can use them for preference scoring
        for c in candidates:
            c["tags"] = tags_map.get(c.get("id"), [])

        # Log opening_hours format of first candidate for debugging
        if candidates:
            sample_oh = candidates[0].get("opening_hours")
            logger.info("opening_hours sample type=%s value=%s", type(sample_oh).__name__, repr(sample_oh)[:200])

        # ── Ranking: filter closed + score by preference / distance / popularity ─
        allow_closed = detect_allow_closed(user_message)
        candidates = rank_candidates(
            candidates,
            resolved_tags=resolved,
            user_lat=user_lat if latlng else None,
            user_lng=user_lng if latlng else None,
            allow_closed=allow_closed,
        )
        logger.info(
            "Session %s | ranked %d candidates (allow_closed=%s, strategy=%s)",
            session_id[:8], len(candidates), allow_closed, strategy,
        )

        context = build_rag_context(candidates[:10], tags_map)

        # ── 7. Grounded generation ────────────────────────────────────────────
        history_for_llm = [
            {"role": m["role"], "content": m["content"]}
            for m in session["history"][:-1]
        ]

        assistant_message = generate_grounded_response(
            user_message=user_message,
            context=context,
            conversation_history=history_for_llm,
            model=model,
        )

        # ── 8. Persist and return ─────────────────────────────────────────────
        session["history"].append({
            "role": "assistant", "content": assistant_message,
            "timestamp": datetime.now().isoformat(),
        })

        restaurants_for_ui = []
        for c in candidates[:15]:
            cname = c.get("name", "")
            if cname and cname.lower() in assistant_message.lower():
                raw_summary = c.get("editorial_summary") or ""
                description: str = ""
                if isinstance(raw_summary, dict):
                    description = raw_summary.get("overview", "") or ""
                elif isinstance(raw_summary, str) and raw_summary:
                    try:
                        parsed = json.loads(raw_summary)
                        description = (
                            (parsed.get("overview", raw_summary) or "")
                            if isinstance(parsed, dict) else raw_summary
                        )
                    except Exception:
                        description = raw_summary

                restaurants_for_ui.append({
                    "name":          cname,
                    "description":   description,
                    "address":       c.get("address", ""),
                    "maps_url":      c.get("gmaps_uri") or "",
                    "photo_url":     c.get("photo_url") or "",
                    "rating":        c.get("rating"),
                    "opening_hours": c.get("opening_hours"),
                })

        return jsonify({
            "response": assistant_message,
            "restaurants": restaurants_for_ui,
            "debug": {
                "required_tags":       required_tags,
                "retrieval_strategy":  strategy,
                "candidates_found":    len(candidates),
                "resolved_tags":       resolved,
                "top5_distance_check": [
                    {"name": c.get("name"), "distance_km": c.get("distance_km")}
                    for c in candidates[:5]
                ],
            },
        })

    except Exception as e:
        logger.exception("Error in /api/chat: %s", e)
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Debug endpoint — test review fetching for any place
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/debug/reviews")
def debug_reviews():
    """
    Quick diagnostic: fetch reviews for a single place by internal DB id.
    Usage: GET /api/debug/reviews?id=123
    """
    place_id = request.args.get("id")
    if not place_id:
        return jsonify({"error": "missing ?id= param"}), 400

    try:
        pid = int(place_id)
    except ValueError:
        return jsonify({"error": "id must be an integer"}), 400

    res = supabase.table("places").select("id, name, gmaps_place_id, gmaps_uri").eq("id", pid).limit(1).execute()
    rows = res.data or []
    if not rows:
        return jsonify({"error": f"place id={pid} not found in Supabase"}), 404

    row = rows[0]
    name = row.get("name")
    gmaps_place_id = row.get("gmaps_place_id") or extract_place_id_from_uri(row.get("gmaps_uri") or "")

    if not gmaps_place_id:
        return jsonify({
            "place": name,
            "error": "No gmaps_place_id and could not extract one from gmaps_uri",
            "gmaps_uri": row.get("gmaps_uri"),
        })

    reviews = fetch_reviews_for_place(gmaps_place_id)
    return jsonify({
        "place":          name,
        "gmaps_place_id": gmaps_place_id,
        "places_key_src": (
            "GOOGLE_PLACES_API_KEY" if os.getenv("GOOGLE_PLACES_API_KEY")
            else "GOOGLE_MAPS_API_KEY" if os.getenv("GOOGLE_MAPS_API_KEY")
            else "GOOGLE_API_KEY (fallback)"
        ),
        "reviews_count":  len(reviews),
        "reviews":        reviews,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Auxiliary endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/session", methods=["POST"])
def create_session():
    try:
        session_id = str(uuid.uuid4())
        get_session(session_id)
        return jsonify({"session_id": session_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status":            "healthy",
        "active_sessions":   len(sessions),
        "gemini_configured": os.environ.get("GOOGLE_API_KEY") is not None,
        "places_key_src": (
            "GOOGLE_PLACES_API_KEY" if os.getenv("GOOGLE_PLACES_API_KEY")
            else "GOOGLE_MAPS_API_KEY" if os.getenv("GOOGLE_MAPS_API_KEY")
            else "GOOGLE_API_KEY (fallback — may lack Places API)"
        ),
        "rag_enabled": True,
    })


def google_text_search(query: str, limit=5):
    url    = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": query, "key": PLACES_KEY}
    r      = requests.get(url, params=params, timeout=20)
    data   = r.json()
    if data.get("status") != "OK":
        return [], {"status": data.get("status"), "error": data.get("error_message")}
    return data.get("results", [])[:limit], None


def google_place_details(place_id: str):
    url    = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields":   "name,types,editorial_summary,opening_hours,formatted_address,geometry,price_level,rating,url,photos",
        "key":      PLACES_KEY,
    }
    r    = requests.get(url, params=params, timeout=20)
    data = r.json()
    if data.get("status") != "OK":
        return None, {"status": data.get("status"), "error": data.get("error_message")}
    return data.get("result"), None


def google_photo_url(photo_ref: str, maxwidth=800):
    return (
        f"https://maps.googleapis.com/maps/api/place/photo"
        f"?maxwidth={maxwidth}&photo_reference={photo_ref}&key={PLACES_KEY}"
    )


@app.get("/api/google-places")
def google_places_endpoint():
    q = request.args.get("q", "dessert near Farrer Park MRT")
    results, err = google_text_search(q, limit=3)
    if err:
        return jsonify(err), 400
    enriched = []
    for r in results:
        details, derr = google_place_details(r["place_id"])
        if derr:
            continue
        photos = details.get("photos") or []
        photo  = google_photo_url(photos[0]["photo_reference"]) if photos else None
        enriched.append({
            "name":        details.get("name"),
            "address":     details.get("formatted_address"),
            "rating":      details.get("rating"),
            "price_level": details.get("price_level"),
            "open_now":    (details.get("opening_hours") or {}).get("open_now"),
            "maps_url":    details.get("url"),
            "photo_url":   photo,
        })
    return jsonify({"query": q, "results": enriched})


@app.get("/api/google-details-by-placeid")
def google_details_by_placeid():
    place_id = request.args.get("place_id")
    if not place_id:
        return jsonify({"error": "missing place_id"}), 400
    details, err = google_place_details(place_id)
    if err:
        return jsonify(err), 400
    tags = auto_tags_from_google(details)
    return jsonify({
        "place_id":    place_id,
        "name":        details.get("name"),
        "address":     details.get("formatted_address"),
        "price_level": details.get("price_level"),
        "open_now":    (details.get("opening_hours") or {}).get("open_now"),
        "types":       details.get("types"),
        "auto_tags":   tags,
    })

@app.get("/")
def home():
    return "Backend running. Try /api/health"

# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("FoodKakiBot Backend  —  RAG Edition")
    print("=" * 55)
    print("Server  : http://localhost:5000")
    print(f"Gemini  : {'configured' if os.environ.get('GOOGLE_API_KEY') else 'NOT configured'}")
    print(f"Supabase: {'configured' if SUPABASE_URL else 'NOT configured'}")
    print(f"Places  : {'GOOGLE_PLACES_API_KEY' if os.getenv('GOOGLE_PLACES_API_KEY') else 'GOOGLE_MAPS_API_KEY' if os.getenv('GOOGLE_MAPS_API_KEY') else 'GOOGLE_API_KEY (fallback)'}")
    print("RAG     : enabled (pgvector + grounded generation)")
    print("=" * 55)
    app.run(debug=True, port=5000)