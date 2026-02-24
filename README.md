# Project FoodKakiBot - AI-Powered Chatbot for Smarter Food Discovery in Singapore

## Team 01

- Lucas Ng Hong Wei
- Gregory Tan
- Tan Zheng Liang
- Elsia Teo
- Moo Zhe Yan

---

## SETUP INSTRUCTIONS

"""

1. Enter API keys in:
   /backend/.env
   - GOOGLE_API_KEY (Gemini)
   - GOOGLE_PLACES_API_KEY or GOOGLE_MAPS_API_KEY (Places/Maps)

2. Set up virtual environment:
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate

3. To run frontend:
   cd foodkakibot
   npm install
   npm run dev

   Frontend will run on <http://localhost:3000>

4. To run backend:
   cd backend

5. Install dependencies:
   pip install -r requirements.txt

6. Run the server:
   python app.py

   Backend will run on <http://localhost:5000>
"""

## For tagging test

"""
1. Set up virtual environment:
   python -m venv venv
   source venv/bin/activate

2. cd backend

3. Install dependencies:
   pip install -r requirements.txt

4. Run the test file:
   python fetch_and_enrich.py

   This will run the first 10 restaurants from the SupaBase DB through the Google Places API and generate an Excel file.

   python fetch_and_enrich.py                         # 10 restaurants (default)
   python fetch_and_enrich.py --limit 50              # 50 restaurants
   python fetch_and_enrich.py --limit 0               # all restaurants
   python fetch_and_enrich.py --limit 25 --output my_data.xlsx  # custom output name
"""