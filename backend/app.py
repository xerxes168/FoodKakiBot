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

from flask import Flask, request, jsonify, redirect
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
import difflib
import json
import logging

from tagging import auto_tags_from_google

# ── RAG module ────────────────────────────────────────────────────────────────
from rag import (
    retrieve_hybrid,
    build_rag_context,
    generate_grounded_response,
    fetch_place_tags_map_rag,
)

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
PLACES_KEY   = os.getenv("GOOGLE_API_KEY")
supabase     = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Per-session state (keyed by session_id, never shared between sessions) ────
# Each session stores:
#   history        : full conversation turns [{role, content, timestamp}]
#   tags           : last resolved {cuisine, location, budget} — carried forward
#   last_candidates: restaurants shown in the previous turn (for follow-ups)
sessions: dict[str, dict] = {}

def get_session(session_id: str) -> dict:
    """Return session state, creating it if new. Sessions are fully isolated."""
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
# Tag utilities (unchanged from original)
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
# Haversine (kept for distance-based endpoints)
# ─────────────────────────────────────────────────────────────────────────────

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi   = math.radians(lat2 - lat1)
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
            params={"origins": f"{user_lat},{user_lng}", "destinations": destinations, "mode": "walking", "key": PLACES_KEY},
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

# ─────────────────────────────────────────────────────────────────────────────
# RAG-powered chat endpoint
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def chat():
    data         = request.json
    user_message = data.get("message")
    session_id   = data.get("session_id")

    if not user_message or not session_id:
        return jsonify({"error": "Missing message or session_id"}), 400

    try:
        # ── 1. Load isolated session state ────────────────────────────────────
        session = get_session(session_id)

        session["history"].append({
            "role": "user",
            "content": user_message,
            "timestamp": datetime.now().isoformat(),
        })

        # ── 2. Extract tags from THIS message only ────────────────────────────
        matched_tags   = extract_tags_from_message(user_message)
        current_tags   = classify_required_tags(matched_tags)

        # LLM fills slots still missing after rule-based extraction.
        # New rule: Location is required, and it must be paired with either budget or cuisine.
        needs_location = current_tags.get("location") is None
        needs_pair = (current_tags.get("budget") is None and current_tags.get("cuisine") is None)
        if needs_location or needs_pair:
            llm_selected = llm_extract_required_tags(user_message, current_tags)
            current_tags = merge_selected_tags(current_tags, llm_selected)

        # ── 3. Merge with remembered tags from earlier in this session ────────
        # Current message wins (allows "actually make it Korean" to override).
        # Remembered tags fill slots the current message didn't mention.
        remembered = session["tags"]
        resolved = {
            "cuisine":  current_tags.get("cuisine")  or remembered.get("cuisine"),
            "location": current_tags.get("location") or remembered.get("location"),
            "budget":   current_tags.get("budget")   or remembered.get("budget"),
        }

        # Persist the freshly resolved state back into this session only
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

        # ── 4. Check if we have enough info ──────────────────────────────────
        has_location = bool(resolved.get("location"))
        has_budget   = bool(resolved.get("budget"))
        has_cuisine  = bool(resolved.get("cuisine"))

        missing_response: str | None = None
        missing_reason: str | None = None

        # Enforce: location required
        if not has_location:
            missing_reason = "location"
            # Show any known non-location fields
            known_parts = [f"{k}: {resolved[k]}" for k in ("budget", "cuisine") if resolved.get(k)]
            known_str = (f" (I already know: {', '.join(known_parts)}.)" if known_parts else "")
            missing_response = (
                f"To find you the perfect restaurant, I still need your location.{known_str} "
                f"For example: 'cheap food in Tampines' or 'Japanese food in Tampines'."
            )

        # Enforce: location must be paired with either budget OR cuisine
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
                "role": "assistant",
                "content": missing_response,
                "timestamp": datetime.now().isoformat(),
            })
            return jsonify({
                "response": missing_response,
                "debug": {"required_tags": required_tags, "missing": missing_reason, "resolved": resolved},
            })

        # ── 5. Hybrid RAG retrieval ───────────────────────────────────────────
        candidates, strategy = retrieve_hybrid(
            user_message,
            limit=15,
            location_tags=[resolved.get("location")] if resolved.get("location") else [],
            budget_tags=[resolved.get("budget")] if resolved.get("budget") else [],
            cuisine_tags=[resolved.get("cuisine")] if resolved.get("cuisine") else [],
        )

        logger.info("Retrieval strategy: %s | candidates: %d", strategy, len(candidates))

        # Remember what we showed for potential follow-ups ("tell me more about #2")
        session["last_candidates"] = candidates

        # ── Distance rerank (nearest first) ──────────────────────────────
        user_lat = data.get("lat")
        user_lng = data.get("lng")
        latlng = None
        if user_lat is not None and user_lng is not None:
            try:
                latlng = (float(user_lat), float(user_lng))
            except (TypeError, ValueError):
                latlng = None

        if latlng:
            candidates = rerank_candidates_by_distance(candidates, latlng[0], latlng[1])
            logger.info("Distance rerank enabled (%s,%s)", latlng[0], latlng[1])
        else:
            user_loc = resolved.get("location")
            fallback = geocode_location_to_latlng(user_loc)
            if fallback:
                candidates = rerank_candidates_by_distance(candidates, fallback[0], fallback[1])
                logger.info("Distance rerank via geocode fallback: %s", user_loc)
            else:
                logger.info("Distance rerank skipped (no lat/lng)")

        # ── 6. Fetch tags and build grounded context ──────────────────────────
        place_ids = [p.get("id") for p in candidates if p.get("id") is not None]
        tags_map  = fetch_place_tags_map_rag(place_ids, supabase) if place_ids else {}
        context   = build_rag_context(candidates[:10], tags_map)

        # ── 7. Grounded generation with full session history ──────────────────
        # Pass history excluding the current turn (already in user_message)
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

        # ── 8. Persist assistant turn and return ──────────────────────────────
        session["history"].append({
            "role": "assistant",
            "content": assistant_message,
            "timestamp": datetime.now().isoformat(),
        })
        restaurants_for_ui = []
        for c in candidates[:15]:
            cname = c.get("name", "")
            if cname and cname.lower() in assistant_message.lower():
                raw_summary = c.get("editorial_summary") or ""
                description = ""
                if isinstance(raw_summary, dict):
                    description = raw_summary.get("overview", "")
                elif isinstance(raw_summary, str):
                    try:
                        parsed = json.loads(raw_summary)
                        description = parsed.get("overview", raw_summary)
                    except Exception:
                        description = raw_summary
                else:
                    description = ""

            restaurants_for_ui.append({
                "name":        cname,
                "description": description,
                "address":     c.get("address", ""),
                "maps_url":    c.get("gmaps_uri") or "",
                "photo_url":   c.get("photo_url") or "",
                "rating":        c.get("rating"),
                "opening_hours": c.get("opening_hours"),   
            })


        return jsonify({
            "response": assistant_message,
            "restaurants": restaurants_for_ui,
            "debug": {
                "required_tags":      required_tags,
                "retrieval_strategy": strategy,
                "candidates_found":   len(candidates),
                "resolved_tags":      resolved,
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
# Existing auxiliary endpoints (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/session", methods=["POST"])
def create_session():
    try:
        session_id = str(uuid.uuid4())
        get_session(session_id)   # initialise isolated state
        return jsonify({"session_id": session_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "active_sessions": len(sessions),
        "gemini_configured": os.environ.get("GOOGLE_API_KEY") is not None,
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
        photos   = details.get("photos") or []
        photo    = google_photo_url(photos[0]["photo_reference"]) if photos else None
        enriched.append({
            "name":       details.get("name"),
            "address":    details.get("formatted_address"),
            "rating":     details.get("rating"),
            "price_level": details.get("price_level"),
            "open_now":   (details.get("opening_hours") or {}).get("open_now"),
            "maps_url":   details.get("url"),
            "photo_url":  photo,
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
        "place_id":   place_id,
        "name":       details.get("name"),
        "address":    details.get("formatted_address"),
        "price_level": details.get("price_level"),
        "open_now":   (details.get("opening_hours") or {}).get("open_now"),
        "types":      details.get("types"),
        "auto_tags":  tags,
    })


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("FoodKakiBot Backend  —  RAG Edition")
    print("=" * 55)
    print("Server  : http://localhost:5000")
    print(f"Gemini  : {'configured' if os.environ.get('GOOGLE_API_KEY') else 'NOT configured'}")
    print(f"Supabase: {'configured' if SUPABASE_URL else 'NOT configured'}")
    print("RAG     : enabled (pgvector + grounded generation)")
    print("=" * 55)
    app.run(debug=True, port=5000)