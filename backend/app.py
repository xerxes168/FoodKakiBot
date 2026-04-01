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
import time as _time

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

# Configure Gemini API
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-flash-lite')

# Configure Supabase Client
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY")
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

IGNORED_QUERY_TAGS = {"Restaurant", "Wink"}
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

CUISINE_ALIASES = {
    # Filipino
    "filipino": "Asian",   # take filipino food as asian
    "pinoy": "Asian",

    # Chinese regional cuisines -> Chinese
    "hunan": "Chinese",
    "hubei": "Chinese",
    "sichuan": "Chinese",
    "szechuan": "Chinese",
    "cantonese": "Chinese",
    "teochew": "Chinese",
    "hokkien": "Chinese",
    "yunnan": "Chinese",
    "xinjiang": "Chinese",
    "dongbei": "Chinese",
    "shanghai": "Chinese",

    # Japanese subtypes
    "omakase": "Japanese",
    "yakitori": "Japanese",
    "donburi": "Japanese",
    "soba": "Japanese",
    "udon": "Japanese",

    # Korean subtypes
    "kbbq": "Korean",
    "korean bbq": "Korean",
    "bibimbap": "Korean",
    "tteokbokki": "Korean",

    # Indian subtypes
    "biryani": "Indian",
    "briyani": "Indian",
    "dosa": "Indian",
    "prata": "Indian",
    "roti prata": "Indian",

    "pho": "Vietnamese",
    "banh mi": "Vietnamese",

    "tom yum": "Thai",

    "filipino": "Filipino",
    "pinoy": "Filipino",
}

NON_LOCATION_TAGS = (
    BUDGET_TAGS | CUISINE_TAGS | {
        "Delivery", "Dine-In", "Takeaway", "Reservable", "Family-Friendly", "Good for Groups",
        "Outdoor Seating", "In Mall", "Food Court", "Live Music", "Museum", "Park",
        "Nightclub", "Indoor Playground", "Playground", "Restaurant", "Wink",
    }
)

NON_RESTAURANT_SPECIALTY_TAGS = {
    "Bakery", "Bubble Tea", "Dessert", "Ice Cream", "Juice", "Juice Bar", "Tea House",
}

# Google Places types that indicate a non-restaurant specialty store
NON_RESTAURANT_GOOGLE_TYPES = {
    "bakery", "ice_cream_shop", "coffee_shop",
}

# Name patterns that strongly indicate a non-restaurant specialty store
NON_RESTAURANT_NAME_PATTERNS = [
    "bubble tea", "boba", "milk tea",
    "ice cream", "gelato", "froyo",
    "juice bar", "smoothie",
    "tea house", "teahouse",
    "dessert", "cake shop", "pastry",
]

NON_RESTAURANT_REQUEST_TERMS = {
    "Bakery": ["bakery", "bake shop", "pastry", "cake shop"],
    "Bubble Tea": ["bubble tea", "boba", "milk tea"],
    "Dessert": ["dessert", "sweet treat", "sweets"],
    "Ice Cream": ["ice cream", "gelato", "froyo", "frozen yogurt"],
    "Juice": ["juice"],
    "Juice Bar": ["juice bar", "smoothie", "smoothie bowl"],
    "Tea House": ["tea house", "teahouse"],
}


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


def user_explicitly_requested_non_restaurant_spot(user_message: str, resolved: dict | None = None) -> bool:
    resolved = resolved or {}
    cuisine = resolved.get("cuisine")
    if cuisine in NON_RESTAURANT_SPECIALTY_TAGS:
        return True

    normalized = normalize_text_for_match(user_message or "")
    for terms in NON_RESTAURANT_REQUEST_TERMS.values():
        if any(contains_phrase(normalized, term) for term in terms):
            return True
    return False


