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
from tagging import auto_tags_from_google


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

# ── Google Places API key ──────────────────────────────────────────────────────
# Prefer the dedicated Places key; fall back to the shared Gemini key.
PLACES_KEY = (
    os.getenv("GOOGLE_PLACES_API_KEY")
    or os.getenv("GOOGLE_MAPS_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
)

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
        "Nightclub", "Indoor Playground", "Playground", "Restaurant",
    }
)

NON_RESTAURANT_SPECIALTY_TAGS = {
    "Bakery", "Bubble Tea", "Dessert", "Ice Cream", "Juice", "Juice Bar", "Tea House",
}

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

    filtered: list[dict] = []
    for candidate in candidates:
        tags = set(candidate.get("tags") or [])
        cuisine_tags = tags & CUISINE_TAGS
        if cuisine_tags and cuisine_tags <= NON_RESTAURANT_SPECIALTY_TAGS:
            continue
        filtered.append(candidate)

    return filtered


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

    # exact DB tag matches first
    for tag in sorted(tag_names, key=len, reverse=True):
        if tag in IGNORED_QUERY_TAGS:
            continue
        if contains_phrase(normalized_user_text, tag):
            matched_tags.append(tag)

    # alias matches -> canonical DB tag
    for alias, canonical in CUISINE_ALIASES.items():
        if contains_phrase(normalized_user_text, alias):
            actual_tag = tag_lookup.get(canonical.lower())
            if actual_tag and actual_tag not in matched_tags:
                matched_tags.append(actual_tag)

    # price aliases
    canonical_price_tag = detect_canonical_price_tag(user_message or "")
    if canonical_price_tag:
        actual_tag = tag_lookup.get(canonical_price_tag.lower())
        if actual_tag and actual_tag not in matched_tags:
            matched_tags.append(actual_tag)

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
    Classify the user message into one of five intents:
      - "restaurant_search"  : user wants new restaurant recommendations
      - "followup_filter"    : user wants to filter/sort/refine previously shown
                               restaurants — returns updated cards
      - "followup_info"      : user is asking an analytical or factual question about
                               previously shown restaurants — returns text only.
                               Also used for specific restaurant info queries (e.g.
                               popular dishes, menu items) that should be answered
                               via LLM + web search instead of RAG.
      - "followup_review"    : user is asking about quality/reviews/experience of
                               previously shown restaurants
      - "conversational"     : general chat, greetings, capability questions, etc.
    """
    msg_lower = user_message.lower()

    # ── PRE-FILTER: catch clear-cut patterns before calling the LLM ──────
    # These patterns are unambiguously about getting *info* about a
    # restaurant (popular dishes, menu, prices, hours) and should always
    # bypass RAG — regardless of whether we have prior candidates.
    _info_pre_patterns = [
        "popular dish", "signature dish", "best dish", "must try", "must-try",
        "recommended dish", "famous dish", "famous for",
        "what do they serve", "what do they sell", "what does it serve",
        "what's on the menu", "what is on the menu", "menu item",
        "popular food at", "popular item", "what food do they",
        "tell me about", "tell me more about", "more about",
        "what are the prices", "how much does", "how much do",
    ]
    if any(kw in msg_lower for kw in _info_pre_patterns):
        logger.info("Pre-filter matched followup_info for: %r", user_message)
        return "followup_info"

    # Analytical questions about previously shown places → followup_info
    _analytical_patterns = [
        "average price", "average cost", "average rating",
        "how much on average", "what's the average",
        "which is cheapest", "which is closest", "which one is",
        "compare these", "compare them", "comparison",
    ]
    if has_last_candidates and any(kw in msg_lower for kw in _analytical_patterns):
        logger.info("Pre-filter matched followup_info (analytical) for: %r", user_message)
        return "followup_info"

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

- "restaurant_search" : The user wants NEW restaurant recommendations or wants to DISCOVER places to eat. This includes ANY request involving food, restaurants, dining, cuisine, price/budget, or location — even if vague or casual. Examples: "cheap Japanese food in Tampines", "best restaurants near Orchard", "give me 10 random top rated places that won't break the bank", "affordable food options", "where to eat", "what's good around here", "any halal places", "I'm hungry", "suggest something".

- "followup_filter" : The user wants to REFINE, FILTER, or RE-SORT the restaurants that were already shown — they want a DIFFERENT SUBSET or ORDER of the same results. The answer should show NEW restaurant cards. Examples: "are there nearer options?", "above 4.5 stars", "show me only halal ones", "cheaper options", "sort by rating", "only budget places", "anything closer?", "higher rated ones", "remove the expensive ones", "above 4.5 stars review".

- "followup_info" : The user is asking a FACTUAL or ANALYTICAL question about restaurants — they want a TEXT answer, not new cards. This includes questions about specific restaurants by name, even if they weren't previously shown. Examples: "what's the average price?", "which one is cheapest?", "what food is sold there?", "how far is the first one?", "what time do they close?", "tell me more about the second one", "what cuisine do they serve?", "compare these two", "what are the popular dishes at Imperial Treasure?", "what does Din Tai Fung serve?", "how much does a meal cost there?".

- "followup_review" : The user is asking specifically about REVIEWS, customer opinions, or subjective quality of restaurants — NOT about filtering by rating. Examples: "is the food good?", "what do people think?", "how are the reviews?", "is it worth it?", "is it nice there?".

- "conversational" : ONLY for greetings, thank-you messages, asking what the bot can do, or clearly off-topic chat that has NOTHING to do with food or restaurants. Examples: "hello", "thanks!", "what can you do?", "tell me a joke".

IMPORTANT RULES:
- When in doubt between "conversational" and "restaurant_search", choose "restaurant_search".
- When the user mentions a rating threshold (e.g. "above 4.5 stars"), that is "followup_filter", NOT "followup_review".
- When the user asks about specific restaurant details (popular dishes, menu, prices), that is "followup_info" — even if the restaurant was not previously shown.
{"- The bot has previously recommended restaurants in this conversation. Questions about those places should be followup_filter, followup_info, or followup_review, NOT restaurant_search." if has_last_candidates else "- No restaurants have been recommended yet, so followup_filter is unlikely. However, followup_info is still valid for specific restaurant questions (e.g. 'what are the popular dishes at X?')."}

Reply with ONLY one of these exact strings: restaurant_search, followup_filter, followup_info, followup_review, conversational
""".strip()

    # ── Keyword-based fallback classifier ────────────────────────────────
    # Used when the LLM call fails OR when the LLM returns an unrecognised
    # string.  Defined here so both the happy path and the except path share it.
    def _keyword_fallback() -> str:
        followup_filter_keywords = [
            "nearer", "closer", "nearest", "closest", "cheaper", "cheapest",
            "higher rated", "above", "over", "at least", "minimum",
            "only halal", "only vegetarian", "only budget", "sort by",
            "more expensive", "within", "under $", "less than",
        ]
        review_keywords = [
            "review", "reviews", "opinion", "opinions", "people say",
            "what do people", "what do customers", "what do others",
            "is the food good", "is the food nice", "is it good", "are they good",
            "how is", "how are", "how's the food",
            "worth it", "worth going", "worth visiting",
            "experience", "atmosphere", "vibe", "ambiance",
            "recommended", "thoughts",
        ]
        followup_info_keywords = [
            "what food", "what do they sell", "what do they serve", "what cuisine",
            "menu", "tell me more", "more about", "tell me about", "info about",
            "which one", "how far",
            "what time", "when do they", "opening hours", "average", "price",
            "popular dish", "signature dish", "best dish", "must try",
            "first one", "second one", "third one", "the one", "compare",
        ]
        food_keywords = [
            "food", "eat", "hungry", "cuisine", "budget", "cheap",
            "near", "in ", "recommend", "suggest", "places", "options", "spot",
            "dinner", "lunch", "breakfast", "supper", "brunch", "meal",
            "affordable", "top rated", "best", "random", "halal", "vegetarian",
            "vegan", "break the bank", "pricey", "expensive", "premium",
            # food-type nouns so "ramen in tampines" etc. aren't classified conversational
            "ramen", "sushi", "pho", "pizza", "burger", "steak", "noodle",
            "rice", "curry", "bbq", "hotpot", "dim sum", "pasta", "cafe",
            "restaurant", "eatery", "kopitiam", "hawker",
        ]
        if has_last_candidates and any(kw in msg_lower for kw in followup_filter_keywords):
            return "followup_filter"
        # Review / info checks run regardless of whether we have prior candidates so that
        # "Reviews about Ramen Taisho" (no prior context) routes to web-search, not RAG.
        if any(kw in msg_lower for kw in review_keywords):
            return "followup_review"
        if any(kw in msg_lower for kw in followup_info_keywords):
            return "followup_info"
        if any(kw in msg_lower for kw in food_keywords):
            return "restaurant_search"
        return "conversational"

    try:
        resp = model.generate_content(prompt)
        answer = (getattr(resp, "text", "") or "").strip().lower()

        # ── Post-LLM override for ambiguous cases ────────────────────────
        # "above 4.5 stars review" contains both "review" and a rating
        # threshold — the filter intent should win.
        if "followup_review" in answer:
            rating_threshold = re.search(
                r"(?:above|over|at\s+least|minimum|more\s+than|higher\s+than)"
                r"\s+\d+(?:\.\d+)?\s*(?:star|rating|⭐)?",
                msg_lower,
            )
            if rating_threshold and has_last_candidates:
                logger.info("Post-LLM override: followup_review → followup_filter (rating threshold detected)")
                return "followup_filter"

        if "followup_filter" in answer:
            return "followup_filter"
        if "followup_info" in answer:
            return "followup_info"
        if "followup_review" in answer:
            return "followup_review"
        if "restaurant_search" in answer or "search" in answer:
            return "restaurant_search"
        if "conversational" in answer:
            return "conversational"
        # LLM returned something unrecognised — fall back to keywords
        logger.info("Classifier returned unrecognised: %r — using keyword fallback", answer)
        return _keyword_fallback()
    except Exception as e:
        logger.warning("Intent classification LLM failed: %s — using keyword fallback", e)
        return _keyword_fallback()


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
    """
    Generate a response about reviews/opinions for restaurants.
    Uses Google Places API reviews when available, falls back to
    web search grounding (Gemini + Google Search) when not.
    """
    history_text = ""
    if conversation_history:
        for turn in conversation_history[-10:]:
            role = "User" if turn["role"] == "user" else "Assistant"
            history_text += f"{role}: {turn['content']}\n"

    restaurant_names = ", ".join(c.get("name", "") for c in candidates[:5] if c.get("name"))

    # ── Path 1: We have real Google Places reviews ────────────────────────
    if reviews_context:
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
            text = (getattr(resp, "text", "") or "").strip()
            if text:
                return text
        except Exception as e:
            logger.warning("Review response (Places API) LLM call failed: %s", e)

    # ── Path 2: No Places reviews — use web search grounding ─────────────
    names_for_search = restaurant_names or user_message
    search_prompt = f"""You are FoodKakiBot, a helpful food recommendation assistant for Singapore.

The user is asking: "{user_message}"

Restaurants in question: {names_for_search}

--- CONVERSATION HISTORY ---
{history_text.strip() or "(none)"}

Instructions:
- Search the internet for recent customer reviews and opinions about these restaurants.
- Summarise what reviewers and customers are saying — food quality, service, value for money, atmosphere.
- Be honest about mixed or negative reviews if they exist.
- If information comes from online sources rather than direct data, say so naturally (e.g. "based on online reviews", "reviewers mention").
- Keep your response friendly and conversational (3-6 sentences).
- Do NOT say you cannot find information — search for it.
"""
    grounded = _gemini_search_grounded(search_prompt)
    if grounded:
        logger.info("Review response answered via web search grounding")
        return grounded

    # ── Path 3: Everything failed ─────────────────────────────────────────
    return (
        "I wasn't able to pull up reviews right now. "
        "Try checking their Google Maps pages for the latest customer opinions!"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Follow-up info response (non-review questions about previously shown places)
# ─────────────────────────────────────────────────────────────────────────────

_PRICE_LEVEL_DESCRIPTIONS = {
    0: "Free",
    1: "Budget (roughly S$5–S$15 per person)",
    2: "Mid-Range (roughly S$15–S$35 per person)",
    3: "Expensive (roughly S$35–S$70 per person)",
    4: "Premium / Fine Dining (roughly S$70+ per person)",
}


def _gemini_search_grounded(prompt: str) -> str | None:
    """
    Call the Gemini REST API with Google Search grounding enabled.
    Returns the generated text, or None on failure.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-2.5-flash-lite:generateContent"
        f"?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        data = resp.json()
        candidates = data.get("candidates")
        if not candidates:
            logger.warning("Search-grounded call returned no candidates: %s",
                           data.get("error", {}).get("message", "unknown"))
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        text = " ".join(p.get("text", "") for p in parts if p.get("text")).strip()
        return text or None
    except Exception as e:
        logger.warning("Search-grounded Gemini call failed: %s", e)
        return None


def _build_restaurant_context_block(candidates: list[dict]) -> str:
    """Build a text block describing restaurant data for LLM context."""
    parts = []
    for i, c in enumerate(candidates[:10], 1):
        tags = c.get("tags") or []
        summary = c.get("editorial_summary") or ""
        if isinstance(summary, dict):
            summary = summary.get("text") or summary.get("overview") or ""
        distance = c.get("distance_km")
        distance_str = f"{distance:.1f} km away" if distance else "distance unknown"
        opening_hours = c.get("opening_hours") or "unknown"
        price_level = c.get("price_level")
        price_str = (
            _PRICE_LEVEL_DESCRIPTIONS.get(price_level, "unknown")
            if price_level is not None else "unknown"
        )
        gmaps = c.get("gmaps_uri") or ""

        parts.append(
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
    return "\n\n".join(parts)


def generate_followup_info_response(
    user_message: str,
    candidates: list[dict],
    conversation_history: list,
) -> str:
    """
    Answer a factual / analytical question using data from previously shown
    restaurants AND web search.  Works in two modes:

    1. With candidates: uses restaurant data as primary source, supplements
       with web search when data is insufficient (e.g. popular dishes).
    2. Without candidates: answers via web search directly (e.g. user asks
       about a specific restaurant by name without prior context).

    Never tells the user that data is "not in the database" — instead
    searches the web or estimates, disclosing when information is approximate.
    """
    history_text = ""
    if conversation_history:
        for turn in conversation_history[-10:]:
            role = "User" if turn["role"] == "user" else "Assistant"
            history_text += f"{role}: {turn['content']}\n"

    has_context = bool(candidates)
    restaurants_block = _build_restaurant_context_block(candidates) if has_context else ""

    # ── Build the search-grounded prompt ─────────────────────────────────
    if has_context:
        grounded_prompt = f"""You are FoodKakiBot, a helpful food recommendation assistant for Singapore.

The user was previously shown these restaurants:

{restaurants_block}

--- CONVERSATION HISTORY ---
{history_text.strip() or "(none)"}

The user is now asking: "{user_message}"

Instructions:
- Use the restaurant data above as your primary source.
- If the data above is not sufficient to fully answer (e.g. popular dishes, specific menu items, detailed pricing), search the internet for the relevant information.
- If they ask about price or average cost, the price levels correspond to typical Singapore per-person spending: Budget ≈ S$5–15, Mid-Range ≈ S$15–35, Expensive ≈ S$35–70, Premium ≈ S$70+. Give rough SGD estimates based on the restaurant type, cuisine, and price level.
- If information is estimated or sourced from general knowledge rather than exact data, say so naturally (e.g. "roughly", "typically around", "based on online sources").
- NEVER say "I don't have that information" or "not in my database" — always search the internet or estimate instead.
- Be concise and conversational (3-6 sentences).
"""
    else:
        # No prior candidates — answer purely via web search
        grounded_prompt = f"""You are FoodKakiBot, a helpful food recommendation assistant for Singapore.

--- CONVERSATION HISTORY ---
{history_text.strip() or "(none)"}

The user is asking: "{user_message}"

Instructions:
- Search the internet for relevant, up-to-date information to answer the user's question.
- For restaurant-specific questions (popular dishes, menu, prices, opening hours), search for that specific restaurant in Singapore.
- For general food questions, use your knowledge of Singapore's dining scene.
- If they ask about price, typical Singapore per-person spending ranges are: Budget ≈ S$5–15, Mid-Range ≈ S$15–35, Expensive ≈ S$35–70, Premium ≈ S$70+.
- If information is estimated or sourced from general knowledge, say so naturally (e.g. "roughly", "typically around", "based on online sources").
- NEVER say "I don't have that information" — always search or estimate instead.
- Be concise and conversational (3-6 sentences).
"""

    grounded_text = _gemini_search_grounded(grounded_prompt)
    if grounded_text:
        logger.info("followup_info answered with search grounding (has_context=%s)", has_context)
        return grounded_text

    # ── Fallback: regular LLM without grounding ──────────────────────────
    logger.info("followup_info falling back to non-grounded LLM (has_context=%s)", has_context)

    if has_context:
        fallback_prompt = f"""You are FoodKakiBot, a helpful food recommendation assistant for Singapore.

The user was previously shown these restaurants:

{restaurants_block}

--- CONVERSATION HISTORY ---
{history_text.strip() or "(none)"}

The user is now asking: "{user_message}"

Instructions:
- Answer their question using the restaurant data above as primary source.
- If they ask about food/cuisine, use the tags and description to answer.
- If they ask about distance or nearer options, compare the distances and highlight the closest ones.
- If they ask about price or average cost, use the price level information above. The price levels correspond to typical Singapore per-person spending: Budget ≈ S$5–15, Mid-Range ≈ S$15–35, Expensive ≈ S$35–70, Premium ≈ S$70+. Give rough SGD estimates where possible — it's okay to estimate based on the restaurant type, cuisine, and price level.
- If they ask about hours, use the opening hours data.
- You may supplement with your general knowledge about these restaurants or Singapore dining if the data above is incomplete — but clearly indicate when you are estimating (e.g. "roughly", "typically around").
- NEVER say "I don't have that information" or "not in my database" — always estimate or use general knowledge instead.
- Be concise and conversational (3-6 sentences).
"""
    else:
        fallback_prompt = f"""You are FoodKakiBot, a helpful food recommendation assistant for Singapore.

--- CONVERSATION HISTORY ---
{history_text.strip() or "(none)"}

The user is asking: "{user_message}"

Instructions:
- Answer the question using your general knowledge about Singapore restaurants and dining.
- For specific restaurants, share what you know about their cuisine, popular dishes, price range, or atmosphere.
- If they ask about price, typical Singapore per-person spending ranges are: Budget ≈ S$5–15, Mid-Range ≈ S$15–35, Expensive ≈ S$35–70, Premium ≈ S$70+.
- Clearly indicate when you are estimating (e.g. "roughly", "typically around", "generally known for").
- NEVER say "I don't have that information" — always provide your best estimate or general knowledge.
- Be concise and conversational (3-6 sentences).
"""

    try:
        resp = model.generate_content(fallback_prompt)
        return (getattr(resp, "text", "") or "").strip()
    except Exception as e:
        logger.error("Follow-up info response failed: %s", e)
        return (
            "Hmm, I'm having a bit of trouble looking that up right now. "
            "You could try checking their Google Maps page for the latest details!"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Conversational response
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Follow-up filter: refine/sort cached candidates based on user's request
# ─────────────────────────────────────────────────────────────────────────────

def apply_followup_filter(
    user_message: str,
    candidates: list[dict],
    *,
    user_lat: float | None = None,
    user_lng: float | None = None,
) -> list[dict]:
    """
    Parse filter / sort criteria from a follow-up message and apply them
    to the cached candidates list.  Returns a (possibly smaller / reordered)
    list of candidates.

    Supported operations:
      • min rating     – "above 4.5 stars", "at least 4 star", "over 4"
      • max price      – "under $20", "cheap", "budget only"
      • price level    – "only budget", "mid-range", "not expensive"
      • cuisine filter – "only halal", "just japanese"
      • tag filter     – "only vegetarian", "with outdoor seating"
      • sort: distance – "nearer", "closer", "nearest"
      • sort: rating   – "highest rated", "sort by rating"
      • sort: price    – "cheapest", "sort by price"
    """
    msg = (user_message or "").lower()
    filtered = list(candidates)  # shallow copy

    # ── Min-rating filter ────────────────────────────────────────────────────
    rating_match = re.search(
        r"(?:above|over|at\s+least|minimum|min|more\s+than|higher\s+than)\s*"
        r"(\d+(?:\.\d+)?)\s*(?:star|rating|⭐)?",
        msg,
    )
    if rating_match:
        min_rating = float(rating_match.group(1))
        filtered = [c for c in filtered if (c.get("rating") or 0) >= min_rating]

    # ── Price-level filter ───────────────────────────────────────────────────
    # "only budget" / "cheaper options" / "not expensive"
    price_tag = detect_canonical_price_tag(user_message)
    if price_tag:
        target_level = PRICE_TAG_TO_LEVEL.get(price_tag, 99)
        # "cheaper" / "budget" → keep things at-or-below that level
        if any(kw in msg for kw in ("cheaper", "cheapest", "budget", "affordable", "under")):
            filtered = [
                c for c in filtered
                if (c.get("price_level") or 99) <= target_level
            ]
        # "more expensive" / "premium" → keep things at-or-above
        elif any(kw in msg for kw in ("more expensive", "pricier", "premium", "upscale")):
            filtered = [
                c for c in filtered
                if (c.get("price_level") or 0) >= target_level
            ]
        else:
            # exact match
            filtered = [
                c for c in filtered
                if (c.get("price_level") or 99) <= target_level
            ]

    # ── Cuisine / tag substring filter ───────────────────────────────────────
    # "only halal", "just japanese", "show me the korean ones"
    only_match = re.search(
        r"(?:only|just|show\s+(?:me\s+)?(?:the\s+)?|filter\s+(?:by|to)\s*)"
        r"([a-z\s\-/]+?)(?:\s+(?:ones?|places?|restaurants?|options?|food))?$",
        msg,
    )
    if only_match:
        filter_term = only_match.group(1).strip()
        # Check if it maps to a known cuisine tag
        matched_cuisine = None
        for ctag in CUISINE_TAGS:
            if ctag.lower() == filter_term or filter_term in ctag.lower():
                matched_cuisine = ctag
                break
        if not matched_cuisine:
            matched_cuisine = CUISINE_ALIASES.get(filter_term)

        if matched_cuisine:
            filtered = [
                c for c in filtered
                if matched_cuisine in (c.get("tags") or [])
            ]

    # ── Distance filter: "within X km" ───────────────────────────────────────
    dist_match = re.search(r"within\s+(\d+(?:\.\d+)?)\s*km", msg)
    if dist_match:
        max_km = float(dist_match.group(1))
        filtered = [
            c for c in filtered
            if c.get("distance_km") is not None and c["distance_km"] <= max_km
        ]

    # ── Sorting ──────────────────────────────────────────────────────────────
    sort_by_distance = any(kw in msg for kw in (
        "nearer", "nearest", "closer", "closest", "nearby", "by distance",
    ))
    sort_by_rating = any(kw in msg for kw in (
        "highest rated", "best rated", "top rated", "sort by rating",
        "by rating", "higher rated",
    ))
    sort_by_price = any(kw in msg for kw in (
        "cheapest", "sort by price", "by price", "lowest price",
    ))

    if sort_by_distance:
        # Re-compute distance if we have GPS but candidates lack it
        if user_lat is not None and user_lng is not None:
            for c in filtered:
                lat = c.get("latitude")
                lng = c.get("longitude")
                if lat is not None and lng is not None and c.get("distance_km") is None:
                    c["distance_km"] = round(haversine_km(user_lat, user_lng, float(lat), float(lng)), 2)
        filtered = sorted(
            filtered,
            key=lambda c: c.get("distance_km") if c.get("distance_km") is not None else 9999,
        )
    elif sort_by_rating:
        filtered = sorted(
            filtered,
            key=lambda c: (c.get("rating") or 0, get_review_count(c)),
            reverse=True,
        )
    elif sort_by_price:
        filtered = sorted(
            filtered,
            key=lambda c: c.get("price_level") if c.get("price_level") is not None else 99,
        )

    return filtered


def generate_followup_filter_response(
    user_message: str,
    original_count: int,
    filtered_candidates: list[dict],
    conversation_history: list,
) -> str:
    """Generate a short text summary describing the filtered/sorted results."""
    history_text = ""
    if conversation_history:
        for turn in conversation_history[-6:]:
            role = "User" if turn["role"] == "user" else "Assistant"
            history_text += f"{role}: {turn['content']}\n"

    names = [c.get("name", "Unknown") for c in filtered_candidates[:10]]
    names_str = ", ".join(names)

    prompt = f"""You are FoodKakiBot, a helpful food recommendation assistant for Singapore.

The user previously saw {original_count} restaurant recommendations.
They just asked: "{user_message}"

After filtering/sorting, {len(filtered_candidates)} restaurants matched.
The top results are: {names_str}

--- CONVERSATION HISTORY ---
{history_text.strip() or "(none)"}

Instructions:
- Write a SHORT (1-3 sentences) friendly summary of the filtered results.
- Mention how many results matched and what filter was applied.
- If zero results matched, say so and suggest broadening their criteria.
- Do NOT list the restaurants by name — the cards will show them.
- Be concise and conversational.
"""

    try:
        resp = model.generate_content(prompt)
        return (getattr(resp, "text", "") or "").strip()
    except Exception as e:
        logger.error("Followup filter response generation failed: %s", e)
        if not filtered_candidates:
            return (
                f"None of the {original_count} restaurants matched that filter. "
                "Try broadening your criteria!"
            )
        return f"Here are {len(filtered_candidates)} results after applying your filter."


# ─────────────────────────────────────────────────────────────────────────────
# Shared helper: build UI-ready restaurant list from candidates
# ─────────────────────────────────────────────────────────────────────────────

def candidates_to_ui(candidates: list[dict], *, top_n: int = 5) -> list[dict]:
    """Convert internal candidate dicts to the frontend restaurant card format."""
    restaurants_for_ui = []
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

        # Fall back to rule-based description when DB has nothing
        if not description.strip():
            description = build_description_from_data(c)

        candidate_tags = c.get("tags") or []
        # Send only cuisine/type tags — filter out location, budget, and meta tags
        food_tags = [
            t for t in candidate_tags
            if t not in NON_LOCATION_TAGS or t in CUISINE_TAGS
        ]
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
            "tags":                food_tags,
        })
    return restaurants_for_ui


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

_PRICE_LABELS = {1: "budget", 2: "mid-range", 3: "upscale", 4: "premium"}

# Tags that describe the venue type rather than the cuisine
_VENUE_TAGS = {"Cafe", "Bar", "Bakery", "Food Court", "Buffet", "Fast Food",
               "Diner", "Tea House", "Juice Bar"}


def build_description_from_data(candidate: dict) -> str:
    """
    Construct a short one-liner description from a candidate's available data
    (tags, price level, rating) — no LLM call required.
    """
    tags: list[str] = candidate.get("tags") or []
    price_level: int | None = candidate.get("price_level")
    rating: float | None = candidate.get("rating")

    cuisine_tags  = [t for t in tags if t in CUISINE_TAGS and t not in _VENUE_TAGS]
    venue_tags    = [t for t in tags if t in _VENUE_TAGS]

    parts: list[str] = []

    # Price modifier
    price_word = _PRICE_LABELS.get(price_level, "") if price_level else ""

    # Determine lead noun (venue type or generic "restaurant")
    if venue_tags:
        lead = venue_tags[0].lower()          # e.g. "cafe", "bakery"
    else:
        lead = "restaurant"

    # Build "A [price] [cuisine] [lead]"
    descriptor_parts = []
    if price_word:
        descriptor_parts.append(price_word)
    if cuisine_tags:
        descriptor_parts.append(cuisine_tags[0].lower())
    descriptor_parts.append(lead)

    sentence = "A " + " ".join(descriptor_parts)

    # Add secondary cuisine if present
    if len(cuisine_tags) > 1:
        sentence += f" serving {cuisine_tags[1].lower()} food"

    sentence += "."

    # Append a rating note if highly rated
    if rating and rating >= 4.5:
        sentence += f" Highly rated at {rating}/5."
    elif rating and rating >= 4.0:
        sentence += f" Well-rated at {rating}/5."

    return sentence


@app.route("/api/chat", methods=["POST"])
def chat():
    data         = request.json
    user_message = data.get("message")
    session_id   = data.get("session_id")

    # Extract optional GPS coordinates sent by the frontend
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
        if intent == "followup_review":
            candidates = session.get("last_candidates") or []

            # Try to get Google Places reviews when we have cached candidates
            reviews_context = ""
            if candidates:
                gmaps_id_map  = fetch_gmaps_place_ids(candidates)
                reviews_context = build_reviews_context(candidates, gmaps_id_map)

            # generate_review_response handles both cases:
            #   - reviews_context present → summarise real reviews
            #   - reviews_context empty   → web search grounding fallback
            reply = generate_review_response(
                user_message=user_message,
                reviews_context=reviews_context,
                candidates=candidates,
                conversation_history=history_for_intent,
            )

            session["history"].append({
                "role": "assistant", "content": reply,
                "timestamp": datetime.now().isoformat(),
            })
            return jsonify({"response": reply, "restaurants": []})

        # ── Path B2: Follow-up info question (non-review) ────────────────────
        if intent == "followup_info":
            candidates = session.get("last_candidates") or []
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

        # ── Path B3: Follow-up filter (refine/sort previous results) ─────────
        if intent == "followup_filter" and has_last_candidates:
            cached = session["last_candidates"]
            original_count = len(cached)

            filtered = apply_followup_filter(
                user_message,
                cached,
                user_lat=gps_lat,
                user_lng=gps_lng,
            )

            # Re-number ranking reasons for the filtered set
            for idx, c in enumerate(filtered, start=1):
                resolved_tags = session.get("tags") or {}
                c["recommended_because"] = build_candidate_reason(c, resolved_tags, has_gps=has_gps)
                c["rank_reason"] = build_rank_reason(c, idx, resolved_tags, has_gps=has_gps)

            reply = generate_followup_filter_response(
                user_message=user_message,
                original_count=original_count,
                filtered_candidates=filtered,
                conversation_history=history_for_intent,
            )

            # Update cached candidates so further follow-ups chain correctly
            if filtered:
                session["last_candidates"] = filtered

            session["history"].append({
                "role": "assistant", "content": reply,
                "timestamp": datetime.now().isoformat(),
            })

            restaurants_for_ui = candidates_to_ui(filtered, top_n=10)

            return jsonify({
                "response": reply,
                "restaurants": restaurants_for_ui,
                "active_tags": {k: v for k, v in (session.get("tags") or {}).items() if v},
                "debug": {
                    "intent": "followup_filter",
                    "original_candidates": original_count,
                    "filtered_candidates": len(filtered),
                },
            })


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
                        "id, name, address, gmaps_uri, editorial_summary, rating, opening_hours, price_level, photo_url, latitude, longitude"
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
                            "id, name, address, gmaps_uri, editorial_summary, rating, opening_hours, price_level, photo_url, latitude, longitude"
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
        # When only a location is given (no budget/cuisine) and no GPS distance
        # context, ranking by composite score is nearly uniform — sort by rating
        # first so the best-rated places always rise to the top.
        if location_only_mode and not latlng:
            candidates = sort_best_rated(candidates)

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

        # Cache candidates so follow-up questions can operate on them
        session["last_candidates"] = candidates

        top_n = 10 if broad_reco_mode else 5
        restaurants_for_ui = candidates_to_ui(candidates, top_n=top_n)

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

@app.route("/api/tags", methods=["GET"])
def get_tags():
    catalog = get_tag_catalog()
    return jsonify({
        "cuisines": catalog["cuisines"],
        "budgets":  catalog["budgets"],
        "locations": catalog["locations"][:60],  # cap to avoid huge payload
    })


@app.route("/api/session/tags", methods=["POST"])
def set_session_tags():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "missing session_id"}), 400
    session = get_session(session_id)
    for key in ("cuisine", "location", "budget"):
        if key in data:
            session["tags"][key] = data[key] or None  # None clears the tag
    return jsonify({"active_tags": {k: v for k, v in session["tags"].items() if v}})


@app.route("/api/session", methods=["POST"])
def create_session():
    try:
        session_id = str(uuid.uuid4())
        get_session(session_id)  # initialise session state
        return jsonify({"session_id": session_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "active_sessions": len(sessions),
        "gemini_configured": os.environ.get("GOOGLE_API_KEY") is not None,
        "places_key_src": (
            "GOOGLE_PLACES_API_KEY" if os.getenv("GOOGLE_PLACES_API_KEY")
            else "GOOGLE_MAPS_API_KEY" if os.getenv("GOOGLE_MAPS_API_KEY")
            else "GOOGLE_API_KEY (fallback — may lack Places API)"
        ),
    })


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
