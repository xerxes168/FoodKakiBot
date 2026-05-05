# Project FoodKakiBot - AI-Powered Chatbot for Smarter Food Discovery in Singapore

## Team 01

- Lucas Ng Hong Wei
- Gregory Tan
- Tan Zheng Liang
- Elsia Teo Yu Ning
- Moo Zhe Yan

---

## Project Overview

FoodKakiBot is a Singaporean-Based AI-powered food recommendation chatbot. Users describe what they're craving by cuisine, location, budget, or vibe and the bot retrieves matching restaurants from a curated database and responds with grounded, hallucination-free recommendations powered by Google Gemini.

The system uses a Retrieval-Augmented Generation (RAG) pipeline: user queries are embedded and matched against a vector database of restaurants in Supabase, filtered by tags (cuisine, location, budget), ranked by distance and popularity, and then passed as context to the LLM.

---

## Script Reference

### `app.py` — Flask Backend / API Server

The main backend server. Exposes a REST API that the frontend calls for each chat message. Orchestrates the full chat pipeline:

1. Extracts tags (cuisine, location, budget) from the user's message using both rule-based parsing and an LLM fallback.
2. Calls the RAG retrieval pipeline to find matching restaurants.
3. Fetches tags for the retrieved candidates to build richer context.
4. Ranks candidates using the scoring logic in `ranking.py`.
5. Passes the grounded context to Gemini to generate the final response.

Also handles user location (GPS coordinates), session/conversation history, and Google Maps geocoding for location-aware queries.

### `rag.py` — RAG Core Module

The core Retrieval-Augmented Generation module. Imported by `app.py` — not run directly.

- **Embedding**: Converts user queries and restaurant documents into vector embeddings using `gemini-embedding-001` via the Google Generative Language REST API.
- **Vector retrieval**: Runs cosine-similarity search against the `places` table in Supabase using `pgvector`, with optional tag-based hard filters.
- **Tag retrieval**: Falls back to pure tag-intersection queries when embeddings are unavailable.
- **Hybrid retrieval**: Combines both strategies, trying vector search first and falling back to tags.
- **Context builder**: Formats retrieved restaurants into a structured block passed to the LLM.
- **Grounded generation**: Prompts Gemini to respond strictly using the retrieved context, preventing hallucinated restaurant details.

### `ranking.py` — Post-Retrieval Ranking

Scores and sorts retrieved restaurant candidates before they are passed to the LLM. Imported by `app.py`.

Scores each candidate across three dimensions:

| Dimension | Weight | Description |
|---|---|---|
| Preference match | 40% | Tag overlap between the query and the restaurant |
| Distance | 50% | Haversine distance from the user's GPS location |
| Popularity | 10% | Composite of Google rating and review count |

Also filters out restaurants that are currently closed, unless the user's message signals they are planning a future visit.

### `enrich.py` — Tagging & Location Enrichment

A unified data enrichment tool with both a CLI interface (for one-time/batch runs) and a public API imported by `app.py` and `rag.py`.

Three enrichers can be run independently or together:

- **`LocationEnricher`**: Adds location-based tags: nearest MRT station (within configurable radius), Singapore planning area (derived from postal code), and road name (parsed from address).
- **`GoogleEnricher`**: Calls the Google Places Details API to add cuisine type, dietary/allergy tags, and other attributes inferred from Google's place data.
- **`AutoTagger`**: Infers tags from the existing dataset or Supabase records using keyword rules (e.g. mapping sub-cuisines like "Ramen" or "Dim Sum" to their parent cuisine tags like "Japanese" or "Chinese").

The module also exposes helper functions used at query time to expand location tags to nearby areas and build multi-tag filter sets for retrieval.

### `fetch_place_details.py` — Google Places Photo & Details Fetcher

A standalone one-time/batch script that enriches the `places` table with data from the Google Places API. Run this after initially populating the database.

Fetches and writes back:
- **Editorial summary**: a short human-readable description of the restaurant.
- **Opening hours**: structured weekly schedule stored as JSONB.
- **Photo**: downloads the primary Google Places photo and uploads it to a Supabase Storage bucket, storing the public URL.

Supports `--limit`, `--force`, `--skip-photos`, and `--skip-details` flags to control which fields are refreshed and how many places are processed.

### `wink_tag.py` — Wink Tag Initialisation Script

A one-time utility script that creates a "Wink" tag in the `tags` table and assigns it to every existing place in the database. Safe to re-run and skips places that are already tagged. Used to initialise a base tag shared by all places.

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