def _is_non_restaurant_by_signals(candidate: dict) -> bool:
    """Check multiple signals to determine if a place is a non-restaurant specialty store."""
    # Signal 1: All cuisine tags are specialty-only
    tags = set(candidate.get("tags") or [])
    cuisine_tags = tags & CUISINE_TAGS
    if cuisine_tags and cuisine_tags <= NON_RESTAURANT_SPECIALTY_TAGS:
        return True

    # Signal 2: Google types indicate specialty store AND no "restaurant" type
    google_types = set(candidate.get("types") or [])
    has_restaurant_type = bool(google_types & {"restaurant", "food", "meal_delivery", "meal_takeaway"})
    if not has_restaurant_type and (google_types & NON_RESTAURANT_GOOGLE_TYPES):
        # Only specialty Google types, no restaurant type — likely not a restaurant
        # But only filter if it also has no full-service cuisine tags
        full_service_cuisine = cuisine_tags - NON_RESTAURANT_SPECIALTY_TAGS
        if not full_service_cuisine:
            return True

    # Signal 3: Place name strongly suggests specialty store AND no restaurant cuisine tags
    name_lower = (candidate.get("name") or "").lower()
    if any(pattern in name_lower for pattern in NON_RESTAURANT_NAME_PATTERNS):
        full_service_cuisine = cuisine_tags - NON_RESTAURANT_SPECIALTY_TAGS
        if not full_service_cuisine:
            return True

    return False


def filter_non_restaurant_candidates(
    candidates: list[dict],
    *,
    user_message: str,
    resolved: dict | None = None,
) -> list[dict]:
    if not candidates:
        return candidates
    if user_explicitly_requested_non_restaurant_spot(user_message, resolved):
        return candidates

    return [c for c in candidates if not _is_non_restaurant_by_signals(c)]


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


_tag_cache: dict = {"names": None, "lower": None, "ts": 0}
_TAG_CACHE_TTL = 300  # 5 minutes

def get_all_tag_names():
    now = _time.time()
    if _tag_cache["names"] is not None and (now - _tag_cache["ts"]) < _TAG_CACHE_TTL:
        return _tag_cache["names"], _tag_cache["lower"]
    response = supabase.table("tags").select("name").execute()
    tags = [t["name"] for t in response.data]
    lower = {t.lower() for t in tags}
    _tag_cache["names"] = tags
    _tag_cache["lower"] = lower
    _tag_cache["ts"] = now
    return tags, lower


def extract_tags_from_message(user_message):
    tag_names, tag_set = get_all_tag_names()
    user_text = user_message.lower()

    matched_tags = [tag for tag in tag_names if tag.lower() in user_text]
    return matched_tags

def canonicalize_query_text(user_message: str) -> str:
    text = user_message or ""
    normalized = text

    for alias, canonical in CUISINE_ALIASES.items():
        pattern = re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE)
        normalized = pattern.sub(canonical, normalized)

    return normalized

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
    m = re.search(r"\b(?:in|at|near|around)\s+([a-zA-Z0-9\s\-]+)", text, flags=re.IGNORECASE)
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


_catalog_cache: dict = {"data": None, "ts": 0}

def get_tag_catalog():
    now = _time.time()
    if _catalog_cache["data"] is not None and (now - _catalog_cache["ts"]) < _TAG_CACHE_TTL:
        return _catalog_cache["data"]
    tag_names, _ = get_all_tag_names()
    catalog = {
        "all": tag_names,
        "budgets":   sorted(t for t in tag_names if t in BUDGET_TAGS),
        "cuisines":  sorted(t for t in tag_names if t in CUISINE_TAGS),
        "locations": get_location_tags_from_all_tags(tag_names),
    }
    _catalog_cache["data"] = catalog
    _catalog_cache["ts"] = now
    return catalog


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
    Classify the user message into one of four intents:
      - "restaurant_search"  : user wants new restaurant recommendations
      - "followup_review"    : user is asking about quality/reviews/experience of
                               previously shown restaurants
      - "followup_info"      : user is asking a general question about previously
                               shown restaurants (menu, distance, hours, price, etc.)
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

- "restaurant_search" : The user wants NEW restaurant recommendations or wants to DISCOVER places to eat. This includes ANY request involving food, restaurants, dining, cuisine, price/budget, or location — even if vague or casual. Examples: "cheap Japanese food in Tampines", "best restaurants near Orchard", "give me 10 random top rated places that won't break the bank", "affordable food options", "where to eat", "what's good around here", "any halal places", "I'm hungry", "suggest something", "are there nearer options" (when no prior restaurants shown).

- "followup_info" : The user is asking a GENERAL question about restaurants that were ALREADY recommended in this conversation — such as what food they sell, their menu, distance, opening hours, price, location details, or any factual detail. Examples: "what food is sold there?", "are there nearer options?", "which one is cheapest?", "how far is it?", "what time do they close?", "what cuisine do they serve?", "tell me more about the first one".

