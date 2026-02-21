# backend/tagging.py

TYPE_TO_TAG = {
    "bakery": ["dessert"],
    "cafe": ["cafe", "dessert"],
    "meal_takeaway": ["fast_food"],
    "meal_delivery": ["fast_food"],
    "bar": ["bar"],
    "night_club": ["bar"],
}

KEYWORDS = {
    "dessert": [
        "gelato", "ice cream", "bingsu", "cake", "patisserie", "pastry", "tart",
        "dessert", "bakery", "waffle", "pancake", "crepe", "brownie", "cookie",
        "churros", "sundae", "parfait", "soft serve", "froyo", "frozen yogurt"
    ],

    "bubble_tea": [
        "bubble tea", "boba", "milk tea", "gong cha", "koi", "liho", "chicha",
        "playmade", "heytea", "chagee", "sharetea", "tealive"
    ],

    "japanese": [
        "ramen", "sushi", "omakase", "donburi", "don", "yakitori", "izakaya",
        "soba", "udon", "tempura", "teppanyaki", "sashimi", "katsu", "tonkatsu",
        "unagi", "gyudon", "handroll", "kaiseki"
    ],

    "korean": [
        "kbbq", "korean bbq", "kimchi", "bibimbap", "tteokbokki", "samgyetang",
        "jajangmyeon", "jjajangmyeon", "gimbap", "bulgogi", "kimchi jjigae",
        "soondubu", "naengmyeon"
    ],

    "indian": [
        "biryani", "briyani", "tandoori", "naan", "curry", "masala", "dosa",
        "roti prata", "prata", "thosai", "mutton", "paneer", "bhel", "chaat"
    ],

    "mala": [
        "mala", "麻辣", "hotpot", "hot pot", "steamboat", "xiang guo", "xiangguo",
        "麻辣香锅", "dry pot", "spicy pot"
    ],

    "western": [
        "grill", "steak", "steakhouse", "bbq", "barbecue", "smokehouse",
        "burger", "burgers", "rib", "ribs", "wings", "roast", "roasted",
        "bistro", "brasserie", "diner", "tavern", "gastropub", "pub",
        "chophouse", "charcoal", "woodfire", "wood-fired"
    ],

    "chinese": [
        "chinese", "sichuan", "szechuan", "cantonese", "teochew", "hokkien",
        "yunnan", "xinjiang", "dongbei", "northeast", "hunan", "shanghai",
        "hotpot", "hot pot", "steamboat", "xiang guo", "mala", "麻辣",
        "dim sum", "dimsum", "xiaolongbao", "xiao long bao", "dumpling", "dumplings",
        "wantan", "wonton", "char siew", "charsiew", "roast duck",
        "congee", "porridge", "la mian", "lamian", "noodle", "noodles",
        "zi char", "tze char", "seafood"
    ],

    "italian": [
        "italian", "trattoria", "osteria", "pizzeria", "pizza", "pasta",
        "spaghetti", "lasagna", "lasagne", "risotto", "gnocchi",
        "carbonara", "bolognese", "margherita", "prosciutto",
        "gelato", "tiramisu", "focaccia", "bruschetta"
    ],

    "mexican": [
        "mexican", "taco", "tacos", "taqueria", "burrito", "burritos",
        "quesadilla", "quesadillas", "nacho", "nachos", "salsa", "guacamole",
        "fajita", "fajitas", "enchilada", "enchiladas", "tortilla",
        "cantina", "tex-mex", "tex mex"
    ],

    # halal is tricky — only tag if strongly indicated
    "halal": [
        "halal", "muslim-friendly", "muslim friendly", "no pork no lard",
        "nasi padang", "padang", "ayam penyet", "satay", "sate",
        "murtabak", "kebab", "shawarma", "mamak", "kampung",
        "warong", "warung", "hajah", "haji"
    ],
}

def auto_tags_from_google(details: dict) -> list[str]:
    tags = set()

    # 1) from google types
    for t in details.get("types", []) or []:
        for mapped in TYPE_TO_TAG.get(t, []):
            tags.add(mapped)

    # 2) from keywords in name
    name = (details.get("name") or "").lower()
    for tag, words in KEYWORDS.items():
        if any(w in name for w in words):
            tags.add(tag)

    return sorted(tags)