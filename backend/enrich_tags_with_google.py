"""
Standalone Google Places Tag Enrichment Script
This script fetches additional data from Google Places API for each restaurant
and updates their tags in the Supabase database with richer context.

Run this separately from your Flask app to enrich existing restaurant data.
"""

import os
import time
import requests
import re
from supabase import create_client
from dotenv import load_dotenv
from collections import defaultdict

load_dotenv()

# Configuration
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY")

GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY")

# Immediate diagnostic print for API key presence and partial value
if GOOGLE_PLACES_API_KEY:
    print(f"✓ GOOGLE_PLACES_API_KEY loaded (ends with ...{GOOGLE_PLACES_API_KEY[-4:]})")
else:
    print("⚠️  GOOGLE_PLACES_API_KEY not found in environment/.env")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============================================================================
# GOOGLE PLACES API FUNCTIONS
# ============================================================================

def search_place_on_google(place_name, address):
    """
    Search for a place on Google Places API with stronger matching.
    Returns: place_id if found, None otherwise
    """

    if not GOOGLE_PLACES_API_KEY:
        print("❌ Google Places API key not configured")
        return None

    # Skip obvious non-food places
    if "playground" in place_name.lower():
        print("⚠️  Skipped non-food place")
        return None

    # Clean restaurant name (remove brackets like "(Tanjong Pagar)")
    clean_name = re.sub(r"\(.*?\)", "", place_name).strip()

    # Force Singapore context to reduce ambiguity
    search_query = f"{clean_name}, Singapore"

    try:
        search_url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
        search_params = {
            "input": search_query,
            "inputtype": "textquery",
            "fields": "place_id,name",
            "locationbias": "circle:5000@1.3521,103.8198",
            "key": GOOGLE_PLACES_API_KEY
        }

        response = requests.get(search_url, params=search_params, timeout=10)
        data = response.json()

        # Debug output to understand failures (REQUEST_DENIED needs the error_message)
        if data.get("status") != "OK":
            status = data.get("status")
            err = data.get("error_message")
            print(f"⚠️  Google status: {status}")
            if err:
                print(f"⚠️  Google error_message: {err}")
            else:
                # Print a minimal view of the payload to help diagnose missing fields
                print(f"⚠️  Google response keys: {list(data.keys())}")
            return None

        if data.get("candidates"):
            return data["candidates"][0]["place_id"]

        return None

    except Exception as e:
        print(f"❌ Error searching for {place_name}: {e}")
        return None


def fetch_place_details(place_id):
    """
    Fetch detailed information about a place from Google Places API
    Returns: dict with types, reviews, price_level, etc.
    """
    try:
        details_url = "https://maps.googleapis.com/maps/api/place/details/json"
        details_params = {
            "place_id": place_id,
            "fields": "types,rating,price_level,reviews,editorial_summary,website,opening_hours,user_ratings_total",
            "key": GOOGLE_PLACES_API_KEY
        }
        
        response = requests.get(details_url, params=details_params, timeout=10)
        data = response.json()
        
        if data.get("status") == "OK":
            return data.get("result", {})

        status = data.get("status")
        err = data.get("error_message")
        print(f"⚠️  Google details status: {status}")
        if err:
            print(f"⚠️  Google details error_message: {err}")
        return None
    
    except Exception as e:
        print(f"❌ Error fetching place details: {e}")
        return None


def get_google_place_data(place_name, address):
    """
    Complete workflow: Search and fetch detailed data for a place
    Returns: dict with Google Places data
    """
    # Step 1: Search for place
    place_id = search_place_on_google(place_name, address)
    if not place_id:
        return None
    
    # Step 2: Fetch details
    details = fetch_place_details(place_id)
    return details


# ============================================================================
# TAG EXTRACTION FROM GOOGLE DATA
# ============================================================================

