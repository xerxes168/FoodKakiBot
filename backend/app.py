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
    user_text = (user_message or "").lower()

    matched_tags = [tag for tag in tag_names if tag.lower() in user_text]

    # Map "$", "$$", "mid range", etc. -> one canonical price tag if present in DB.
    canonical_price_tag = detect_canonical_price_tag(user_message or "")
    if canonical_price_tag:
        tag_lookup = {t.lower(): t for t in tag_names}
        actual_tag = tag_lookup.get(canonical_price_tag.lower())
        if actual_tag and actual_tag not in matched_tags:
            matched_tags.append(actual_tag)

    return matched_tags

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
    print("FILTERING RULES:", rules)

    
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
        
        # Create prompt with system instruction
        system_prompt = """You are FoodKakiBot, a helpful restaurant recommendation assistant. Help users decide where to eat based on their preferences, location, cuisine type, budget, dietary restrictions, and mood.

        When making recommendations:
        - Ask clarifying questions if needed (location, cuisine preference, budget, dietary needs)
        - Provide 2-3 specific restaurant suggestions when you have enough information
        - Include brief descriptions of why each restaurant is a good fit
        - Be conversational and enthusiastic
        - Use location context to tailor recommendations appropriately
        - Provide locations that are only in Singapore
        - Do not ask for personal information
        - If the user provides location, cuisine preference, and budget, give restaurant recommendations right away without asking further questions.
        - Always use the restaurant name, address, and Google Maps URL exactly as provided in the database context.
        - Do NOT rewrite or paraphrase restaurant names or addresses.
        - You MUST ONLY recommend restaurants that match ALL tags found in the database context.
        - Make sure to ONLY recommend restaurants that are in the database, if the restaurants are not in the database, explain that no restaurants were found and don't offer any other options.
        - If no restaurants are found in the database that match the user tags, explain that no restaurants were found.
        - If the restaurant database context is empty, you must first state clearly that no matching restaurants were found in the database.
        - NO CRUD operations on the database are allowed.

        Keep responses concise and friendly."""
        
        matched_tags = extract_tags_from_message(user_message)

        # If user mentioned at least one DB tag, but DB returns no results, do NOT allow fallback
        if matched_tags:
            food_results = fetch_food_places_by_tags(matched_tags)
        else:
            food_results = []

        filtered_results = apply_rules_to_db(rules) or []
        food_results = merge_place_results(food_results, filtered_results)
        # Debug
        print("FINAL FILTERED RESULTS:", food_results)

        food_context = ""
        if food_results:
            food_context = "Here are available restaurants from the database:\n"
            for f in food_results[:5]:
                food_context += (
                    f"- {f['name']}\n"
                    f"  Address: {f['address']}\n"
                    f"  Google Maps: {f['gmaps_uri']}\n"
                )

        full_prompt = f"""
        {system_prompt}

        Restaurant database context:
        {food_context}

        Conversation history:
        {context}

        User: {user_message}
        Assistant:
        """
        

        # Get AI response from Gemini
        response = model.generate_content(full_prompt)
        assistant_message = response.text
        
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
