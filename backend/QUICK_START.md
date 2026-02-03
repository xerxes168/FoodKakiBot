# Quick Start Guide: Sustainable Auto-Tagging System

## 🎯 Goal
Make your restaurant tagging system sustainable by automatically extracting and assigning tags whenever new restaurants are added.

## 📋 Prerequisites Checklist

- [ ] Python 3.8+ installed
- [ ] Flask app running
- [ ] Supabase account set up
- [ ] Database tables created (tags, places, place_tags)

## 🚀 Setup (5 minutes)

### Step 1: Update Your Flask App

Replace your current `app.py` with the auto-tagging version:

```bash
# Backup your current app
cp app.py app_backup.py

# Use the new version with auto-tagging
cp app_with_autotagging.py app.py
```

### Step 2: Verify Database Schema

Make sure you have these tables in Supabase:

```sql
-- Run this in Supabase SQL Editor if you haven't already
-- (use database_schema.sql for full schema)

CREATE TABLE IF NOT EXISTS tags (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS places (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name TEXT NOT NULL,
  address TEXT NOT NULL,
  gmaps_uri TEXT,
  UNIQUE(name, address)
);

CREATE TABLE IF NOT EXISTS place_tags (
  place_id UUID REFERENCES places(id) ON DELETE CASCADE,
  tag_id UUID REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (place_id, tag_id)
);
```

### Step 3: Restart Your Server

```bash
python app.py
```

You should see:
```
Restaurant Chatbot Backend with Auto-Tagging
Server running on: http://localhost:5000

New Endpoints:
  POST /api/restaurants - Add restaurant with auto-tagging
  POST /api/restaurants/<id>/retag - Re-tag a restaurant
  POST /api/restaurants/bulk-retag - Re-tag all restaurants
```

### Step 4: Tag Existing Restaurants

If you already have restaurants in your database:

```bash
curl -X POST http://localhost:5000/api/restaurants/bulk-retag
```

This will analyze all existing restaurants and assign appropriate tags.

## ✅ Test It Out

### Test 1: Add a New Restaurant

```bash
curl -X POST http://localhost:5000/api/restaurants \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Sakura Sushi Bar",
    "address": "100 Orchard Road, Singapore",
    "description": "Casual Japanese restaurant"
  }'
```

**Expected Response:**
```json
{
  "success": true,
  "place_id": "...",
  "name": "Sakura Sushi Bar",
  "tags_extracted": ["Japanese", "Orchard", "Casual"],
  "message": "Restaurant added successfully with 3 tags"
}
```

### Test 2: Use Python Script

```bash
python test_autotagging.py
```

This runs a full test suite showing how auto-tagging works.

## 🔄 How It Works Daily

### Scenario 1: Adding Restaurant via API

```python
import requests

# Your backend automatically tags this
response = requests.post('http://localhost:5000/api/restaurants', json={
    "name": "New Thai Restaurant",
    "address": "123 Marina Bay, Singapore",
    "description": "Family-friendly Thai restaurant with budget options"
})

# System automatically extracts: ["Thai", "Marina Bay", "Family-Friendly", "Budget"]
```

### Scenario 2: Importing from CSV/Excel

```python
import pandas as pd
import requests

# Read your restaurant data
df = pd.read_excel('new_restaurants.xlsx')

for _, row in df.iterrows():
    # Each restaurant is automatically tagged
    requests.post('http://localhost:5000/api/restaurants', json={
        "name": row['name'],
        "address": row['address'],
        "description": row.get('description', '')
    })
```

### Scenario 3: User Submits via Form

```javascript
// Frontend form submission
async function submitRestaurant(formData) {
  const response = await fetch('/api/restaurants', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: formData.name,
      address: formData.address,
      description: formData.description
    })
  });
  
  const result = await response.json();
  // Tags are automatically extracted and saved
  console.log('Auto-tagged with:', result.tags_extracted);
}
```

## 🎨 Customizing Tag Rules

### Add New Cuisine Type

Edit `app.py` and add to `CUISINE_KEYWORDS`:

```python
CUISINE_KEYWORDS = {
    # ... existing cuisines ...
    'Spanish': ['spanish', 'tapas', 'paella'],
    'Greek': ['greek', 'souvlaki', 'gyro'],
}
```

### Add New Location

```python
LOCATION_PATTERNS = {
    # ... existing locations ...
    'Sentosa': ['sentosa', 'resort world'],
}
```

### Add Custom Tag Category