- "followup_review" : The user is asking specifically about REVIEWS, ratings, quality, or customer opinions of restaurants already recommended. Examples: "is the food good?", "what do people think?", "how are the reviews?", "is it worth it?", "is it nice there?".

- "conversational" : ONLY for greetings, thank-you messages, asking what the bot can do, or clearly off-topic chat that has NOTHING to do with food or restaurants. Examples: "hello", "thanks!", "what can you do?", "tell me a joke".

IMPORTANT: When in doubt between "conversational" and "restaurant_search", choose "restaurant_search". The bot is a food recommendation bot — most messages are about food.
{"Note: The bot has previously recommended restaurants in this conversation. Questions about those places should be followup_info or followup_review, NOT restaurant_search." if has_last_candidates else "Note: No restaurants have been recommended yet, so follow-up intents are unlikely — prefer restaurant_search."}

Reply with ONLY one of these exact strings: restaurant_search, followup_info, followup_review, conversational
""".strip()

    try:
        resp = model.generate_content(prompt)
        answer = (getattr(resp, "text", "") or "").strip().lower()
        if "followup_review" in answer:
            return "followup_review"
        if "followup_info" in answer:
            return "followup_info"
        if "restaurant_search" in answer or "search" in answer:
            return "restaurant_search"
        if "conversational" in answer:
            return "conversational"
        # Keyword fallback
        msg_lower = user_message.lower()
        review_keywords = [
            "good", "worth", "nice", "review", "opinion", "people say", "popular",
            "recommended", "quality", "experience", "atmosphere", "vibe",
            "how is", "how are", "is it", "are they", "do people", "what do", "thoughts",
        ]
        followup_info_keywords = [
            "what food", "what do they sell", "what do they serve", "what cuisine",
            "menu", "tell me more", "more about", "which one", "how far",
            "what time", "when do they", "opening hours", "nearer", "closer",
            "cheapest", "most expensive", "nearest", "closest",
            "first one", "second one", "third one", "the one",
        ]
        food_keywords = [
            "food", "eat", "restaurant", "hungry", "cuisine", "budget", "cheap",
            "near", "in ", "recommend", "suggest", "places", "options", "spot",
            "dinner", "lunch", "breakfast", "supper", "brunch", "meal",
            "affordable", "top rated", "best", "random", "halal", "vegetarian",
            "vegan", "break the bank", "pricey", "expensive", "premium",
        ]
        if has_last_candidates and any(kw in msg_lower for kw in review_keywords):
            return "followup_review"
        if has_last_candidates and any(kw in msg_lower for kw in followup_info_keywords):
            return "followup_info"
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
# Follow-up info response (non-review questions about previously shown places)
# ─────────────────────────────────────────────────────────────────────────────

def generate_followup_info_response(
    user_message: str,
    candidates: list[dict],
    conversation_history: list,
) -> str:
    """Answer a general follow-up question using data from previously shown restaurants."""
    history_text = ""
    if conversation_history:
        for turn in conversation_history[-10:]:
            role = "User" if turn["role"] == "user" else "Assistant"
            history_text += f"{role}: {turn['content']}\n"

    # Build a detailed context block from the cached candidates
    restaurant_info_parts = []
    for i, c in enumerate(candidates[:10], 1):
        tags = c.get("tags") or []
        summary = c.get("editorial_summary") or ""
        if isinstance(summary, dict):
            summary = summary.get("text") or summary.get("overview") or ""
        distance = c.get("distance_km")
        distance_str = f"{distance:.1f} km away" if distance else "distance unknown"
        opening_hours = c.get("opening_hours") or "unknown"
        price_level = c.get("price_level")
        price_str = ("$" * price_level) if price_level else "unknown"
        gmaps = c.get("gmaps_uri") or ""

        restaurant_info_parts.append(
            f"{i}. {c.get('name', 'Unknown')}\n"
            f"   Address: {c.get('address', 'N/A')}\n"
            f"   Rating: {c.get('rating', 'N/A')}\n"
            f"   Price Level: {price_str}\n"
            f"   Distance: {distance_str}\n"
            f"   Tags: {', '.join(tags) if tags else 'N/A'}\n"
            f"   Description: {summary or 'N/A'}\n"
            f"   Opening Hours: {opening_hours}\n"
            f"   Google Maps: {gmaps}"
        )

    restaurants_block = "\n\n".join(restaurant_info_parts)

    prompt = f"""You are FoodKakiBot, a helpful food recommendation assistant for Singapore.