class GoogleTagExtractor:
    """Extract tags from Google Places API data"""
    
    # Cuisine detection from Google types and reviews
    CUISINE_KEYWORDS = {
        'Japanese': ['japanese', 'sushi', 'ramen', 'soba', 'tempura', 'izakaya', 'yakitori', 'tonkatsu', 'udon', 'teriyaki'],
        'Chinese': ['chinese', 'dim sum', 'cantonese', 'szechuan', 'peking duck', 'zi char', 'noodles', 'dumpling'],
        'Korean': ['korean', 'bbq', 'kimchi', 'bibimbap', 'bulgogi', 'kbbq', 'banchan'],
        'Thai': ['thai', 'tom yum', 'pad thai', 'green curry', 'red curry', 'som tam'],
        'Vietnamese': ['vietnamese', 'pho', 'banh mi', 'spring roll', 'bun'],
        'Indian': ['indian', 'curry', 'tandoori', 'biryani', 'naan', 'masala', 'tikka'],
        'Italian': ['italian', 'pizza', 'pasta', 'risotto', 'trattoria', 'gelato', 'tiramisu'],
        'French': ['french', 'bistro', 'brasserie', 'croissant', 'escargot', 'bouillabaisse'],
        'Mexican': ['mexican', 'tacos', 'burrito', 'quesadilla', 'guacamole', 'enchilada'],
        'Western': ['western', 'steak', 'burger', 'grill', 'ribeye', 'sirloin'],
        'Seafood': ['seafood', 'fish', 'oyster', 'lobster', 'crab', 'prawn', 'salmon', 'tuna'],
        'Mediterranean': ['mediterranean', 'greek', 'hummus', 'falafel', 'shawarma', 'kebab'],
        'Middle Eastern': ['middle eastern', 'lebanese', 'turkish', 'persian', 'arabic'],
        'Spanish': ['spanish', 'tapas', 'paella', 'sangria', 'jamon'],
    }
    
    # Atmosphere keywords from reviews
    ATMOSPHERE_KEYWORDS = {
        'Romantic': ['romantic', 'intimate', 'date night', 'cozy', 'candlelit', 'perfect for couples', 'anniversary'],
        'Family-Friendly': ['family', 'children', 'kids', 'kid-friendly', 'playground', 'high chair', 'family-friendly'],
        'Casual': ['casual', 'relaxed', 'laid-back', 'informal', 'chill', 'easygoing'],
        'Trendy': ['trendy', 'hipster', 'instagram', 'instagrammable', 'modern', 'stylish', 'chic'],
        'Traditional': ['traditional', 'heritage', 'authentic', 'classic', 'old-school', 'historical'],
        'Upscale': ['upscale', 'elegant', 'sophisticated', 'classy', 'refined', 'luxury'],
        'Lively': ['lively', 'energetic', 'vibrant', 'buzzing', 'busy', 'bustling'],
    }
    
    # Dietary tags
    DIETARY_KEYWORDS = {
        'Halal': ['halal', 'muslim-friendly', 'halal-certified'],
        'Vegetarian': ['vegetarian', 'vegan', 'plant-based', 'veggie', 'vegetarian options'],
        'Gluten-Free': ['gluten-free', 'gluten free', 'celiac-friendly'],
    }
    
    # Special features
    SPECIAL_FEATURES = {
        'Outdoor Seating': ['outdoor', 'alfresco', 'patio', 'terrace', 'garden seating'],
        'Live Music': ['live music', 'live band', 'jazz', 'acoustic'],
        'Late Night': ['late night', '24 hours', 'open late', 'midnight'],
        'Brunch': ['brunch', 'breakfast', 'morning'],
        'Buffet': ['buffet', 'all you can eat', 'unlimited'],
        'Takeaway': ['takeaway', 'take out', 'delivery', 'grab'],
    }
    
    # Map Google Place Types to our tags
    GOOGLE_TYPES_MAP = {
        'bar': 'Bar',
        'cafe': 'Cafe',
        'bakery': 'Bakery',
        'night_club': 'Bar',
        'meal_takeaway': 'Takeaway',
        'meal_delivery': 'Takeaway',
    }
    
    @classmethod
    def extract_tags_from_google_data(cls, google_data, restaurant_name, address):
        """
        Extract tags from Google Places data
        
        Args:
            google_data: Response from Google Places API
            restaurant_name: Original restaurant name
            address: Restaurant address
            
        Returns:
            List of extracted tags
        """
        tags = set()
        
        if not google_data:
            return []
        
        # Combine all text for analysis
        text_for_analysis = f"{restaurant_name} {address}".lower()
        
        # Extract from Google types
        place_types = google_data.get('types', [])
        for place_type in place_types:
            if place_type in cls.GOOGLE_TYPES_MAP:
                tags.add(cls.GOOGLE_TYPES_MAP[place_type])
        
        # Extract from editorial summary
        editorial = google_data.get('editorial_summary', {})
        if editorial and 'overview' in editorial:
            text_for_analysis += ' ' + editorial['overview'].lower()
        
        # Extract from reviews
        reviews = google_data.get('reviews', [])
        review_texts = []
        for review in reviews[:5]:  # Top 5 reviews
            review_text = review.get('text', '')
            if review_text:
                review_texts.append(review_text.lower())
        
        if review_texts:
            text_for_analysis += ' ' + ' '.join(review_texts)
        
        # Extract cuisine tags
        for cuisine, keywords in cls.CUISINE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_for_analysis:
                    tags.add(cuisine)
                    break
        
        # Extract atmosphere tags
        for atmosphere, keywords in cls.ATMOSPHERE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_for_analysis:
                    tags.add(atmosphere)
                    break
        
        # Extract dietary tags
        for dietary, keywords in cls.DIETARY_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_for_analysis:
                    tags.add(dietary)
                    break
        
        # Extract special features
        for feature, keywords in cls.SPECIAL_FEATURES.items():
            for keyword in keywords:
                if keyword in text_for_analysis:
                    tags.add(feature)
                    break
        
        # Extract price level
        price_level = google_data.get('price_level')
        if price_level is not None:
            if price_level <= 1:
                tags.add('Budget')
            elif price_level == 2:
                tags.add('Mid-Range')
            elif price_level >= 3:
                tags.add('Fine Dining')
        
        # Extract from rating (high-rated places)
        rating = google_data.get('rating')
        if rating and rating >= 4.5:
            tags.add('Highly Rated')
        
        return list(tags)