```python
# In the AutoTagger class, add:
PARKING_TAGS = {
    'Parking Available': ['parking', 'valet', 'car park'],
    'MRT Nearby': ['mrt', 'station nearby', 'accessible'],
}

# Then in extract_tags method:
for tag, keywords in cls.PARKING_TAGS.items():
    if any(kw in combined for kw in keywords):
        tags.add(tag)
```

After updating, re-tag all restaurants:
```bash
curl -X POST http://localhost:5000/api/restaurants/bulk-retag
```

## 📊 Monitoring Tag Quality

### Check What Tags Are Being Extracted

```python
# View logs when adding restaurants
# Your Flask console will show:
# Extracted tags for 'Restaurant Name': ['Tag1', 'Tag2', 'Tag3']
```

### Review Tags in Database

```sql
-- See all tags and their usage
SELECT t.name, COUNT(pt.place_id) as restaurant_count
FROM tags t
LEFT JOIN place_tags pt ON t.id = pt.tag_id
GROUP BY t.name
ORDER BY restaurant_count DESC;
```

### Test Tag Extraction Before Adding

```python
from app_with_autotagging import AutoTagger

tags = AutoTagger.extract_tags(
    name="Test Restaurant",
    address="Test Address",
    description="Test Description"
)
print("Would extract:", tags)
```

## 🔧 Maintenance Tasks

### Weekly: Review New Tags

Check if new tags are being created as expected:

```sql
SELECT name, created_at 
FROM tags 
WHERE created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;
```

### Monthly: Optimize Tag Rules

1. Look at underused tags (< 5 restaurants)
2. Combine similar tags
3. Add more keywords to popular cuisines

### When Updating Tag Logic

Always re-tag after changes:

```bash
# After editing CUISINE_KEYWORDS, LOCATION_PATTERNS, etc.
curl -X POST http://localhost:5000/api/restaurants/bulk-retag
```

## 🐛 Troubleshooting

### "No tags extracted"

**Cause:** Restaurant info doesn't match any keywords

**Solution:** 
1. Add more keywords to tag categories
2. Provide richer descriptions
3. Check the extraction logic

### "Too many generic tags"

**Cause:** Keywords too broad

**Solution:** Make keywords more specific:
```python
# Before (too broad)
'Casual': ['casual']

# After (more specific)
'Casual': ['casual dining', 'relaxed atmosphere', 'laid-back']
```

### "Restaurant already exists" error

**Cause:** Duplicate name + address combination

**Solution:** Update instead of insert, or use different address

## 📈 Scaling Considerations

### For 10,000+ Restaurants

Use batch processing:

```python
import requests

restaurants = load_large_dataset()  # Your 10k restaurants

batch_size = 100
for i in range(0, len(restaurants), batch_size):
    batch = restaurants[i:i+batch_size]
    for restaurant in batch:
        requests.post('/api/restaurants', json=restaurant)
    print(f"Processed {i+batch_size}/{len(restaurants)}")
```

### For High-Traffic Apps

Consider:
1. Caching extracted tags
2. Async tag extraction
3. Tag extraction queue
4. Database connection pooling

## 🎓 Advanced Features

### AI-Powered Tag Extraction

Enhance tagging with Gemini AI:

```python
def ai_extract_tags(name, description):
    prompt = f"""
    Extract relevant tags from this restaurant:
    Name: {name}
    Description: {description}
    
    Return tags as JSON array for: cuisine, location, price, atmosphere
    """
    response = model.generate_content(prompt)
    return json.loads(response.text)
```

### User Feedback Loop

Let users suggest tags:

```python
@app.route('/api/restaurants/<id>/suggest-tag', methods=['POST'])
def suggest_tag(id):
    tag = request.json.get('tag')
    # Add to suggestions table for review
    # Auto-add if confidence > threshold
```

## 📚 Next Steps

1. ✅ Verify auto-tagging works with test script
2. ✅ Re-tag existing restaurants
3. ✅ Customize tag rules for your needs
4. ✅ Set up monitoring
5. ✅ Train team on new API endpoints

## 🎉 Success Metrics

You'll know it's working when:
- ✅ New restaurants automatically get 3-5 relevant tags
- ✅ No manual tagging needed
- ✅ Chatbot finds restaurants based on extracted tags
- ✅ Tag quality stays consistent over time

## 💡 Tips

1. **Rich descriptions = Better tags**: Always include descriptions when adding restaurants
2. **Review first 10**: Check first 10 auto-tagged restaurants to validate logic
3. **Iterate on keywords**: Update tag keywords based on your specific data
4. **Document custom tags**: Keep a list of your custom tag categories

---

**Questions?** Check `AUTO_TAGGING_API_DOCS.md` for detailed API documentation.
