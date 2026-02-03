"""
Test Script for Auto-Tagging System
Run this to test the auto-tagging functionality
"""

import requests
import json

BASE_URL = "http://localhost:5000"

def test_add_restaurant():
    """Test adding a restaurant with auto-tagging"""
    
    print("=" * 60)
    print("TEST 1: Add Restaurant with Auto-Tagging")
    print("=" * 60)
    
    test_restaurants = [
        {
            "name": "Sakura Sushi Bar",
            "address": "100 Orchard Road, #02-15, Singapore 238840",
            "description": "Casual Japanese sushi restaurant with fresh sashimi",
            "gmaps_uri": "https://maps.google.com/?cid=123456"
        },
        {
            "name": "Lao Zhang Chicken Rice",
            "address": "Block 123 Jurong West Ave 6, #01-234, Singapore 640123",
            "description": "Budget-friendly hawker stall serving authentic chicken rice"
        },
        {
            "name": "The French Table",
            "address": "25 Marina Bay Sands, Singapore 018956",
            "description": "Fine dining French bistro with Michelin star, romantic ambiance"
        },
        {
            "name": "Vegan Delights",
            "address": "45 Holland Village, Singapore 275954",
            "description": "Trendy plant-based cafe serving vegetarian and vegan dishes"
        }
    ]
    
    for restaurant in test_restaurants:
        try:
            response = requests.post(
                f"{BASE_URL}/api/restaurants",
                json=restaurant,
                timeout=10
            )
            
            if response.status_code == 201:
                result = response.json()
                print(f"\n✓ Added: {result['name']}")
                print(f"  Tags extracted: {', '.join(result['tags_extracted'])}")
                print(f"  Place ID: {result['place_id']}")
            else:
                print(f"\n✗ Failed to add {restaurant['name']}")
                print(f"  Error: {response.json()}")
        
        except requests.exceptions.ConnectionError:
            print("\n✗ Cannot connect to server. Make sure Flask is running on port 5000")
            return
        except Exception as e:
            print(f"\n✗ Error: {e}")
    
    print("\n" + "=" * 60)


def test_retag_single():
    """Test re-tagging a single restaurant"""
    
    print("\n" + "=" * 60)
    print("TEST 2: Re-tag Single Restaurant")
    print("=" * 60)
    
    # You'll need to replace this with an actual place_id from your database
    place_id = "your-place-id-here"
    
    print(f"\nNote: Update place_id in test script to test this endpoint")
    print(f"Example: curl -X POST {BASE_URL}/api/restaurants/{place_id}/retag")


def test_bulk_retag():
    """Test bulk re-tagging all restaurants"""
    
    print("\n" + "=" * 60)
    print("TEST 3: Bulk Re-tag All Restaurants")
    print("=" * 60)
    print("\nThis will re-tag ALL restaurants in your database.")
    print("Only run this when you update the tagging logic.\n")
    
    confirm = input("Continue? (y/n): ").strip().lower()
    
    if confirm != 'y':
        print("Skipped bulk re-tag")
        return
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/restaurants/bulk-retag",
            timeout=300  # 5 minute timeout for large datasets
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"\n✓ Bulk re-tag completed")
            print(f"  Total places: {result['total_places']}")
            print(f"  Success: {result['success_count']}")
            print(f"  Errors: {result['error_count']}")
        else:
            print(f"\n✗ Bulk re-tag failed: {response.json()}")
    
    except Exception as e:
        print(f"\n✗ Error: {e}")


def test_health_check():
    """Test the health endpoint"""
    
    print("\n" + "=" * 60)
    print("HEALTH CHECK")
    print("=" * 60)
    
    try:
        response = requests.get(f"{BASE_URL}/api/health")
        
        if response.status_code == 200:
            result = response.json()
            print("\n✓ Server is healthy")
            print(f"  Status: {result['status']}")
            print(f"  Gemini configured: {result['gemini_configured']}")
            print(f"  Supabase configured: {result['supabase_configured']}")
            print(f"  Active sessions: {result['active_sessions']}")
        else:
            print("\n✗ Health check failed")
    
    except requests.exceptions.ConnectionError:
        print("\n✗ Cannot connect to server")
        print("  Make sure Flask is running: python app.py")
    except Exception as e:
        print(f"\n✗ Error: {e}")


def demo_tag_extraction():
    """Demonstrate tag extraction locally without API"""
    
    print("\n" + "=" * 60)
    print("DEMO: Tag Extraction (No API Call)")
    print("=" * 60)
    
    # Import the AutoTagger class (this assumes you have app_with_autotagging.py)
    try:
        import sys
        sys.path.append('.')
        from app_with_autotagging import AutoTagger
        
        test_cases = [
            {
                "name": "Sushi Paradise",
                "address": "123 Orchard Road, Singapore",
                "description": "Upscale Japanese restaurant"
            },
            {
                "name": "Hawker Center Food Court",
                "address": "456 Tampines Ave 5, Singapore",
                "description": "Affordable hawker center with various cuisines"
            },
            {
                "name": "Romantic Italian Bistro",
                "address": "789 Clarke Quay, Singapore",
                "description": "Intimate Italian restaurant perfect for date nights"
            }
        ]
        
        for case in test_cases:
            tags = AutoTagger.extract_tags(
                name=case['name'],
                address=case['address'],
                description=case['description']
            )
            print(f"\n{case['name']}")
            print(f"  Tags: {', '.join(tags)}")
    
    except ImportError:
        print("\nCouldn't import AutoTagger. Make sure app_with_autotagging.py is in the current directory.")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("AUTO-TAGGING SYSTEM TEST SUITE")
    print("=" * 60)
    print("\nMake sure:")
    print("1. Flask server is running (python app.py)")
    print("2. Supabase is configured")
    print("3. Database tables are created")
    print("\n" + "=" * 60)
    
    # Run tests
    test_health_check()
    demo_tag_extraction()
    test_add_restaurant()
    test_retag_single()
    # test_bulk_retag()  # Commented out by default - uncomment to test
    
    print("\n" + "=" * 60)
    print("TEST SUITE COMPLETED")
    print("=" * 60 + "\n")
