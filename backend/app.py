from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import google.generativeai as genai
from datetime import datetime
import uuid
from supabase import create_client
from dotenv import load_dotenv

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

# In-memory storage for conversations (temporary - no database)
conversations = {}

def get_all_tag_names():
    response = supabase.table("tags").select("name").execute()
    tags = [t["name"] for t in response.data]
    return tags, {t.lower() for t in tags}

def extract_tags_from_message(user_message):
    tag_names, tag_set = get_all_tag_names()
    user_text = user_message.lower()

    matched_tags = [tag for tag in tag_names if tag.lower() in user_text]
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

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    user_message = data.get('message')
    session_id = data.get('session_id')
    
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
        - If you don't know specific restaurants in their area, provide general cuisine/style recommendations
        - Provide locations that are only in Singapore
        - Do not ask for personal information
        - If the user provides location, cuisine preference, and budget, give restaurant recommendations right away without asking further questions.
        - Always use the restaurant name, address, and Google Maps URL exactly as provided in the database context.
        - Do NOT rewrite or paraphrase restaurant names or addresses.
        - You MUST ONLY recommend restaurants that match ALL tags found in the database context.
        - If no restaurant matches, say so explicitly and do NOT suggest unrelated restaurants.
        - If no restaurants are found in the database that match the user’s tags, explain that and then suggest suitable restaurants based on general knowledge.
        - You are NOT allowed to recommend any restaurant that is not explicitly listed in the restaurant database context.
        - If the restaurant database context is empty, you must first state clearly that no matching restaurants were found in the database.
        - Only after stating that may you suggest general restaurants based on common knowledge.

        Keep responses concise and friendly."""
        
        matched_tags = extract_tags_from_message(user_message)

        # If user mentioned at least one DB tag, but DB returns no results, do NOT allow fallback
        if matched_tags:
            food_results = fetch_food_places_by_tags(matched_tags)
        else:
            food_results = []

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

if __name__ == '__main__':
    print("=" * 50)
    print("Restaurant Chatbot Backend (No Database)")
    print("=" * 50)
    print("Server running on: http://localhost:5000")
    print(f"Gemini API configured: {os.environ.get('GOOGLE_API_KEY') is not None}")
    print("Health check: http://localhost:5000/api/health")
    print("=" * 50)
    app.run(debug=True, port=5000)