# ============================================================================
# DATABASE OPERATIONS
# ============================================================================

def ensure_tags_exist(tag_names):
    """Ensure all tags exist in the database, create if they don't"""
    tag_map = {}
    
    for tag_name in tag_names:
        try:
            # Try to get existing tag
            result = supabase.table("tags").select("id, name").eq("name", tag_name).execute()
            
            if result.data:
                tag_map[tag_name] = result.data[0]['id']
            else:
                # Create new tag
                insert_result = supabase.table("tags").insert({"name": tag_name}).execute()
                tag_map[tag_name] = insert_result.data[0]['id']
                print(f"  ✨ Created new tag: {tag_name}")
        except Exception as e:
            print(f"  ❌ Error with tag '{tag_name}': {e}")
    
    return tag_map


def add_tags_to_place(place_id, tag_ids):
    """Add tags to a place (keeps existing tags)"""
    added_count = 0
    for tag_id in tag_ids:
        try:
            supabase.table("place_tags").insert({
                "place_id": place_id,
                "tag_id": tag_id
            }).execute()
            added_count += 1
        except:
            pass  # Tag already exists for this place
    
    return added_count


def get_existing_tags_for_place(place_id):
    """Get existing tag names for a place"""
    try:
        result = supabase.table("place_tags").select(
            "tags(name)"
        ).eq("place_id", place_id).execute()
        
        existing_tags = [r['tags']['name'] for r in result.data if r.get('tags')]
        return set(existing_tags)
    except:
        return set()


# ============================================================================
# MAIN ENRICHMENT FUNCTIONS
# ============================================================================

def enrich_single_restaurant(place_id, name, address, dry_run=False):
    """
    Enrich a single restaurant with Google Places data
    
    Args:
        place_id: Restaurant ID in database
        name: Restaurant name
        address: Restaurant address
        dry_run: If True, only show what would be added without actually adding
    
    Returns:
        dict with results
    """
    print(f"\n{'='*60}")
    print(f"Processing: {name}")
    print(f"{'='*60}")
    
    # Get existing tags
    existing_tags = get_existing_tags_for_place(place_id)
    print(f"📌 Existing tags: {', '.join(existing_tags) if existing_tags else 'None'}")
    
    # Fetch Google data
    print(f"🔍 Searching Google Places...")
    google_data = get_google_place_data(name, address)
    
    if not google_data:
        print(f"⚠️  Could not find on Google Places")
        return {
            'success': False,
            'name': name,
            'reason': 'Not found on Google'
        }
    
    print(f"✅ Found on Google Places")
    
    # Show what Google knows
    if google_data.get('rating'):
        print(f"   Rating: {google_data['rating']}/5.0")
    if google_data.get('price_level') is not None:
        price_labels = ['Free', 'Budget', 'Mid-Range', 'Fine Dining', 'Luxury']
        print(f"   Price: {price_labels[google_data['price_level']]}")
    if google_data.get('user_ratings_total'):
        print(f"   Reviews: {google_data['user_ratings_total']}")
    
    # Extract new tags
    print(f"🏷️  Extracting tags from Google data...")
    new_tags = GoogleTagExtractor.extract_tags_from_google_data(google_data, name, address)
    
    # Filter out tags that already exist
    tags_to_add = [tag for tag in new_tags if tag not in existing_tags]
    
    if not tags_to_add:
        print(f"✓ No new tags to add (already has all relevant tags)")
        return {
            'success': True,
            'name': name,
            'new_tags': [],
            'existing_tags': list(existing_tags)
        }
    
    print(f"✨ New tags to add: {', '.join(tags_to_add)}")
    
    if dry_run:
        print(f"🔍 DRY RUN - Would add {len(tags_to_add)} new tags")
        return {
            'success': True,
            'name': name,
            'new_tags': tags_to_add,
            'existing_tags': list(existing_tags),
            'dry_run': True
        }
    
    # Add new tags to database
    tag_map = ensure_tags_exist(tags_to_add)
    tag_ids = [tag_map[tag] for tag in tags_to_add if tag in tag_map]
    added_count = add_tags_to_place(place_id, tag_ids)
    
    print(f"✅ Added {added_count} new tags")
    
    return {
        'success': True,
        'name': name,
        'new_tags': tags_to_add,
        'existing_tags': list(existing_tags),
        'added_count': added_count
    }


