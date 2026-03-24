# Project FoodKakiBot - AI-Powered Chatbot for Smarter Food Discovery in Singapore

## Team 01

- Lucas Ng Hong Wei
- Gregory Tan
- Tan Zheng Liang
- Elsia Teo Yu Ning
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
   LocationEnricher — MRT proximity + planning area (formerly enrich_location_tags.py)
   GoogleEnricher — Google Places Details API (formerly enrich_places_google.py)
   AutoTagger — dataset/DB inference (formerly auto_tag_places.py)   

   python enrich.py location --apply --mrt-radius 800
   python enrich.py google   --apply --use-find --include-allergies --places-new
   python enrich.py auto     --apply --source dataset --dataset ./food.xlsx
   python enrich.py all      --apply   # runs all three in sequence

5. Export dishes mentioned in the top 5 Google reviews:
   python export_review_dishes.py

   The script will prompt for how many restaurants to process, then save an Excel workbook with:
   - a restaurant summary sheet
   - a review details sheet showing extracted dishes per review
"""
