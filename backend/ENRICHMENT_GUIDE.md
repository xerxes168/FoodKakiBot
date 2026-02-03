# Google Places Tag Enrichment Script

## 🎯 What This Does

A **standalone script** that runs separately from your Flask app. It:

1. ✅ Fetches restaurant data from Google Places (reviews, types, price level)
2. ✅ Extracts additional tags based on Google's richer context
3. ✅ Updates your Supabase database with new tags
4. ✅ Keeps existing tags and only adds new ones

**Key Point:** This runs independently - your Flask app (`app.py`) stays simple and fast!

## 📋 Setup

### 1. Get Google Places API Key

```bash
# 1. Go to: https://console.cloud.google.com/
# 2. Create/select a project
# 3. Enable "Places API"
# 4. Create API Key
```

### 2. Add to .env File

```bash
# Add this line:
GOOGLE_PLACES_API_KEY=your_api_key_here
```

### 3. Install Dependencies (if needed)

```bash
pip install requests python-dotenv supabase
```

## 🚀 Usage

### Test Mode (Recommended First)

Test on 5 restaurants without making changes:

```bash
python enrich_tags_with_google.py --mode test
```

**Output:**
```
[1/5] Processing...
========================================
Processing: Tokyo Sushi Bar
========================================
📌 Existing tags: Japanese, Orchard
🔍 Searching Google Places...
✅ Found on Google Places
   Rating: 4.5/5.0
   Price: Mid-Range
   Reviews: 234
🏷️  Extracting tags from Google data...
✨ New tags to add: Romantic, Casual, Highly Rated
🔍 DRY RUN - Would add 3 new tags
```

### Single Restaurant

Enrich one specific restaurant:

```bash
# Get restaurant ID from your database first
python enrich_tags_with_google.py --mode single --restaurant-id "550e8400-e29b-41d4-a716-446655440000"
```

### Enrich All Restaurants (Limited)

Process first 10 restaurants:

```bash
python enrich_tags_with_google.py --mode all --limit 10
```

### Enrich ALL Restaurants (Production)

Process your entire database:

```bash
# Dry run first to see what would change
python enrich_tags_with_google.py --mode all --dry-run

# If satisfied, run for real
python enrich_tags_with_google.py --mode all
```

## ⚙️ Command Options

```bash
--mode [test|single|all]    # Operating mode
--restaurant-id ID          # For single mode
--limit N                   # Process only N restaurants
--dry-run                   # Preview changes without saving
--delay SECONDS             # Delay between API calls (default: 0.2)
```

### Examples

```bash
# Test on 3 restaurants with dry run
python enrich_tags_with_google.py --mode all --limit 3 --dry-run

# Process 50 restaurants with slower rate limiting
python enrich_tags_with_google.py --mode all --limit 50 --delay 0.5

# Single restaurant, actually update
python enrich_tags_with_google.py --mode single --restaurant-id "abc-123"
```

## 📊 What Gets Extracted

### From Google Reviews

**Reviews say:** "Perfect date night spot, romantic ambiance, excellent sushi"
**Tags extracted:** Romantic, Japanese, Highly Rated

### From Price Level

- **0-1** → Budget
- **2** → Mid-Range
- **3-4** → Fine Dining

### From Google Types

- `bar` → Bar
- `cafe` → Cafe
- `bakery` → Bakery
- `meal_takeaway` → Takeaway

### From Context Analysis

Searches reviews, descriptions, and editorial summaries for:
- **Cuisine**: Japanese, Chinese, Korean, Italian, etc.
- **Atmosphere**: Romantic, Family-Friendly, Casual, Trendy, Traditional
- **Dietary**: Halal, Vegetarian, Gluten-Free
- **Features**: Outdoor Seating, Live Music, Late Night, Brunch, Buffet

## 📈 Performance

### API Usage

- **2 API calls per restaurant** (search + details)
- **Free tier**: 3,000 calls/month = ~1,500 restaurants
- **Processing speed**: ~1 restaurant per second (with default delay)

### Example: 982 Restaurants

```bash
python enrich_tags_with_google.py --mode all

# Takes: ~16 minutes
# Uses: 1,964 API calls
# Cost: Free (within quota)
```

## 🎯 Real Examples

### Example 1: "The Garden Restaurant"

**Before:**
```
Existing tags: Marina Bay
```

**Google fetches:**
- Reviews: "beautiful outdoor seating", "Mediterranean cuisine", "great for families"
- Price level: 2
- Rating: 4.6

**After:**
```
New tags added: Mediterranean, Outdoor Seating, Family-Friendly, Mid-Range, Highly Rated
Total tags: Marina Bay, Mediterranean, Outdoor Seating, Family-Friendly, Mid-Range, Highly Rated
```

### Example 2: "Quick Bites Hawker"

**Before:**
```
Existing tags: Jurong, Chinese
```

**Google fetches:**
- Reviews: "cheap and good", "fast service", "local food"
- Price level: 1
- Rating: 4.2

**After:**
```
New tags added: Budget, Casual
Total tags: Jurong, Chinese, Budget, Casual
```

