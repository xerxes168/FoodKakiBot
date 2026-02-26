from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import google.generativeai as genai
from datetime import datetime
import uuid
from supabase import create_client
from dotenv import load_dotenv
import requests
from tagging import auto_tags_from_google
import re
import math
import difflib
import json

app = Flask(__name__)
CORS(app)
load_dotenv()

# Configure Gemini API
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-flash-lite')

# Configure Supabase Client
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY")
PLACES_KEY = os.getenv("GOOGLE_API_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# In-memory storage for conversations (temporary - no database)
conversations = {}

PRICE_LEVEL_TO_TAG = {
    0: "Free",
    1: "Budget",
    2: "Mid-Range",
    3: "Expensive",
    4: "Premium",
}

PRICE_TAG_TO_LEVEL = {v: k for k, v in PRICE_LEVEL_TO_TAG.items()}

PRICE_TAG_ALIASES = {
    "Free": ["free"],
    "Budget": ["cheap", "budget", "affordable", "economical", "low cost", "low-cost"],
    "Mid-Range": ["mid range", "mid-range", "moderate", "reasonably priced", "not too expensive"],
    "Expensive": ["expensive", "pricey", "high price", "high-priced"],
    "Premium": ["premium", "luxury", "high end", "high-end", "fine dining", "very expensive"],
}

IGNORED_QUERY_TAGS = {
    "Restaurant",
}

BUDGET_TAGS = set(PRICE_LEVEL_TO_TAG.values())

# Food-type tags that should count as the "cuisine" slot in strict tag mode.
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

# Tags that should not be treated as the required "location" slot.
NON_LOCATION_TAGS = (
    BUDGET_TAGS
    | CUISINE_TAGS
    | {
        "Delivery", "Dine-In", "Takeaway", "Reservable", "Family-Friendly", "Good for Groups",
        "Outdoor Seating", "In Mall", "Food Court", "Live Music", "Museum", "Park",
        "Nightclub", "Indoor Playground", "Playground", "Restaurant",
    }
)

def normalize_text_for_match(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("-", " ")
    text = re.sub(r"[^a-z0-9$\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def contains_phrase(normalized_text: str, phrase: str) -> bool:
    p = normalize_text_for_match(phrase)
    if not p:
        return False
    return f" {p} " in f" {normalized_text} "

def detect_canonical_price_tag(message: str) -> str | None:
    raw = (message or "").lower()
    normalized = normalize_text_for_match(message or "")

    # Prefer explicit dollar notation, longest first.
    dollar_map = [
        ("$$$$", "Premium"),
        ("$$$", "Expensive"),
        ("$$", "Mid-Range"),
        ("$", "Budget"),
    ]
    for symbol, canonical in dollar_map:
        if re.search(rf"(?<!\$){re.escape(symbol)}(?!\$)", raw):
            return canonical

    # Then match common natural-language variants.
    for canonical, aliases in PRICE_TAG_ALIASES.items():
        if any(contains_phrase(normalized, alias) for alias in aliases):
            return canonical

    return None

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in km

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c

def get_all_tag_names():
    response = supabase.table("tags").select("name").execute()
    tags = [t["name"] for t in response.data]
    return tags, {t.lower() for t in tags}

def extract_tags_from_message(user_message):
    tag_names, _ = get_all_tag_names()
    normalized_user_text = normalize_text_for_match(user_message or "")
    tag_lookup = {t.lower(): t for t in tag_names}

    # Phrase-based matching reduces accidental partial matches.
    matched_tags = []
    for tag in sorted(tag_names, key=len, reverse=True):
        if tag in IGNORED_QUERY_TAGS:
            continue
        if contains_phrase(normalized_user_text, tag):
            matched_tags.append(tag)

    # Map "$", "$$", "mid range", etc. -> one canonical price tag if present in DB.
    canonical_price_tag = detect_canonical_price_tag(user_message or "")
    if canonical_price_tag:
        actual_tag = tag_lookup.get(canonical_price_tag.lower())
        if actual_tag and actual_tag not in matched_tags:
            matched_tags.append(actual_tag)

    return matched_tags

def classify_required_tags(matched_tags):
    """Pick one tag for each required category: cuisine, location, budget."""
    selected = {"cuisine": None, "location": None, "budget": None}

    for tag in matched_tags:
        if selected["budget"] is None and tag in BUDGET_TAGS:
            selected["budget"] = tag
            continue
        if selected["cuisine"] is None and tag in CUISINE_TAGS:
            selected["cuisine"] = tag
            continue
        if selected["location"] is None and tag not in NON_LOCATION_TAGS:
            selected["location"] = tag
            continue

    return selected

def extract_location_phrase_from_message(user_message):
    text = (user_message or "").strip()
    if not text:
        return None

    m = re.search(r"\b(?:in|at|near)\s+([a-zA-Z0-9\s\-]+)", text, flags=re.IGNORECASE)
    if not m:
        return None

    phrase = m.group(1).strip(" .,!?:;")
    phrase = re.split(
        r"\b(?:with|for|under|budget|cheap|affordable|mid-range|mid range|expensive|premium)\b",
        phrase,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .,!?:;")
    return phrase or None

def get_location_tags_from_all_tags(tag_names):
    return sorted([t for t in tag_names if t not in NON_LOCATION_TAGS])

def suggest_location_tags(user_message, max_items=6):
    phrase = extract_location_phrase_from_message(user_message)
    if not phrase:
        return []

    tag_names, _ = get_all_tag_names()
    location_tags = get_location_tags_from_all_tags(tag_names)
    if not location_tags:
        return []

    return difflib.get_close_matches(phrase, location_tags, n=max_items, cutoff=0.0)

def parse_json_from_llm_text(text):
    text = (text or "").strip()
    if not text:
        return None

    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    candidate = fence_match.group(1).strip() if fence_match else text

    try:
        return json.loads(candidate)
    except Exception:
        pass

    # Fallback: try to extract the first JSON object.
    obj_match = re.search(r"\{[\s\S]*\}", text)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except Exception:
            return None
    return None

def get_tag_catalog():
    tag_names, _ = get_all_tag_names()
    budgets = sorted([t for t in tag_names if t in BUDGET_TAGS])
    cuisines = sorted([t for t in tag_names if t in CUISINE_TAGS])
    locations = get_location_tags_from_all_tags(tag_names)
    return {
        "all": tag_names,
        "budgets": budgets,
        "cuisines": cuisines,
        "locations": locations,
    }

def llm_extract_required_tags(user_message, current_selected=None):
    """Ask the LLM to map the query to exact DB tags, then validate strictly."""
    catalog = get_tag_catalog()
    current_selected = current_selected or {"cuisine": None, "location": None, "budget": None}

    prompt = f"""
You map a user food request into EXACT database tags.

Rules:
- Choose at most one tag for each category: cuisine, location, budget.
- Output ONLY JSON with keys: cuisine, location, budget.
- Values must be exact strings from the allowed lists below, or null.
- Do not invent tags.

User message:
{user_message}

Rule-based hints (may be incomplete):
{json.dumps(current_selected, ensure_ascii=True)}

Allowed cuisine tags:
{json.dumps(catalog["cuisines"], ensure_ascii=True)}

Allowed location tags:
{json.dumps(catalog["locations"], ensure_ascii=True)}

Allowed budget tags:
{json.dumps(catalog["budgets"], ensure_ascii=True)}
""".strip()

    try:
        response = model.generate_content(prompt)
        payload = parse_json_from_llm_text(getattr(response, "text", ""))
        if not isinstance(payload, dict):
            return None

        result = {"cuisine": None, "location": None, "budget": None}
        cuisine = payload.get("cuisine")
        location = payload.get("location")
        budget = payload.get("budget")

        if isinstance(cuisine, str) and cuisine in catalog["cuisines"]:
            result["cuisine"] = cuisine
        if isinstance(location, str) and location in catalog["locations"]:
            result["location"] = location
        if isinstance(budget, str) and budget in catalog["budgets"]:
            result["budget"] = budget
        return result
    except Exception as e:
        print("LLM tag extraction failed:", str(e))
        return None

def merge_selected_tags(rule_selected, llm_selected):
    merged = dict(rule_selected or {"cuisine": None, "location": None, "budget": None})
    if not llm_selected:
        return merged
    for key in ("cuisine", "location", "budget"):
        if merged.get(key) is None and llm_selected.get(key):
            merged[key] = llm_selected[key]
    return merged

def fetch_place_tags_map(place_ids):
    if not place_ids:
        return {}
    try:
        res = supabase.table("place_tags").select("place_id, tag_name").in_("place_id", place_ids).execute()
        tag_map = {}
        for row in (res.data or []):
            pid = row.get("place_id")
            tag_name = row.get("tag_name")
            if pid is None or not tag_name:
                continue
            tag_map.setdefault(pid, []).append(tag_name)
        for pid in tag_map:
            tag_map[pid] = sorted(set(tag_map[pid]))
        return tag_map
    except Exception as e:
        print("Failed to fetch place tags for ranking:", str(e))
        return {}

def llm_rank_recommendations(user_message, required_tags, candidate_places):
    """Rank already-validated DB candidates and return explanations. Never expands the candidate set."""
    if not candidate_places:
        return None

    candidate_payload = [
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "address": p.get("address"),
            "tags": p.get("tags", []),
        }
        for p in candidate_places
    ]

    prompt = f"""
You are ranking restaurant recommendations from a pre-filtered database result.
All candidates already match the required constraints.

Rules:
- Use ONLY the candidates provided.
- Do NOT invent restaurants.
- Return ONLY JSON with this shape:
  {{
    "ordered_ids": [id1, id2, id3],
    "reasons": {{
      "id1": "short reason",
      "id2": "short reason"
    }}
  }}
- `ordered_ids` must contain only ids from the provided candidates.
- Return up to 3 ids.
- Keep reasons short and grounded in the provided names/tags/address only.

User message:
{user_message}

Required tags:
{json.dumps(required_tags, ensure_ascii=True)}

Candidates:
{json.dumps(candidate_payload, ensure_ascii=True)}
""".strip()

    try:
        response = model.generate_content(prompt)
        payload = parse_json_from_llm_text(getattr(response, "text", ""))
        if not isinstance(payload, dict):
            return None

        valid_ids = {p.get("id") for p in candidate_places}
        ordered_ids = []
        for raw_id in (payload.get("ordered_ids") or []):
            normalized = raw_id
            if isinstance(raw_id, str) and raw_id.isdigit():
                normalized = int(raw_id)
            if normalized in valid_ids and normalized not in ordered_ids:
                ordered_ids.append(normalized)
            if len(ordered_ids) >= 3:
                break

        if not ordered_ids:
            return None

        reasons_payload = payload.get("reasons") or {}
        reasons = {}
        if isinstance(reasons_payload, dict):
            for k, v in reasons_payload.items():
                norm_k = int(k) if isinstance(k, str) and k.isdigit() else k
                if norm_k in valid_ids and isinstance(v, str):
                    reasons[norm_k] = v.strip()

        return {"ordered_ids": ordered_ids, "reasons": reasons}
    except Exception as e:
        print("LLM ranking failed:", str(e))
        return None

def fetch_food_places_by_tags(tags, limit=5):
    if not tags:
        return []

    place_id_sets = []

    for tag in tags:
        res = supabase.table("place_tags").select(
            "place_id, tags!inner(name)"
        ).eq("tags.name", tag).execute()
        place_id_sets.append({r["place_id"] for r in res.data})

    valid_place_ids = set.intersection(*place_id_sets) if place_id_sets else set()

    if not valid_place_ids:
        return []

    res = supabase.table("places").select(
        "id, name, address, gmaps_uri"
    ).in_("id", list(valid_place_ids)).limit(limit).execute()

    return res.data

def merge_place_results(*result_lists):
    """Merge lists of place dicts while deduplicating by stable restaurant identity."""
    merged = []
    seen = set()

    for result_list in result_lists:
        for place in (result_list or []):
            if not isinstance(place, dict):
                continue
            key = (
                place.get("id"),
                place.get("gmaps_uri"),
                place.get("name"),
                place.get("address"),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(place)

    return merged

def format_tag_only_response(matched_tags, food_results):
    if not matched_tags:
        return (
            "I couldn't match your request to database tags. "
            "Try exact tags like Dessert, Japanese, Budget, and an area tag like Rochor."
        )

    tag_list = ", ".join(matched_tags)
    if not food_results:
        return f"I couldn't find any restaurants in my database that match all these tags: {tag_list}."

    lines = [f"Here are restaurants that match all these tags: {tag_list}"]
    for f in food_results[:3]:
        lines.append(
            f"- {f['name']}\n"
            f"  Address: {f['address']}\n"
            f"  Google Maps: {f['gmaps_uri']}"
        )
    return "\n".join(lines)

def format_required_tag_response(selected_tags, food_results, user_message=None):
    missing = [k for k in ("cuisine", "location", "budget") if not selected_tags.get(k)]
    if missing:
        pretty = ", ".join(missing)
        msg = (
            "Sorry! I need at least 3 information before recommending a restaurant: cuisine, location and budget. "
            f"I'm missing a {pretty}."
        )
        if "location" in missing:
            location_phrase = extract_location_phrase_from_message(user_message or "")
            if location_phrase:
                msg += f" I couldn't match '{location_phrase}' to a location tag in the database."
        return msg

    required_tags = [
        selected_tags["cuisine"],
        selected_tags["location"],
        selected_tags["budget"],
    ]

    if not food_results:
        return "I couldn't find any restaurants in my database that match all 3 required tags: " + ", ".join(required_tags) + "."

    lines = ["Here are restaurants that match all 3 required tags: " + ", ".join(required_tags)]
    for f in food_results[:3]:
        lines.append(
            f"- {f['name']}\n"
            f"  Address: {f['address']}\n"
            f"  Google Maps: {f['gmaps_uri']}"
        )
    return "\n".join(lines)

def format_llm_ranked_response(required_tags, candidate_places, ranking):
    if not ranking or not ranking.get("ordered_ids"):
        return None

    by_id = {p.get("id"): p for p in candidate_places}
    ordered_places = []
    for pid in ranking["ordered_ids"]:
        place = by_id.get(pid)
        if place:
            ordered_places.append(place)

    # Fill remaining slots deterministically if LLM returned fewer than 3.
    for place in candidate_places:
        if place not in ordered_places:
            ordered_places.append(place)
        if len(ordered_places) >= 3:
            break

    ordered_places = ordered_places[:3]
    reasons = ranking.get("reasons") or {}

    lines = ["Here are the best matches from my database (ranked): " + ", ".join(required_tags)]
    for place in ordered_places:
        reason = reasons.get(place.get("id"))
        line = (
            f"- {place.get('name')}\n"
            f"  Address: {place.get('address')}\n"
            f"  Google Maps: {place.get('gmaps_uri')}"
        )
        if reason:
            line += f"\n  Why: {reason}"
        lines.append(line)

    return "\n".join(lines)

def google_text_search(query: str, limit=5):
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": query, "key": PLACES_KEY}
    r = requests.get(url, params=params, timeout=20)
    data = r.json()

    if data.get("status") != "OK":
        return [], {"status": data.get("status"), "error": data.get("error_message")}

    results = data.get("results", [])[:limit]
    return results, None

def google_place_details(place_id: str):
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "name,types,editorial_summary,opening_hours,formatted_address,geometry,price_level,rating,url,photos",
        "key": PLACES_KEY
    }
    r = requests.get(url, params=params, timeout=20)
    data = r.json()

    # DEBUG (temporary): print what Google returns
    print("DETAILS status:", data.get("status"))
    print("DETAILS keys:", list((data.get("result") or {}).keys()))

    if data.get("status") != "OK":
        return None, {"status": data.get("status"), "error": data.get("error_message")}

    return data.get("result"), None

def google_photo_url(photo_ref: str, maxwidth=800):
    return (
        "https://maps.googleapis.com/maps/api/place/photo"
        f"?maxwidth={maxwidth}&photo_reference={photo_ref}&key={PLACES_KEY}"
    )

def apply_rules_to_db(rules, limit=5):
    query = supabase.table("places").select("*")

    # ----- PRICE FILTER -----
    if "budget_amount" in rules:
        amt = rules["budget_amount"]
        if amt <= 10:
            rules["max_price"] = 1
        elif amt <= 20:
            rules["max_price"] = 2
        elif amt <= 35:
            rules["max_price"] = 3
        else:
            rules["max_price"] = 4

    # Exact price level (from canonical phrases like "$$", "mid range")
    if "price_level_exact" in rules:
        query = query.eq("price_level", rules["price_level_exact"])

    # Apply the max_price filter if present (numeric budget constraints)
    if "max_price" in rules and "price_level_exact" not in rules:
        query = query.lte("price_level", rules["max_price"])

    # ----- CUISINE FILTER -----
    if "cuisine" in rules:
        query = query.contains("types", [rules["cuisine"]])

    # ----- DIETARY FILTER -----
    if rules.get("dietary") == "halal":
        query = query.contains("types", ["halal"])

    # ----- DISTANCE FILTER -----
    if "location_query" in rules:
        loc = rules["location_query"]

        location_results, _ = google_text_search(loc, limit=1)
        if location_results:
            first = location_results[0]
            user_lat = first["geometry"]["location"]["lat"]
            user_lng = first["geometry"]["location"]["lng"]

            # Pull a larger candidate pool first (adjust as needed)
            # You MUST have latitude/longitude columns in places table
            res = query.limit(200).execute()
            rows = res.data or []

            # compute distance + filter within radius (optional)
            enriched = []
            for r in rows:
                lat = r.get("latitude")
                lng = r.get("longitude")
                if lat is None or lng is None:
                    continue
                dist_km = haversine_km(user_lat, user_lng, lat, lng)
                r["distance_km"] = round(dist_km, 2)
                enriched.append(r)

            # sort by nearest
            enriched.sort(key=lambda x: x["distance_km"])

            # return only closest N
            return enriched[:limit]

    res = query.limit(limit).execute()
    return res.data

@app.get("/api/google-places")
def google_places_endpoint():
    q = request.args.get("q", "dessert near Farrer Park MRT")
    results, err = google_text_search(q, limit=3)
    if err:
        return jsonify(err), 400

    enriched = []
    for r in results:
        place_id = r["place_id"]
        details, derr = google_place_details(place_id)
        if derr:
            continue

        photos = details.get("photos") or []
        photo = google_photo_url(photos[0]["photo_reference"]) if photos else None

        enriched.append({
            "name": details.get("name"),
            "address": details.get("formatted_address"),
            "rating": details.get("rating"),
            "price_level": details.get("price_level"),
            "open_now": (details.get("opening_hours") or {}).get("open_now"),
            "maps_url": details.get("url"),
            "photo_url": photo,
        })

    return jsonify({"query": q, "results": enriched})

def extract_filtering_rules(message):
    message = message.lower()
    rules = {}

    # ----- BUDGET  -----
    # 1) Always try to extract a number first (under $10, budget $20, <$15 etc.)
    m = re.search(r"(?:under\s*)?\$?\s*(\d+)", message)
    if "$" in message or "under" in message:
        m2 = re.search(r"\$(\d+)", message)
        if m2:
            rules["budget_amount"] = int(m2.group(1))

    # 2) If no explicit number, fall back to keywords
    if "budget_amount" not in rules:
        canonical_price_tag = detect_canonical_price_tag(message)
        if canonical_price_tag in PRICE_TAG_TO_LEVEL:
            rules["price_level_exact"] = PRICE_TAG_TO_LEVEL[canonical_price_tag]

    # ----- DIETARY -----
    if "halal" in message:
        rules["dietary"] = "halal"
    if "vegetarian" in message:
        rules["dietary"] = "vegetarian"

    # ----- CUISINE -----
    cuisines = ["japanese", "korean", "chinese", "western", "thai", "indian"]
    for c in cuisines:
        if c in message:
            rules["cuisine"] = c

    # ----- DISTANCE -----
    if "near" in message:
        rules["location_query"] = message.split("near", 1)[1].strip()

    return rules

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    user_message = data.get('message')
    session_id = data.get('session_id')
    rules = extract_filtering_rules(user_message)
    print("FILTERING RULES (debug only; chat uses tag matching):", rules)

    
    if not user_message or not session_id:
        return jsonify({"error": "Missing message or session_id"}), 400
    
    try:
        # Initialize conversation history if new session
        if session_id not in conversations:
            conversations[session_id] = []
        
        # Add user message to history
        conversations[session_id].append({
            'role': 'user',
            'content': user_message,
            'timestamp': datetime.now().isoformat()
        })
        
        # Build context from history
        context = ""
        for msg in conversations[session_id][:-1]:  # Exclude current message
            context += f"{msg['role']}: {msg['content']}\n"
        
        matched_tags = extract_tags_from_message(user_message)
        selected_tags = classify_required_tags(matched_tags)

        # Hybrid parsing: use LLM to fill missing required tags, but validate against DB tag lists.
        if any(selected_tags[k] is None for k in ("cuisine", "location", "budget")):
            llm_selected = llm_extract_required_tags(user_message, selected_tags)
            selected_tags = merge_selected_tags(selected_tags, llm_selected)

        required_tags = [selected_tags["cuisine"], selected_tags["location"], selected_tags["budget"]]
        required_tags = [t for t in required_tags if t]

        food_results = fetch_food_places_by_tags(required_tags, limit=10) if len(required_tags) == 3 else []
        # Debug
        print("MATCHED TAGS:", matched_tags)
        print("REQUIRED TAGS:", required_tags)
        print("FINAL FILTERED RESULTS:", food_results)

        # Strict deterministic gating: require cuisine + location + budget, and DB AND-match exactly those 3 tags.
        assistant_message = format_required_tag_response(selected_tags, food_results, user_message)

        # If we have valid DB matches, use the LLM only to rank/explain the candidates (never to invent places).
        if len(required_tags) == 3 and food_results:
            place_ids = [p.get("id") for p in food_results if p.get("id") is not None]
            place_tags_map = fetch_place_tags_map(place_ids)
            rank_candidates = []
            for p in food_results:
                p_copy = dict(p)
                p_copy["tags"] = place_tags_map.get(p.get("id"), [])
                rank_candidates.append(p_copy)

            ranking = llm_rank_recommendations(user_message, required_tags, rank_candidates)
            llm_message = format_llm_ranked_response(required_tags, rank_candidates, ranking)
            if llm_message:
                assistant_message = llm_message
        
        # Add assistant message to history
        conversations[session_id].append({
            'role': 'assistant',
            'content': assistant_message,
            'timestamp': datetime.now().isoformat()
        })
        
        return jsonify({"response": assistant_message})
    
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
        "place_id": place_id,
        "name": details.get("name"),
        "address": details.get("formatted_address"),
        "price_level": details.get("price_level"),
        "open_now": (details.get("opening_hours") or {}).get("open_now"),
        "types": details.get("types"),
        "auto_tags": tags
    })

@app.get("/api/test-filters")
def test_filters():
    message = request.args.get("q", "")
    rules = extract_filtering_rules(message)

    filtered_results = apply_rules_to_db(rules)

    return jsonify({
        "user_message": message,
        "rules_detected": rules,
        "results": filtered_results
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