def enrich_all_restaurants(limit=None, dry_run=False, delay=0.2):
    """
    Enrich all restaurants in the database with Google Places data
    
    Args:
        limit: Maximum number of restaurants to process (None for all)
        dry_run: If True, only show what would be added
        delay: Delay between API calls (seconds) to respect rate limits
    
    Returns:
        Summary statistics
    """
    print("\n" + "="*60)
    print("GOOGLE PLACES TAG ENRICHMENT")
    print("="*60)
    
    if not GOOGLE_PLACES_API_KEY:
        print("❌ Error: GOOGLE_PLACES_API_KEY not set in .env file")
        return
    
    # Get all restaurants
    print(f"\n📊 Fetching restaurants from database...")
    places_result = supabase.table("places").select("id, name, address").execute()
    places = places_result.data
    
    if limit:
        places = places[:limit]
    
    print(f"✓ Found {len(places)} restaurants to process")
    
    if dry_run:
        print(f"\n🔍 DRY RUN MODE - No changes will be made\n")
    
    # Statistics
    stats = {
        'total': len(places),
        'success': 0,
        'failed': 0,
        'not_found': 0,
        'no_new_tags': 0,
        'new_tags_added': defaultdict(int)
    }
    
    # Process each restaurant
    for i, place in enumerate(places, 1):
        print(f"\n[{i}/{len(places)}] Processing...")
        
        result = enrich_single_restaurant(
            place['id'],
            place['name'],
            place['address'],
            dry_run=dry_run
        )
        
        if result['success']:
            stats['success'] += 1
            if result.get('new_tags'):
                for tag in result['new_tags']:
                    stats['new_tags_added'][tag] += 1
            else:
                stats['no_new_tags'] += 1
        else:
            stats['failed'] += 1
            if result.get('reason') == 'Not found on Google':
                stats['not_found'] += 1
        
        # Rate limiting
        if i < len(places):
            time.sleep(delay)
    
    # Print summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Total restaurants: {stats['total']}")
    print(f"Successfully processed: {stats['success']}")
    print(f"Failed: {stats['failed']}")
    print(f"Not found on Google: {stats['not_found']}")
    print(f"No new tags needed: {stats['no_new_tags']}")
    
    if stats['new_tags_added']:
        print(f"\n🏷️  Top 10 New Tags Added:")
        sorted_tags = sorted(stats['new_tags_added'].items(), key=lambda x: x[1], reverse=True)
        for tag, count in sorted_tags[:10]:
            print(f"   {tag}: {count} restaurants")
    
    return stats


# ============================================================================
# CLI INTERFACE
# ============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Enrich restaurant tags using Google Places API"
    )
    parser.add_argument(
        '--mode',
        choices=['all', 'single', 'test'],
        default='test',
        help='Enrichment mode (default: test)'
    )
    parser.add_argument(
        '--restaurant-id',
        help='Restaurant ID for single mode'
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Limit number of restaurants to process'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be added without making changes'
    )
    parser.add_argument(
        '--delay',
        type=float,
        default=0.2,
        help='Delay between API calls in seconds (default: 0.2)'
    )
    
    args = parser.parse_args()
    
    if args.mode == 'test':
        print("Test mode: Processing first 5 restaurants")
        enrich_all_restaurants(limit=5, dry_run=True, delay=args.delay)
    
    elif args.mode == 'single':
        if not args.restaurant_id:
            print("❌ Error: --restaurant-id required for single mode")
            return
        
        # Get restaurant details
        place = supabase.table("places").select("*").eq("id", args.restaurant_id).execute()
        if not place.data:
            print(f"❌ Restaurant not found: {args.restaurant_id}")
            return
        
        place_data = place.data[0]
        enrich_single_restaurant(
            place_data['id'],
            place_data['name'],
            place_data['address'],
            dry_run=args.dry_run
        )
    
    elif args.mode == 'all':
        if not args.dry_run:
            print("\n⚠️  WARNING: This will enrich ALL restaurants in your database")
            print("This will use your Google Places API quota!")
            confirm = input("\nContinue? (yes/no): ").strip().lower()
            if confirm != 'yes':
                print("Cancelled.")
                return
        
        enrich_all_restaurants(
            limit=args.limit,
            dry_run=args.dry_run,
            delay=args.delay
        )


if __name__ == "__main__":
    main()
