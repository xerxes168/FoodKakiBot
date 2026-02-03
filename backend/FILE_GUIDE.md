# 🎯 Complete Auto-Tagging Solution - What to Use

## 📦 Files Overview

### ✅ **ESSENTIAL FILES** (Use These)

#### 1. **enrich_tags_with_google.py** ⭐ RECOMMENDED
**Purpose:** Standalone script that fetches Google Places data to enrich tags
**When to use:** Run separately from your app to add rich tags from Google reviews/data
**Usage:**
```bash
# Test first
python enrich_tags_with_google.py --mode test

# Then enrich all restaurants
python enrich_tags_with_google.py --mode all
```

#### 2. **app_with_autotagging.py**
**Purpose:** Flask app with basic auto-tagging (no Google API)
**When to use:** If you want built-in tagging but don't need Google enrichment
**Usage:**
```bash
cp app_with_autotagging.py app.py
```

#### 3. **database_schema.sql**
**Purpose:** Database setup (tables, indexes, views)
**When to use:** Run once in Supabase SQL Editor
**Usage:** Copy and paste into Supabase

---

## 🎯 **RECOMMENDED SETUP** (Best of Both Worlds)

### Keep Your Flask App Simple
Use your **original app.py** (or basic `app_with_autotagging.py`)

### Run Enrichment Script Separately
```bash
# Initial enrichment (run once)
python enrich_tags_with_google.py --mode all

# Weekly maintenance (new restaurants)
python enrich_tags_with_google.py --mode all --limit 50
```

**Why this is best:**
- ✅ Flask app stays fast (no Google API calls during user requests)
- ✅ Rich tagging from Google reviews and data
- ✅ Run enrichment on your schedule (doesn't slow down your app)
- ✅ Can process restaurants in batches

---

## 📚 Reference Files

### Documentation
- **ENRICHMENT_GUIDE.md** - How to use the enrichment script
- **QUICK_START.md** - Quick setup guide
- **AUTO_TAGGING_API_DOCS.md** - API documentation

### Optional Alternative Files
- **app_with_google_enhanced_tagging.py** - Flask app with built-in Google API (slower)
- **sync_tags.py** - One-time sync script (already done)
- **auto_tag_restaurants.py** - Analysis script (already done)

---

## 🚀 Quick Setup (3 Steps)

### Step 1: Setup Database
```bash
# Run in Supabase SQL Editor
database_schema.sql
```

### Step 2: Get Google API Key
```
1. Go to https://console.cloud.google.com/
2. Enable "Places API"
3. Create API Key
4. Add to .env: GOOGLE_PLACES_API_KEY=your_key
```

### Step 3: Enrich Your Data
```bash
# Test on 5 restaurants first
python enrich_tags_with_google.py --mode test

# Looks good? Process all
python enrich_tags_with_google.py --mode all
```

---

## 📊 Comparison: Different Approaches

### Option A: Enrichment Script (RECOMMENDED) ⭐
```bash
Flask App: app_with_autotagging.py
Enrichment: enrich_tags_with_google.py (run separately)
```
**Pros:**
- ✅ Fast app performance
- ✅ Rich Google-powered tags
- ✅ Flexible scheduling
- ✅ Easy to manage API quota

**Cons:**
- ⚠️ Need to run enrichment script separately
- ⚠️ Tags not instant for new restaurants

**Best for:** Production use, most users

---

### Option B: Built-in Google API
```bash
Flask App: app_with_google_enhanced_tagging.py
```
**Pros:**
- ✅ Tags enriched immediately when adding restaurant
- ✅ Everything in one place

**Cons:**
- ⚠️ Slower (1-2 seconds per restaurant add)
- ⚠️ Can hit API rate limits during bulk imports
- ⚠️ User waits for Google API during requests

**Best for:** Low-volume apps, want instant enrichment

---

### Option C: Basic Tagging Only
```bash
Flask App: app_with_autotagging.py
```
**Pros:**
- ✅ Very fast
- ✅ No API costs
- ✅ Simple

**Cons:**
- ⚠️ Less accurate tags
- ⚠️ Misses context from reviews

**Best for:** Testing, limited budget

---

## 🎯 Our Recommendation

### For Your Restaurant Chatbot:

**Use:** `enrich_tags_with_google.py` (standalone script)

**Why:**
1. Your chatbot needs **fast responses** → Don't slow it down with Google API calls
2. You want **rich, accurate tags** → Google reviews provide amazing context
3. You can **batch process** → Run enrichment weekly for new restaurants
4. **Better UX** → Users get instant responses, tagging happens in background

### Workflow:
```bash
# 1. User adds restaurant via your app (fast, basic tags)
POST /api/restaurants → [Japanese, Orchard]

# 2. Later, run enrichment script (adds rich tags)
python enrich_tags_with_google.py → [Japanese, Orchard, Romantic, Mid-Range, Highly Rated]

# 3. Chatbot uses enriched tags for better recommendations
User: "romantic Japanese in Orchard" → Perfect match!
```

---

## 🗂️ File Structure

```
your-project/
├── app.py (or app_with_autotagging.py)          # Your Flask app
├── enrich_tags_with_google.py                    # Tag enrichment script ⭐
├── database_schema.sql                           # Database setup
├── .env                                          # Your API keys
│   ├── GOOGLE_API_KEY=...
│   ├── SUPABASE_URL=...
│   ├── SUPABASE_ANON_KEY=...
│   └── GOOGLE_PLACES_API_KEY=...                # Add this!
│
└── documentation/
    ├── ENRICHMENT_GUIDE.md                       # How to use enrichment
    ├── QUICK_START.md                            # Quick start guide
    └── AUTO_TAGGING_API_DOCS.md                  # API docs
```

---

## ❓ FAQ

### Q: Do I need both app_with_autotagging.py AND enrich_tags_with_google.py?

**A:** No! Use ONE of these approaches:
- **Recommended:** Basic app + separate enrichment script
- **Alternative:** App with built-in Google API

### Q: How often should I run the enrichment script?

**A:** 
- **Initial setup:** Once to enrich all existing restaurants
- **Maintenance:** Weekly/monthly for new restaurants
- **Updates:** When you improve tag extraction logic

### Q: What if I run out of Google API quota?

**A:**
- Free tier: 3,000 calls/month = ~1,500 restaurants
- Process in batches: `--limit 100` per day
- Or upgrade to paid plan (~$17 per 1,000 restaurants)

### Q: Can I use the enrichment script without Google API?

**A:** The enrichment script specifically uses Google API. Without it, use `app_with_autotagging.py` which has basic keyword-based tagging.

### Q: Will this slow down my chatbot?

**A:** No! The enrichment script runs separately. Your chatbot stays fast.

---

## 🎓 Next Steps

1. ✅ Run `database_schema.sql` in Supabase
2. ✅ Get Google Places API key
3. ✅ Test enrichment: `python enrich_tags_with_google.py --mode test`
4. ✅ Enrich all: `python enrich_tags_with_google.py --mode all`
5. ✅ Set up weekly cron job for new restaurants

**You're all set!** Your restaurants will have rich, accurate tags from Google. 🎉