## 🔄 When to Run This Script

### Initial Setup
```bash
# Enrich all existing restaurants
python enrich_tags_with_google.py --mode all
```

### Weekly Maintenance
```bash
# Enrich recently added restaurants
python enrich_tags_with_google.py --mode all --limit 50
```

### After Updating Tag Logic
```bash
# Re-run to apply new extraction rules
python enrich_tags_with_google.py --mode all
```

## 🐛 Troubleshooting

### "Could not find on Google Places"

**Causes:**
- Restaurant name spelling doesn't match Google
- New restaurant not yet on Google Maps
- Address is incomplete

**Solutions:**
- Check restaurant name in Google Maps manually
- Add more specific address details
- Skip and process manually

### "OVER_QUERY_LIMIT"

**Cause:** Exceeded API quota

**Solutions:**
```bash
# Option 1: Wait for quota reset (monthly)

# Option 2: Process in smaller batches
python enrich_tags_with_google.py --mode all --limit 100 --delay 0.5

# Option 3: Upgrade to paid plan
```

### API Key Not Working

**Check:**
```bash
# 1. API key is in .env file
cat .env | grep GOOGLE_PLACES_API_KEY

# 2. Places API is enabled in Google Cloud Console

# 3. Test the key manually
python enrich_tags_with_google.py --mode test
```

## 📊 Statistics & Reporting

The script shows a summary at the end:

```
==================================================
SUMMARY
==================================================
Total restaurants: 982
Successfully processed: 945
Failed: 37
Not found on Google: 37
No new tags needed: 123

🏷️  Top 10 New Tags Added:
   Mid-Range: 456 restaurants
   Casual: 389 restaurants
   Family-Friendly: 234 restaurants
   Romantic: 178 restaurants
   Highly Rated: 567 restaurants
   Outdoor Seating: 89 restaurants
   Budget: 267 restaurants
   Trendy: 145 restaurants
   Traditional: 98 restaurants
   Vegetarian: 76 restaurants
```

## 💰 Cost Management

### Stay Within Free Tier

```bash
# Process 100 restaurants per day = 200 API calls/day
# = 6,000 API calls/month (within free tier)

# Monday
python enrich_tags_with_google.py --mode all --limit 100

# Tuesday
python enrich_tags_with_google.py --mode all --limit 100

# etc...
```

### Monitor Usage

Check usage in [Google Cloud Console](https://console.cloud.google.com/):
- APIs & Services → Dashboard → Places API

## 🔧 Integration with Your Workflow

### Option 1: Manual Runs

```bash
# Run when needed
python enrich_tags_with_google.py --mode all --limit 50
```

### Option 2: Scheduled (Cron Job)

```bash
# Add to crontab (runs weekly)
0 2 * * 0 cd /path/to/project && python enrich_tags_with_google.py --mode all --limit 100
```

### Option 3: On-Demand API Endpoint

Add this to your Flask app if you want to trigger enrichment via API:

```python
@app.route('/api/admin/enrich-tags', methods=['POST'])
def trigger_enrichment():
    """Admin endpoint to trigger tag enrichment"""
    # Run enrichment script in background
    import subprocess
    subprocess.Popen(['python', 'enrich_tags_with_google.py', '--mode', 'all', '--limit', '50'])
    return jsonify({"message": "Tag enrichment started"})
```

## 📝 Best Practices

1. ✅ **Always test first**: Use `--mode test` before processing all
2. ✅ **Use dry-run**: Verify changes with `--dry-run` flag
3. ✅ **Process in batches**: Use `--limit` to stay within API quota
4. ✅ **Rate limit**: Use `--delay` to be nice to Google's servers
5. ✅ **Monitor logs**: Check for "not found" restaurants
6. ✅ **Review new tags**: Check the summary to see if extraction makes sense

## 🎓 Advanced Usage

### Custom Tag Extraction

Edit the script to add your own tag categories:

```python
# In GoogleTagExtractor class:

CUSTOM_KEYWORDS = {
    'Pet-Friendly': ['pet-friendly', 'dogs allowed', 'cat cafe'],
    'WiFi': ['wifi', 'free wifi', 'internet'],
    'Parking': ['parking', 'valet', 'free parking'],
}

# Then in extract_tags_from_google_data:
for feature, keywords in cls.CUSTOM_KEYWORDS.items():
    for keyword in keywords:
        if keyword in text_for_analysis:
            tags.add(feature)
            break
```

### Filter by Existing Tags

Only enrich restaurants missing certain tags:

```python
# Before enriching, check:
if 'Casual' not in existing_tags:
    # Run enrichment
```

## 📚 Summary

| Feature | Value |
|---------|-------|
| **Runs separately from app** | ✅ Yes |
| **Updates database** | ✅ Yes |
| **API calls per restaurant** | 2 |
| **Free tier limit** | ~1,500 restaurants/month |
| **Processing speed** | ~1 per second |
| **Keeps existing tags** | ✅ Yes |
| **Dry run available** | ✅ Yes |

Your Flask app stays simple and fast, while this script enriches your data in the background! 🎉
