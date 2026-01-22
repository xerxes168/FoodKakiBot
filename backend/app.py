from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import google.generativeai as genai
from datetime import datetime
import uuid

app = Flask(__name__)
CORS(app)

# Configure Gemini API
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-flash-lite')

# In-memory storage for conversations (temporary - no database)
conversations = {}

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
        
        Include Google Maps links for each restaurant using this format ONLY:
        https://www.google.com/maps/search/?api=1&query=<restaurant name>
        Do NOT use maps.app.goo.gl or short links.

        Keep responses concise and friendly."""
        
        full_prompt = f"{system_prompt}\n\nConversation history:\n{context}\n\nUser: {user_message}\n\nAssistant:"
        
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