The user was previously shown these restaurants:

{restaurants_block}

--- CONVERSATION HISTORY ---
{history_text.strip() or "(none)"}

The user is now asking: "{user_message}"

Instructions:
- Answer their question using ONLY the restaurant data provided above.
- If they ask about food/cuisine, use the tags and description to answer.
- If they ask about distance or nearer options, compare the distances and highlight the closest ones.
- If they ask about price, use the price level information.
- If they ask about hours, use the opening hours data.
- Do NOT invent any information not present in the data above.
- Be concise and conversational (3-6 sentences).
- If the data doesn't contain enough info to fully answer, say so honestly and suggest they check the Google Maps links.
"""

    try:
        resp = model.generate_content(prompt)
        return (getattr(resp, "text", "") or "").strip()
    except Exception as e:
        logger.error("Follow-up info response failed: %s", e)
        return (
            "I don't have enough details to answer that right now. "
            "Try checking the Google Maps links in the cards above for more info!"
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

def apply_progressive_radius(candidates, min_results=5, radius_steps=None):
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
            "id, name, address, gmaps_uri, editorial_summary, rating, opening_hours, price_level, photo_url, latitude, longitude, types"
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

def is_generic_recommendation_request(user_message: str) -> bool:
    msg = (user_message or "").lower().strip()

    if "recommend" in msg and ("restaurant" in msg or "food" in msg or "eat" in msg):
        return True

    generic_patterns = [
        "where should i eat",
        "what should i eat",
        "where to eat",
        "food recommendation",
        "give me recommendations",
        "any recommendations",
        "best food",
        "best restaurant",
        "top rated",
        "random places",
        "random restaurant",
        "random food",
        "suggest me",
        "suggest something",
        "won't break the bank",
        "wont break the bank",
        "break the bank",
        "affordable places",
        "affordable food",
        "cheap places",
        "good places",
        "good food",
        "nice places",
        "nice food",
    ]
    return any(p in msg for p in generic_patterns)

def generic_review_rank_key(candidate: dict):
    """
    Rank generic recommendations mainly by rating/review strength.
    If review-count fields are unavailable, it falls back to rating only.
    """
    rating = candidate.get("rating") or 0
    review_count = (
        candidate.get("user_ratings_total")
        or candidate.get("reviews_count")
        or candidate.get("review_count")
        or 0
    )
    return (rating, review_count)

def is_llm_error_message(text: str) -> bool:
    msg = (text or "").lower().strip()
    error_markers = [
        "sorry, i encountered an error",
        "please try again",
        "error generating a response",
        "wasn't able to",
        "quota",
        "rate limit",
    ]
    return any(m in msg for m in error_markers)

def sort_best_rated(candidates: list[dict]) -> list[dict]:
    return sorted(
        candidates,
        key=lambda c: (
            c.get("rating") or 0,
            c.get("user_ratings_total") or c.get("reviews_count") or c.get("review_count") or 0
        ),
        reverse=True
    )

def get_review_count(candidate: dict) -> int:
    return (
        candidate.get("user_ratings_total")
        or candidate.get("reviews_count")
        or candidate.get("review_count")
        or 0
    )


def build_candidate_reason(candidate: dict, resolved: dict, has_gps: bool = False) -> str:
    reasons = []

    cuisine = resolved.get("cuisine")
    budget = resolved.get("budget")
    location = resolved.get("location")

    tags = set(candidate.get("tags") or [])

    # match reasons
    if cuisine and cuisine in tags:
        reasons.append(f"matches your {cuisine.lower()} preference")

    if budget and budget in tags:
        budget_reason = {
            "Budget": "fits your budget preference",
            "Mid-Range": "fits your mid-range budget",
            "Expensive": "fits your expensive budget",
            "Premium": "fits your premium budget",
            "Free": "fits your free budget preference",
        }.get(budget, f"fits your {budget.lower()} budget")
        reasons.append(budget_reason)

    # rating / popularity reasons
    rating = candidate.get("rating")
    review_count = get_review_count(candidate)

    if rating:
        if review_count:
            reasons.append(f"has a strong rating of {rating} from {review_count} reviews")
        else:
            reasons.append(f"has a strong rating of {rating}")

    # distance / area reasons
    distance_km = candidate.get("distance_km")
    if distance_km is not None:
        if location:
            reasons.append(f"is about {distance_km:.1f} km from {location}")
        elif has_gps:
            reasons.append(f"is about {distance_km:.1f} km from you")

    # fallback
    if not reasons:
        reasons.append("it is one of the stronger matches based on your request")

    return "Recommended because it " + ", ".join(reasons) + "."


def build_rank_reason(candidate: dict, index: int, resolved: dict, has_gps: bool = False) -> str:
    rating = candidate.get("rating") or 0
    review_count = get_review_count(candidate)
    distance_km = candidate.get("distance_km")

    location = resolved.get("location")
    if distance_km is not None and (location or has_gps):
        dist_label = f"{location}" if location else "you"
        return (
            f"Ranked #{index} because it balances rating ({rating})"
            + (f", review volume ({review_count})" if review_count else "")
            + f", and distance from {dist_label} ({distance_km:.1f} km)."
        )

    if resolved.get("location"):
        return (
            f"Ranked #{index} because it is a strong match for the requested area"
            + (f", has rating {rating}" if rating else "")
            + (f", and has {review_count} reviews" if review_count else "")
            + "."
        )

    return (
        f"Ranked #{index} mainly by rating"
        + (f" ({rating})" if rating else "")
        + (f" and review volume ({review_count})" if review_count else "")
        + "."
    )


def enrich_candidates_with_reasoning(candidates: list[dict], resolved: dict, has_gps: bool = False) -> list[dict]:
    enriched = []
    for idx, c in enumerate(candidates, start=1):
        c = dict(c)
        c["recommended_because"] = build_candidate_reason(c, resolved, has_gps=has_gps)
        c["rank_reason"] = build_rank_reason(c, idx, resolved, has_gps=has_gps)
        enriched.append(c)
    return enriched

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    user_message = data.get('message')
    session_id = data.get('session_id')
    
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

        # ── Path B2: Follow-up info question (non-review) ────────────────────
        if intent == "followup_info" and has_last_candidates:
            candidates = session["last_candidates"]
            reply = generate_followup_info_response(
                user_message=user_message,
                candidates=candidates,
                conversation_history=history_for_intent,
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
        generic_request = is_generic_recommendation_request(user_message)
        remembered = session["tags"]

        resolved = {
            "cuisine": current_tags.get("cuisine") or remembered.get("cuisine"),
            "location": current_tags.get("location") if generic_request else (current_tags.get("location") or remembered.get("location")),
            "budget": current_tags.get("budget") or remembered.get("budget"),
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
        has_explicit_location = bool(resolved.get("location"))
        has_near_me_location = bool(has_gps)
        has_any_location_context = has_explicit_location or has_near_me_location

        has_budget = bool(resolved.get("budget"))
        has_cuisine = bool(resolved.get("cuisine"))

        generic_request = is_generic_recommendation_request(user_message)

        # Broad recommendations are allowed in two cases:
        # 1) no location, but user still asked generally / gave some preference
        # 2) location exists, but user did not specify budget or cuisine yet
        broad_reco_mode = (
            (not has_any_location_context and (generic_request or has_budget or has_cuisine))
            or
            (has_any_location_context and not has_budget and not has_cuisine)
        )

        missing_response: str | None = None
        missing_reason:   str | None = None

        # only block when we really have nothing useful
        if not has_any_location_context and not broad_reco_mode:
            missing_reason = "location"
            known_parts = [f"{k}: {resolved[k]}" for k in ("budget", "cuisine") if resolved.get(k)]
            known_str   = (f" (I already know: {', '.join(known_parts)}.)" if known_parts else "")
            missing_response = (
                f"Tell me a location in Singapore and I’ll recommend places for you.{known_str} "
                f"For example: 'cheap food in Tampines' or 'Japanese food in Bugis'."
            )

        elif has_any_location_context and not (has_budget or has_cuisine) and not generic_request:
            pass

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
                "debug": {
                    "required_tags": required_tags,
                    "missing": missing_reason,
                    "resolved": resolved,
                    "generic_request": generic_request,
                    "broad_reco_mode": broad_reco_mode,
                },
            })

        # ── 5. Hybrid RAG retrieval ───────────────────────────────────────────

        canonical_user_message = canonicalize_query_text(user_message)

        raw_loc = resolved.get("location")
        location_only_mode = bool(raw_loc) and not resolved.get("budget") and not resolved.get("cuisine")

        use_gps_only = has_gps and not resolved.get("location")

        if use_gps_only:
            candidates = retrieve_nearby_from_db(
                user_lat=gps_lat,
                user_lng=gps_lng,
                budget_tag=resolved.get("budget"),
                cuisine_tag=resolved.get("cuisine"),
                limit=50,
            )
            strategy = "nearby_db"

            if not candidates:
                # Final fallback: ignore location and search broadly across Singapore
                fallback_query = canonicalize_query_text(user_message)
                candidates, strategy = retrieve_hybrid(
                    fallback_query,
                    limit=80,
                    location_tags=[],
                    budget_tags=[resolved.get("budget")] if resolved.get("budget") else [],
                    cuisine_tags=[resolved.get("cuisine")] if resolved.get("cuisine") else [],
                )
                if candidates:
                    strategy = strategy + "_no_loc_fallback"

            # If still empty and we have some preference (budget/cuisine or generic request),
            # do a broad Singapore-wide tag-based fallback and rank by rating.
            if not candidates and (resolved.get("budget") or resolved.get("cuisine") or generic_request):
                broad_tags = [t for t in [resolved.get("budget"), resolved.get("cuisine")] if t]
                if broad_tags:
                    candidates = retrieve_by_tags(broad_tags, limit=200)
                    strategy = "singapore_tag_fallback"
                else:
                    candidates = supabase.table("places").select(
                        "id, name, address, gmaps_uri, editorial_summary, rating, opening_hours, price_level, photo_url, latitude, longitude, types"
                    ).limit(200).execute().data or []
                    strategy = "singapore_global_fallback"

                candidates = sort_best_rated(candidates)

        else:
            expanded_locs = expand_location_tags(raw_loc) if raw_loc else []
            retrieval_location_tags = expanded_locs if expanded_locs else ([raw_loc] if raw_loc else [])

            logger.info(
                "Session %s | location expansion: %s → %s",
                session_id[:8], raw_loc, expanded_locs,
            )

            candidates: list = []
            strategy = "none"

            if location_only_mode:
                seen_ids = set()
                merged_candidates = []

                # 1) exact location tag first
                exact_candidates = retrieve_by_tags([raw_loc], limit=80) if raw_loc else []
                for c in exact_candidates:
                    cid = c.get("id")
                    if cid not in seen_ids:
                        seen_ids.add(cid)
                        merged_candidates.append(c)

                if merged_candidates:
                    strategy = "location_tag_exact"

                # 2) expand to neighbouring locations if exact isn't enough
                if len(merged_candidates) < 10 and expanded_locs:
                    expanded_candidates = retrieve_by_tags(expanded_locs, limit=80)
                    for c in expanded_candidates:
                        cid = c.get("id")
                        if cid not in seen_ids:
                            seen_ids.add(cid)
                            merged_candidates.append(c)

                    if merged_candidates:
                        strategy = "location_tag_expanded"

                # 3) fallback to hybrid area query only if tag retrieval is still weak
                if len(merged_candidates) < 5:
                    area_query = f"best restaurants in {raw_loc}"
                    hybrid_candidates, hybrid_strategy = retrieve_hybrid(
                        area_query,
                        limit=80,
                        location_tags=expanded_locs if expanded_locs else ([raw_loc] if raw_loc else []),
                        budget_tags=[],
                        cuisine_tags=[],
                    )
                    for c in hybrid_candidates:
                        cid = c.get("id")
                        if cid not in seen_ids:
                            seen_ids.add(cid)
                            merged_candidates.append(c)

                    if merged_candidates:
                        strategy = hybrid_strategy + "_area_fallback"

                    candidates = merged_candidates
                
            else:
                candidates, strategy = retrieve_hybrid(
                    canonical_user_message,
                    limit=80,
                    location_tags=retrieval_location_tags if retrieval_location_tags else [],
                    budget_tags=[resolved.get("budget")] if resolved.get("budget") else [],
                    cuisine_tags=[resolved.get("cuisine")] if resolved.get("cuisine") else [],
                )

                if candidates:
                    strategy = strategy + ("_location_preference" if retrieval_location_tags else "_preference_only")

                # Singapore-wide fallback if hybrid is still empty
                if not candidates and (resolved.get("budget") or resolved.get("cuisine") or generic_request):
                    broad_tags = [t for t in [resolved.get("budget"), resolved.get("cuisine")] if t]

                    if broad_tags:
                        candidates = retrieve_by_tags(broad_tags, limit=200)
                        strategy = "singapore_tag_fallback"
                    else:
                        candidates = supabase.table("places").select(
                            "id, name, address, gmaps_uri, editorial_summary, rating, opening_hours, price_level, photo_url, latitude, longitude, types"
                        ).limit(200).execute().data or []
                        strategy = "singapore_global_fallback"

                    candidates = sort_best_rated(candidates)

        logger.info("Retrieval strategy: %s | candidates: %d", strategy, len(candidates))

        # ── Distance rerank ───────────────────────────────────────────────────
        latlng = None

        user_loc = resolved.get("location")
        if user_loc:
            # Location explicitly specified → rank by distance from that place,
            # not the user's physical position.
            latlng = geocode_location_to_latlng(user_loc)
            if not latlng and has_gps:
                # Geocode failed → fall back to GPS so distance ranking still works
                latlng = (gps_lat, gps_lng)
        elif has_gps:
            # No location specified (e.g. "near me" or no location at all) → use GPS
            latlng = (gps_lat, gps_lng)

        if latlng:
            user_lat, user_lng = latlng
            candidates = rerank_candidates_by_distance(candidates, user_lat, user_lng)

            # Keep the nearest sufficiently large cluster when distance context exists.
            if len(candidates) > 5 and not broad_reco_mode:
                candidates = apply_progressive_radius(
                    candidates,
                    min_results=8 if raw_loc else 5,
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

        pre_filter_count = len(candidates)
        candidates = filter_non_restaurant_candidates(
            candidates,
            user_message=user_message,
            resolved=resolved,
        )
        if len(candidates) != pre_filter_count:
            logger.info(
                "Filtered %d non-restaurant specialty candidates for session %s",
                pre_filter_count - len(candidates),
                session_id[:8],
            )

        # Log opening_hours format of first candidate for debugging
        if candidates:
            sample_oh = candidates[0].get("opening_hours")
            logger.info("opening_hours sample type=%s value=%s", type(sample_oh).__name__, repr(sample_oh)[:200])

        # ── Ranking: score by preference / distance / popularity ──────────────
        candidates = rank_candidates(
            candidates,
            resolved_tags=resolved,
            user_lat=user_lat if latlng else None,
            user_lng=user_lng if latlng else None,
        )

        candidates = enrich_candidates_with_reasoning(
            candidates,
            resolved=resolved,
            has_gps=has_gps,
        )

        if not candidates:
            if resolved.get("location"):
                fallback_msg = (
                    f"I couldn’t find enough restaurant matches around {resolved.get('location')} right now. "
                    f"Tell me your budget or preferred cuisine and I’ll refine the search."
                )
            elif has_gps:
                fallback_msg = (
                    "I couldn’t find enough nearby restaurant matches right now. "
                    "Tell me your budget or preferred cuisine and I’ll refine the search."
                )
            else:
                fallback_msg = (
                    "I couldn’t narrow it down properly, so here are some of the best-rated restaurant options in Singapore instead. "
                    "Tell me a location if you want more relevant recommendations near you."
                )

            session["history"].append({
                "role": "assistant",
                "content": fallback_msg,
                "timestamp": datetime.now().isoformat(),
            })

            return jsonify({
                "response": fallback_msg,
                "restaurants": [],
                "active_tags": {k: v for k, v in resolved.items() if v},
                "debug": {
                    "required_tags": required_tags,
                    "retrieval_strategy": strategy,
                    "candidates_found": 0,
                    "resolved_tags": resolved,
                },
            })

        context = build_rag_context(candidates[:10], tags_map)

        # ── 7. Grounded generation ────────────────────────────────────────────
        history_for_llm = [
            {"role": m["role"], "content": m["content"]}
            for m in session["history"][:-1]
        ]

        generation_query = canonical_user_message

        if location_only_mode and resolved.get("location"):
            generation_query = f"restaurants in {resolved.get('location')}"

        assistant_message = generate_grounded_response(
            user_message=generation_query,
            context=context,
            conversation_history=history_for_llm,
            model=model,
        ) or "Here are some restaurant recommendations for you."

        top_reason_lines = []
        for c in candidates[:3]:
            name = c.get("name", "This restaurant")
            why = c.get("recommended_because", "")
            if why:
                top_reason_lines.append(f"- {name}: {why}")

        if top_reason_lines:
            assistant_message += "\n\nWhy these were recommended:\n" + "\n".join(top_reason_lines)

        if candidates and is_llm_error_message(assistant_message):
            if resolved.get("location") and not has_budget and not has_cuisine:
                assistant_message = (
                    f"Here are some restaurant recommendations around {resolved.get('location')}."
                )
            elif not has_any_location_context and broad_reco_mode:
                assistant_message = "Here are some restaurant recommendations for you."
            elif resolved.get("cuisine") and has_gps:
                assistant_message = (
                    f"Here are some {resolved.get('cuisine')} places near you."
                )
            else:
                assistant_message = "Here are some restaurant recommendations for you."

        if not has_any_location_context and broad_reco_mode:
            assistant_message += (
                "\n\nDo you have any preferred location in Singapore "
                "for more accurate results?"
            )
        elif has_any_location_context and not has_budget and not has_cuisine:
            assistant_message += (
                "\n\nTell me either your budget or preferred cuisine "
                "for more tailored recommendations."
            )
        
        if strategy == "singapore_tag_fallback":
            if resolved.get("cuisine") and resolved.get("budget"):
                assistant_message = (
                    f"Here are some of the best-rated {resolved.get('budget').lower()} {resolved.get('cuisine')} options in Singapore."
                )
            elif resolved.get("cuisine"):
                assistant_message = (
                    f"Here are some of the best-rated {resolved.get('cuisine')} restaurants in Singapore."
                )
            elif resolved.get("budget"):
                assistant_message = (
                    f"Here are some of the best-rated {resolved.get('budget').lower()} restaurant options in Singapore."
                )
        elif strategy == "singapore_global_fallback":
            assistant_message = "Here are some of the best-rated restaurants in Singapore."

        # ── 8. Persist and return ─────────────────────────────────────────────
        session["history"].append({
            "role": "assistant", "content": assistant_message,
            "timestamp": datetime.now().isoformat(),
        })

        restaurants_for_ui = []
        top_n = 10 if broad_reco_mode else 5

        for c in candidates[:top_n]:
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

            candidate_tags = c.get("tags") or []
            restaurants_for_ui.append({
                "name":                c.get("name", ""),
                "description":         description,
                "address":             c.get("address", ""),
                "maps_url":            c.get("gmaps_uri") or "",
                "photo_url":           c.get("photo_url") or "",
                "rating":              c.get("rating"),
                "opening_hours":       c.get("opening_hours"),
                "recommended_because": c.get("recommended_because", ""),
                "rank_reason":         c.get("rank_reason", ""),
                "is_wink":             "Wink" in candidate_tags,
            })

        return jsonify({
            "response": assistant_message,
            "restaurants": restaurants_for_ui,
            "active_tags": {k: v for k, v in resolved.items() if v},
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
        print(f"Error: {str(e)}")
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@app.route('/api/session', methods=['POST'])
def create_session():
    try:
        session_id = str(uuid.uuid4())
        conversations[session_id] = []
        return jsonify({"session_id": session_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "active_sessions": len(conversations),
        "gemini_configured": os.environ.get("GOOGLE_API_KEY") is not None
    })

if __name__ == '__main__':
    print("=" * 50)
    print("Restaurant Chatbot Backend (No Database)")
    print("=" * 50)
    print("Server running on: http://localhost:5000")
    print(f"Gemini API configured: {os.environ.get('GOOGLE_API_KEY') is not None}")
    print("Health check: http://localhost:5000/api/health")
    print("=" * 50)
    app.run(debug=True, port=5000)