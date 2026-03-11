"""
location_data.py
----------------
Static Singapore geographic data for location-aware tag enrichment.

Contains:
  - MRT/LRT station coordinates (all lines as of 2024)
  - Planning area adjacency map (URA 55 planning areas)
  - Postal district → planning area mapping
"""

from __future__ import annotations

# ── MRT / LRT Stations ─────────────────────────────────────────────────────────
# Format: (station_name, latitude, longitude)
# Name is used directly as a tag, e.g. "Near Bugis MRT"

MRT_STATIONS: list[tuple[str, float, float]] = [
    # === North-South Line (NSL) ===
    ("Jurong East", 1.3332, 103.7422),
    ("Bukit Batok", 1.3491, 103.7496),
    ("Bukit Gombak", 1.3587, 103.7518),
    ("Choa Chu Kang", 1.3854, 103.7443),
    ("Yew Tee", 1.3970, 103.7477),
    ("Kranji", 1.4252, 103.7619),
    ("Marsiling", 1.4327, 103.7742),
    ("Woodlands", 1.4370, 103.7864),
    ("Admiralty", 1.4408, 103.8006),
    ("Sembawang", 1.4490, 103.8199),
    ("Canberra", 1.4431, 103.8298),
    ("Yishun", 1.4293, 103.8353),
    ("Khatib", 1.4175, 103.8330),
    ("Yio Chu Kang", 1.3817, 103.8448),
    ("Ang Mo Kio", 1.3700, 103.8495),
    ("Bishan", 1.3510, 103.8485),
    ("Braddell", 1.3404, 103.8468),
    ("Toa Payoh", 1.3328, 103.8474),
    ("Novena", 1.3203, 103.8437),
    ("Newton", 1.3133, 103.8386),
    ("Orchard", 1.3044, 103.8319),
    ("Somerset", 1.3006, 103.8390),
    ("Dhoby Ghaut", 1.2990, 103.8455),
    ("City Hall", 1.2931, 103.8520),
    ("Raffles Place", 1.2835, 103.8514),
    ("Marina Bay", 1.2764, 103.8545),
    ("Marina South Pier", 1.2710, 103.8636),

    # === East-West Line (EWL) ===
    ("Pasir Ris", 1.3731, 103.9493),
    ("Tampines", 1.3540, 103.9454),
    ("Simei", 1.3432, 103.9530),
    ("Tanah Merah", 1.3273, 103.9462),
    ("Bedok", 1.3240, 103.9299),
    ("Kembangan", 1.3207, 103.9129),
    ("Eunos", 1.3196, 103.9032),
    ("Paya Lebar", 1.3180, 103.8923),
    ("Aljunied", 1.3162, 103.8832),
    ("Kallang", 1.3115, 103.8716),
    ("Lavender", 1.3073, 103.8634),
    ("Bugis", 1.3009, 103.8564),
    ("City Hall", 1.2931, 103.8520),  # duplicate — skip in dedup
    ("Raffles Place", 1.2835, 103.8514),
    ("Tanjong Pagar", 1.2764, 103.8462),
    ("Outram Park", 1.2801, 103.8398),
    ("Tiong Bahru", 1.2863, 103.8269),
    ("Redhill", 1.2895, 103.8166),
    ("Queenstown", 1.2942, 103.8060),
    ("Commonwealth", 1.3021, 103.7983),
    ("Buona Vista", 1.3072, 103.7904),
    ("Dover", 1.3113, 103.7784),
    ("Clementi", 1.3152, 103.7649),
    ("Chinese Garden", 1.3424, 103.7323),
    ("Lakeside", 1.3444, 103.7209),
    ("Boon Lay", 1.3388, 103.7059),
    ("Pioneer", 1.3369, 103.6971),
    ("Joo Koon", 1.3278, 103.6783),
    ("Gul Circle", 1.3196, 103.6611),
    ("Tuas Crescent", 1.3212, 103.6490),
    ("Tuas West Road", 1.3294, 103.6395),
    ("Tuas Link", 1.3408, 103.6374),
    ("Expo", 1.3353, 103.9614),
    ("Changi Airport", 1.3572, 103.9884),

    # === Circle Line (CCL) ===
    ("Dhoby Ghaut", 1.2990, 103.8455),
    ("Bras Basah", 1.2963, 103.8502),
    ("Esplanade", 1.2934, 103.8552),
    ("Promenade", 1.2934, 103.8610),
    ("Nicoll Highway", 1.2998, 103.8637),
    ("Stadium", 1.3064, 103.8751),
    ("Mountbatten", 1.3064, 103.8819),
    ("Dakota", 1.3088, 103.8882),
    ("Paya Lebar", 1.3180, 103.8923),
    ("MacPherson", 1.3264, 103.8897),
    ("Tai Seng", 1.3351, 103.8873),
    ("Bartley", 1.3424, 103.8798),
    ("Serangoon", 1.3499, 103.8732),
    ("Lorong Chuan", 1.3518, 103.8648),
    ("Bishan", 1.3510, 103.8485),
    ("Marymount", 1.3490, 103.8393),
    ("Caldecott", 1.3373, 103.8392),
    ("Botanic Gardens", 1.3228, 103.8150),
    ("Farrer Road", 1.3175, 103.8075),
    ("Holland Village", 1.3119, 103.7964),
    ("Buona Vista", 1.3072, 103.7904),
    ("one-north", 1.2990, 103.7878),
    ("Kent Ridge", 1.2929, 103.7841),
    ("Haw Par Villa", 1.2831, 103.7819),
    ("Pasir Panjang", 1.2764, 103.7919),
    ("Labrador Park", 1.2723, 103.8023),
    ("Telok Blangah", 1.2706, 103.8098),
    ("HarbourFront", 1.2655, 103.8218),
    ("Bayfront", 1.2828, 103.8591),
    ("Marina Bay", 1.2764, 103.8545),

    # === Downtown Line (DTL) ===
    ("Bukit Panjang", 1.3784, 103.7763),
    ("Cashew", 1.3699, 103.7839),
    ("Hillview", 1.3629, 103.7671),
    ("Beauty World", 1.3412, 103.7756),
    ("King Albert Park", 1.3348, 103.7783),
    ("Sixth Avenue", 1.3302, 103.7964),
    ("Tan Kah Kee", 1.3261, 103.8075),
    ("Botanic Gardens", 1.3228, 103.8150),
    ("Stevens", 1.3198, 103.8266),
    ("Newton", 1.3133, 103.8386),
    ("Little India", 1.3066, 103.8491),
    ("Rochor", 1.3044, 103.8523),
    ("Bugis", 1.3009, 103.8564),
    ("Promenade", 1.2934, 103.8610),
    ("Bayfront", 1.2828, 103.8591),
    ("Downtown", 1.2793, 103.8526),
    ("Telok Ayer", 1.2823, 103.8484),
    ("Chinatown", 1.2845, 103.8436),
    ("Fort Canning", 1.2923, 103.8438),
    ("Bencoolen", 1.2984, 103.8497),
    ("Jalan Besar", 1.3052, 103.8554),
    ("Bendemeer", 1.3139, 103.8622),
    ("Geylang Bahru", 1.3218, 103.8712),
    ("Mattar", 1.3271, 103.8828),
    ("MacPherson", 1.3264, 103.8897),
    ("Ubi", 1.3291, 103.8991),
    ("Kaki Bukit", 1.3352, 103.9091),
    ("Bedok North", 1.3344, 103.9192),
    ("Bedok Reservoir", 1.3368, 103.9321),
    ("Tampines West", 1.3468, 103.9384),
    ("Tampines", 1.3540, 103.9454),
    ("Tampines East", 1.3575, 103.9531),
    ("Upper Changi", 1.3417, 103.9613),
    ("Expo", 1.3353, 103.9614),

    # === Thomson–East Coast Line (TEL) ===
    ("Woodlands North", 1.4479, 103.8200),
    ("Woodlands", 1.4370, 103.7864),
    ("Woodlands South", 1.4268, 103.7950),
    ("Springleaf", 1.3988, 103.8178),
    ("Lentor", 1.3887, 103.8349),
    ("Mayflower", 1.3757, 103.8388),
    ("Bright Hill", 1.3637, 103.8370),
    ("Upper Thomson", 1.3567, 103.8309),
    ("Caldecott", 1.3373, 103.8392),
    ("Stevens", 1.3198, 103.8266),
    ("Napier", 1.3074, 103.8217),
    ("Orchard Boulevard", 1.3009, 103.8248),
    ("Orchard", 1.3044, 103.8319),
    ("Great World", 1.2948, 103.8236),
    ("Havelock", 1.2891, 103.8354),
    ("Outram Park", 1.2801, 103.8398),
    ("Maxwell", 1.2800, 103.8444),
    ("Shenton Way", 1.2773, 103.8483),
    ("Marina Bay", 1.2764, 103.8545),
    ("Gardens by the Bay", 1.2810, 103.8650),
    ("Tanjong Rhu", 1.2994, 103.8724),
    ("Katong Park", 1.3048, 103.8820),
    ("Tanjong Katong", 1.3032, 103.8980),
    ("Marine Parade", 1.3026, 103.9066),
    ("Marine Terrace", 1.3055, 103.9143),
    ("Siglap", 1.3097, 103.9272),
    ("Bayshore", 1.3151, 103.9400),
    ("Bedok South", 1.3212, 103.9450),
    ("Sungei Bedok", 1.3265, 103.9557),

    # === North-East Line (NEL) ===
    ("HarbourFront", 1.2655, 103.8218),
    ("Outram Park", 1.2801, 103.8398),
    ("Chinatown", 1.2845, 103.8436),
    ("Clarke Quay", 1.2884, 103.8464),
    ("Dhoby Ghaut", 1.2990, 103.8455),
    ("Little India", 1.3066, 103.8491),
    ("Farrer Park", 1.3123, 103.8542),
    ("Boon Keng", 1.3197, 103.8617),
    ("Potong Pasir", 1.3313, 103.8693),
    ("Woodleigh", 1.3392, 103.8710),
    ("Serangoon", 1.3499, 103.8732),
    ("Kovan", 1.3599, 103.8851),
    ("Hougang", 1.3712, 103.8920),
    ("Buangkok", 1.3832, 103.8928),
    ("Sengkang", 1.3916, 103.8954),
    ("Punggol", 1.4053, 103.9022),

    # === Jurong Region Line (JRL, partial) ===
    ("Boon Lay", 1.3388, 103.7059),
    ("Gek Poh", 1.3445, 103.6958),
    ("Tawas", 1.3499, 103.6876),
]

# Deduplicate by name (keep first occurrence)
_seen: set[str] = set()
_deduped: list[tuple[str, float, float]] = []
for _entry in MRT_STATIONS:
    if _entry[0] not in _seen:
        _seen.add(_entry[0])
        _deduped.append(_entry)
MRT_STATIONS = _deduped


# ── Planning Area Adjacency Map ────────────────────────────────────────────────
# Each key maps to a set of directly-adjacent planning areas.
# Derived from URA Master Plan boundaries.
# Used for query expansion: "user asked for Tampines → also search Simei, Pasir Ris"

PLANNING_AREA_NEIGHBORS: dict[str, list[str]] = {
    # ── CBD / City ─────────────────────────────────────────────────────────────
    "Raffles Place":    ["Tanjong Pagar", "Chinatown", "City Hall", "Marina Bay", "Clarke Quay"],
    "Marina Bay":       ["Raffles Place", "City Hall", "Tanjong Pagar"],
    "Tanjong Pagar":    ["Raffles Place", "Chinatown", "Tiong Bahru", "Harbourfront"],
    "Chinatown":        ["Tanjong Pagar", "Raffles Place", "Clarke Quay", "Tiong Bahru"],
    "City Hall":        ["Raffles Place", "Marina Bay", "Bugis", "Clarke Quay"],
    "Clarke Quay":      ["Raffles Place", "City Hall", "Robertson Quay", "Chinatown"],
    "Robertson Quay":   ["Clarke Quay", "Orchard", "Tiong Bahru", "Tanjong Pagar"],
    # ── Orchard / River Valley ────────────────────────────────────────────────
    "Orchard":          ["Newton", "Tanglin", "Robertson Quay", "Novena"],
    "Tanglin":          ["Orchard", "Newton", "Holland Village", "Bukit Timah"],
    "Holland Village":  ["Tanglin", "Bukit Timah", "Queenstown", "Clementi"],
    # ── Bugis / Kampong Glam / Little India ───────────────────────────────────
    "Bugis":            ["City Hall", "Kampong Glam", "Little India", "Kallang"],
    "Kampong Glam":     ["Bugis", "Little India", "Kallang"],
    "Little India":     ["Bugis", "Kampong Glam", "Novena", "Kallang"],
    # ── Newton / Novena / Balestier ───────────────────────────────────────────
    "Newton":           ["Orchard", "Novena", "Tanglin", "Bukit Timah"],
    "Novena":           ["Newton", "Toa Payoh", "Balestier", "Bishan", "Bukit Timah"],
    "Balestier":        ["Novena", "Toa Payoh", "MacPherson", "Little India"],
    # ── Upper Thomson / Mandai ────────────────────────────────────────────────
    "Upper Thomson":    ["Novena", "Bishan", "Ang Mo Kio", "Mandai"],
    "Mandai":           ["Upper Thomson", "Woodlands", "Yishun", "Ang Mo Kio"],
    # ── Toa Payoh / Bishan ────────────────────────────────────────────────────
    "Toa Payoh":        ["Novena", "Bishan", "Ang Mo Kio", "Kallang", "MacPherson"],
    "Bishan":           ["Ang Mo Kio", "Toa Payoh", "Novena", "Serangoon", "Upper Thomson"],
    # ── Kallang / MacPherson / Potong Pasir ───────────────────────────────────
    "Kallang":          ["Bugis", "Geylang", "MacPherson", "Toa Payoh", "Marine Parade"],
    "MacPherson":       ["Kallang", "Geylang", "Toa Payoh", "Balestier", "Paya Lebar", "Potong Pasir"],
    "Potong Pasir":     ["MacPherson", "Serangoon", "Toa Payoh", "Geylang"],
    # ── Geylang / Eunos / Paya Lebar ──────────────────────────────────────────
    "Geylang":          ["Kallang", "MacPherson", "Paya Lebar", "Eunos", "Joo Chiat"],
    "Eunos":            ["Geylang", "Paya Lebar", "Kembangan", "Joo Chiat"],
    "Paya Lebar":       ["Geylang", "Serangoon", "Hougang", "Eunos"],
    # ── Joo Chiat / Katong / Marine Parade ────────────────────────────────────
    "Joo Chiat":        ["Geylang", "Katong", "Eunos"],
    "Katong":           ["Joo Chiat", "Marine Parade", "Bedok"],
    "Marine Parade":    ["Katong", "Kallang", "Bedok"],
    # ── Bedok / Upper East Coast ──────────────────────────────────────────────
    "Bedok":            ["Tampines", "Pasir Ris", "Marine Parade", "Katong"],
    # ── Tampines / Pasir Ris / Changi ─────────────────────────────────────────
    "Tampines":         ["Pasir Ris", "Bedok", "Changi", "Sengkang"],
    "Pasir Ris":        ["Tampines", "Changi", "Sengkang"],
    "Changi":           ["Tampines", "Pasir Ris", "Bedok"],
    # ── Serangoon / Hougang / Sengkang / Punggol ──────────────────────────────
    "Serangoon":        ["Ang Mo Kio", "Bishan", "Hougang", "Sengkang", "Paya Lebar", "Potong Pasir"],
    "Hougang":          ["Ang Mo Kio", "Serangoon", "Sengkang", "Punggol"],
    "Sengkang":         ["Hougang", "Punggol", "Pasir Ris", "Serangoon"],
    "Punggol":          ["Hougang", "Sengkang"],
    # ── Ang Mo Kio / Yio Chu Kang / Seletar ──────────────────────────────────
    "Ang Mo Kio":       ["Bishan", "Toa Payoh", "Serangoon", "Yio Chu Kang", "Hougang", "Upper Thomson"],
    "Yio Chu Kang":     ["Ang Mo Kio", "Hougang", "Seletar", "Serangoon"],
    "Seletar":          ["Yio Chu Kang", "Sengkang", "Ang Mo Kio"],
    # ── North ─────────────────────────────────────────────────────────────────
    "Woodlands":        ["Mandai", "Choa Chu Kang", "Bukit Panjang", "Lim Chu Kang", "Sembawang"],
    "Sembawang":        ["Woodlands", "Yishun"],
    "Yishun":           ["Mandai", "Sembawang", "Ang Mo Kio"],
    # ── West ──────────────────────────────────────────────────────────────────
    "Queenstown":       ["Tiong Bahru", "Clementi", "Buona Vista", "Holland Village", "Alexandra"],
    "Buona Vista":      ["Queenstown", "Clementi", "West Coast", "Pasir Panjang"],
    "Clementi":         ["Bukit Timah", "Bukit Batok", "Queenstown", "Buona Vista", "Jurong East"],
    "Tiong Bahru":      ["Chinatown", "Tanjong Pagar", "Queenstown", "Alexandra", "Robertson Quay"],
    "Alexandra":        ["Tiong Bahru", "Queenstown", "Harbourfront", "Buona Vista"],
    "Harbourfront":     ["Tanjong Pagar", "Alexandra", "Pasir Panjang"],
    "Pasir Panjang":    ["Harbourfront", "Buona Vista", "West Coast"],
    "West Coast":       ["Clementi", "Queenstown", "Buona Vista", "Pasir Panjang"],
    "Bukit Timah":      ["Newton", "Tanglin", "Holland Village", "Bukit Batok", "Bukit Panjang", "Clementi"],
    "Bukit Batok":      ["Bukit Timah", "Clementi", "Jurong East", "Choa Chu Kang", "Bukit Panjang"],
    "Bukit Panjang":    ["Choa Chu Kang", "Bukit Timah", "Bukit Batok", "Woodlands"],
    "Choa Chu Kang":    ["Bukit Batok", "Bukit Panjang", "Woodlands", "Tengah", "Jurong West"],
    "Jurong East":      ["Jurong West", "Bukit Batok", "Clementi"],
    "Jurong West":      ["Jurong East", "Choa Chu Kang", "Tuas"],
    "Tengah":           ["Bukit Batok", "Choa Chu Kang", "Jurong East"],
    "Tuas":             ["Jurong West"],
    "Lim Chu Kang":     ["Woodlands", "Choa Chu Kang"],
}

# ── Postal District → Planning Area ───────────────────────────────────────────
# First two digits of Singapore postal code → rough planning area group
# Useful for extracting area from formatted address strings

POSTAL_DISTRICT_TO_AREA: dict[str, str] = {
    # ── D01: Raffles Place, Cecil, Marina, People's Park ──────────────────────
    # 01-03: CBD core (Cecil St, Robinson, South Bridge, Raffles Place)
    # 04-05: Marina/Suntec end (Republic Blvd, Temasek Blvd)
    # 06: People's Park / Chinatown fringe
    "01": "Raffles Place",  "02": "Raffles Place",  "03": "Raffles Place",
    "04": "Marina Bay",     "05": "Marina Bay",     "06": "Chinatown",
    # ── D02: Anson, Tanjong Pagar ─────────────────────────────────────────────
    "07": "Tanjong Pagar",  "08": "Tanjong Pagar",
    # ── D03: Alexandra, Bukit Merah, Queenstown, Tiong Bahru ─────────────────
    # 14: Tiong Bahru / Kim Tian / Havelock
    # 15: Queenstown / Commonwealth / Redhill
    # 16: Alexandra / Bukit Merah / Delta
    "14": "Tiong Bahru",    "15": "Queenstown",     "16": "Alexandra",
    # ── D04: Harbourfront, Telok Blangah ──────────────────────────────────────
    "09": "Harbourfront",   "10": "Harbourfront",
    # ── D05: Buona Vista, Pasir Panjang, West Coast ───────────────────────────
    # 11: Buona Vista / one-north
    # 12-13: Pasir Panjang / West Coast
    "11": "Buona Vista",    "12": "Pasir Panjang",  "13": "Pasir Panjang",
    # ── D06: City Hall, Clarke Quay ───────────────────────────────────────────
    "17": "City Hall",
    # ── D07: Bugis, Beach Road ────────────────────────────────────────────────
    "18": "Bugis",          "19": "Bugis",
    # ── D08: Little India, Farrer Park ────────────────────────────────────────
    "20": "Little India",   "21": "Little India",
    # ── D09: Orchard, River Valley ────────────────────────────────────────────
    "22": "Orchard",        "23": "River Valley",
    # ── D10: Holland, Tanglin, Dempsey ────────────────────────────────────────
    # 24-25: Tanglin / Cuscaden / Stevens
    # 26-27: Holland Village / Farrer Road / Sixth Ave
    "24": "Tanglin",        "25": "Tanglin",
    "26": "Holland Village","27": "Holland Village",
    # ── D11: Newton, Novena, Thomson ──────────────────────────────────────────
    # 28: Newton / Novena core
    # 29-30: Upper Thomson / Marymount fringe
    "28": "Novena",         "29": "Newton",         "30": "Newton",
    # ── D12: Balestier, Toa Payoh ─────────────────────────────────────────────
    "31": "Balestier",      "32": "Toa Payoh",      "33": "Toa Payoh",
    # ── D13: MacPherson, Potong Pasir, Kallang ────────────────────────────────
    "34": "MacPherson",     "35": "Kallang",        "36": "MacPherson",
    "37": "Potong Pasir",
    # ── D14: Geylang, Paya Lebar, Eunos ──────────────────────────────────────
    "38": "Geylang",        "39": "Geylang",        "40": "Paya Lebar",
    "41": "Eunos",
    # ── D15: Katong, Joo Chiat, Marine Parade ─────────────────────────────────
    # 42-43: Joo Chiat / Katong core
    # 44-45: Marine Parade / Siglap / East Coast Road
    "42": "Joo Chiat",      "43": "Katong",
    "44": "Marine Parade",  "45": "Marine Parade",
    # ── D16: Bedok, Upper East Coast ──────────────────────────────────────────
    "46": "Bedok",          "47": "Bedok",          "48": "Bedok",
    # ── D17: Loyang, Changi ───────────────────────────────────────────────────
    "49": "Changi",         "50": "Changi",         "81": "Changi",
    # ── D18: Tampines, Pasir Ris ──────────────────────────────────────────────
    "51": "Tampines",       "52": "Pasir Ris",
    # ── D19: Serangoon, Hougang, Sengkang ─────────────────────────────────────
    "53": "Hougang",        "54": "Serangoon",      "55": "Serangoon",
    "82": "Sengkang",
    # ── D20: Ang Mo Kio, Bishan ───────────────────────────────────────────────
    "56": "Bishan",         "57": "Ang Mo Kio",
    # ── D21: Clementi, Upper Bukit Timah ──────────────────────────────────────
    "58": "Clementi",       "59": "Clementi",
    # ── D22: Jurong East, Jurong West, Boon Lay ───────────────────────────────
    "60": "Jurong East",    "61": "Jurong West",    "62": "Jurong West",
    "63": "Jurong West",    "64": "Jurong East",
    # ── D23: Bukit Batok, Choa Chu Kang, Hillview ────────────────────────────
    "65": "Bukit Batok",    "66": "Bukit Batok",    "67": "Choa Chu Kang",
    "68": "Bukit Panjang",
    # ── D24: Lim Chu Kang, Tengah ─────────────────────────────────────────────
    "69": "Tengah",         "70": "Lim Chu Kang",   "71": "Lim Chu Kang",
    # ── D25: Woodlands, Kranji ────────────────────────────────────────────────
    "72": "Woodlands",      "73": "Woodlands",
    # ── D26: Mandai, Upper Thomson, Springleaf ────────────────────────────────
    "77": "Upper Thomson",  "78": "Mandai",
    # ── D27: Sembawang, Yishun ────────────────────────────────────────────────
    "75": "Yishun",         "76": "Sembawang",
    # ── D28: Seletar, Yio Chu Kang ────────────────────────────────────────────
    "79": "Yio Chu Kang",   "80": "Seletar",
}

# ── Common Singapore Area Keywords ────────────────────────────────────────────
# These are subzone/locality names that appear in addresses but may not be
# planning areas. We normalise them to a parent planning area.

AREA_ALIAS_TO_PLANNING_AREA: dict[str, str] = {
    # ── CBD / Marina / City ────────────────────────────────────────────────────
    "raffles place":        "Raffles Place",
    "shenton way":          "Raffles Place",
    "tanjong pagar plaza":  "Tanjong Pagar",
    "robinson road":        "Raffles Place",
    "battery road":         "Raffles Place",
    "telok ayer":           "Raffles Place",       # Telok Ayer St is CBD-side
    "amoy street":          "Raffles Place",
    "club street":          "Tanjong Pagar",
    "ann siang":            "Tanjong Pagar",
    "tanjong pagar":        "Tanjong Pagar",
    "chinatown":            "Chinatown",
    "new bridge road":      "Chinatown",
    "smith street":         "Chinatown",
    "temple street":        "Chinatown",
    "pagoda street":        "Chinatown",
    "eu tong sen":          "Chinatown",
    "marina bay sands":     "Marina Bay",
    "marina bay":           "Marina Bay",
    "marina centre":        "Marina Bay",
    "suntec":               "Marina Bay",
    "esplanade":            "City Hall",
    "city hall":            "City Hall",
    "boat quay":            "Clarke Quay",
    "clarke quay":          "Clarke Quay",
    "clark quay":           "Clarke Quay",
    "robertson quay":       "Robertson Quay",
    "river valley":         "Robertson Quay",
    "fort canning":         "Clarke Quay",
    "bras basah":           "Bugis",
    # ── Kampong Glam / Bugis ──────────────────────────────────────────────────
    "kampong glam":         "Kampong Glam",
    "arab street":          "Kampong Glam",
    "haji lane":            "Kampong Glam",
    "sultan gate":          "Kampong Glam",
    "beach road":           "Bugis",
    "bugis":                "Bugis",
    "golden mile":          "Bugis",
    # ── Little India / Farrer Park ────────────────────────────────────────────
    "little india":         "Little India",
    "serangoon road":       "Little India",
    "farrer park":          "Little India",
    "mustafa":              "Little India",
    "tekka":                "Little India",
    "rochor":               "Bugis",
    # ── Orchard ───────────────────────────────────────────────────────────────
    "orchard road":         "Orchard",
    "orchard":              "Orchard",
    "somerset":             "Orchard",
    "cairnhill":            "Orchard",
    "scotts road":          "Orchard",
    "dhoby ghaut":          "Orchard",
    # ── River Valley / Robertson Quay ─────────────────────────────────────────
    "kim yam road":         "Robertson Quay",
    "martin road":          "Robertson Quay",
    "mohamed sultan":       "Robertson Quay",
    "great world":          "Robertson Quay",
    "kim seng":             "Orchard",
    # ── Tanglin / Dempsey ─────────────────────────────────────────────────────
    "tanglin":              "Tanglin",
    "dempsey":              "Tanglin",
    "cuscaden":             "Tanglin",
    "ardmore":              "Tanglin",
    "nassim":               "Tanglin",
    "stevens road":         "Tanglin",
    "anderson road":        "Tanglin",
    "draycott":             "Tanglin",
    # ── Holland Village ───────────────────────────────────────────────────────
    "holland village":      "Holland Village",
    "holland":              "Holland Village",
    "holland road":         "Holland Village",
    "ghim moh":             "Holland Village",
    "farrer road":          "Holland Village",
    "sixth avenue":         "Holland Village",
    "king albert park":     "Bukit Timah",
    "botanic gardens":      "Holland Village",
    "tan kah kee":          "Holland Village",
    # ── Bukit Timah ───────────────────────────────────────────────────────────
    "bukit timah":          "Bukit Timah",
    "beauty world":         "Bukit Timah",
    "upper bukit timah":    "Bukit Panjang",
    # ── Newton / Novena ───────────────────────────────────────────────────────
    "newton":               "Newton",
    "novena":               "Novena",
    "moulmein":             "Novena",
    "thomson road":         "Novena",
    "toa payoh rise":       "Novena",
    "whitley":              "Newton",
    "watten":               "Newton",
    "barker road":          "Newton",
    # ── Upper Thomson / Mandai ────────────────────────────────────────────────
    "upper thomson":        "Upper Thomson",
    "springleaf":           "Upper Thomson",
    "lentor":               "Upper Thomson",
    "thomson":              "Upper Thomson",
    "mandai":               "Mandai",
    # ── Balestier / Toa Payoh ─────────────────────────────────────────────────
    "balestier":            "Balestier",
    "boon keng":            "Balestier",
    "bendemeer":            "Balestier",
    "toa payoh":            "Toa Payoh",
    "braddell":             "Toa Payoh",
    "lorong 1 toa payoh":   "Toa Payoh",
    # ── Kallang / Lavender ────────────────────────────────────────────────────
    "kallang":              "Kallang",
    "lavender":             "Kallang",
    "geylang bahru":        "Kallang",
    "jalan besar":          "Little India",
    # ── MacPherson / Potong Pasir ─────────────────────────────────────────────
    "macpherson":           "MacPherson",
    "mac pherson":          "MacPherson",
    "potong pasir":         "Potong Pasir",
    "boon keng road":       "MacPherson",
    "ubi":                  "MacPherson",
    # ── Geylang / Eunos / Paya Lebar ──────────────────────────────────────────
    "geylang":              "Geylang",
    "aljunied":             "Geylang",
    "guillemard":           "Geylang",
    "dakota":               "Geylang",
    "eunos":                "Eunos",
    "kembangan":            "Eunos",
    "paya lebar":           "Paya Lebar",
    "jalan eunos":          "Eunos",
    "kaki bukit":           "Bedok",
    # ── Joo Chiat / Katong / Marine Parade ────────────────────────────────────
    "joo chiat":            "Joo Chiat",
    "east coast road":      "Joo Chiat",
    "tanjong katong":       "Katong",
    "amber road":           "Katong",
    "katong":               "Katong",
    "koon seng":            "Joo Chiat",
    "ceylon road":          "Katong",
    "marine parade":        "Marine Parade",
    "marine drive":         "Marine Parade",
    "marine terrace":       "Marine Parade",
    "east coast park":      "Marine Parade",
    "east coast":           "Marine Parade",
    "siglap":               "Marine Parade",
    "frankel":              "Marine Parade",
    "still road":           "Marine Parade",
    "mountbatten":          "Marine Parade",
    "tanjong rhu":          "Marine Parade",
    # ── Bedok ─────────────────────────────────────────────────────────────────
    "bedok":                "Bedok",
    "upper changi":         "Bedok",
    "new upper changi":     "Bedok",
    "chai chee":            "Bedok",
    # ── Tampines / Pasir Ris / Changi ─────────────────────────────────────────
    "tampines":             "Tampines",
    "simei":                "Tampines",
    "pasir ris":            "Pasir Ris",
    "changi":               "Changi",
    "loyang":               "Changi",
    "changi airport":       "Changi",
    "changi village":       "Changi",
    # ── Serangoon / Hougang / Sengkang / Punggol ──────────────────────────────
    "serangoon":            "Serangoon",
    "kovan":                "Serangoon",
    "lorong chuan":         "Serangoon",
    "upper serangoon":      "Serangoon",
    "hougang":              "Hougang",
    "buangkok":             "Hougang",
    "sengkang":             "Sengkang",
    "anchorvale":           "Sengkang",
    "compassvale":          "Sengkang",
    "punggol":              "Punggol",
    "rivervale":            "Sengkang",
    # ── Ang Mo Kio / Bishan ───────────────────────────────────────────────────
    "ang mo kio":           "Ang Mo Kio",
    "amk":                  "Ang Mo Kio",
    "bishan":               "Bishan",
    "marymount":            "Bishan",
    "sin ming":             "Bishan",
    "bright hill":          "Bishan",
    # ── Queenstown / Buona Vista / Tiong Bahru / Alexandra ────────────────────
    "queenstown":           "Queenstown",
    "commonwealth":         "Queenstown",
    "buona vista":          "Buona Vista",
    "one-north":            "Buona Vista",
    "one north":            "Buona Vista",
    "kent ridge":           "Buona Vista",
    "dover":                "Buona Vista",
    "tiong bahru":          "Tiong Bahru",
    "redhill":              "Tiong Bahru",
    "havelock":             "Tiong Bahru",
    "alexandra":            "Alexandra",
    "bukit merah":          "Alexandra",
    "delta avenue":         "Alexandra",
    # ── Harbourfront / Telok Blangah ──────────────────────────────────────────
    "harbourfront":         "Harbourfront",
    "telok blangah":        "Harbourfront",
    "sentosa":              "Harbourfront",
    "vivocity":             "Harbourfront",
    "mount faber":          "Harbourfront",
    "labrador":             "Pasir Panjang",
    "pasir panjang":        "Pasir Panjang",
    "haw par villa":        "Pasir Panjang",
    "west coast":           "West Coast",
    # ── Jurong / Boon Lay / West ──────────────────────────────────────────────
    "jurong east":          "Jurong East",
    "jurong west":          "Jurong West",
    "boon lay":             "Jurong West",
    "pioneer":              "Jurong West",
    "tuas":                 "Tuas",
    "clementi":             "Clementi",
    "clementi avenue":      "Clementi",
    "bukit batok":          "Bukit Batok",
    "bukit gombak":         "Bukit Batok",
    "choa chu kang":        "Choa Chu Kang",
    "cck":                  "Choa Chu Kang",
    "yew tee":              "Choa Chu Kang",
    "bukit panjang":        "Bukit Panjang",
    "hillview":             "Bukit Panjang",
    "cashew":               "Bukit Panjang",
    "tengah":               "Tengah",
    "lim chu kang":         "Lim Chu Kang",
    # ── North ─────────────────────────────────────────────────────────────────
    "woodlands":            "Woodlands",
    "marsiling":            "Woodlands",
    "woodgrove":            "Woodlands",
    "kranji":               "Woodlands",
    "admiralty":            "Sembawang",
    "sembawang":            "Sembawang",
    "canberra":             "Sembawang",
    "yishun":               "Yishun",
    "khatib":               "Yishun",
    # ── Yio Chu Kang / Seletar ────────────────────────────────────────────────
    "yio chu kang":         "Yio Chu Kang",
    "seletar":              "Seletar",
    "jalan kayu":           "Seletar",
    "fernvale":             "Sengkang",
}


def get_planning_area_from_postal(postal_code: str) -> str | None:
    """Return planning area from first 2 digits of a 6-digit postal code."""
    code = (postal_code or "").strip().lstrip("S").lstrip("s")
    if len(code) >= 2:
        return POSTAL_DISTRICT_TO_AREA.get(code[:2])
    return None


def get_expanded_location_tags(tag: str, depth: int = 1) -> list[str]:
    """
    Given a planning area tag, return it plus its neighbors (for query expansion).
    depth=1 → immediate neighbors only.
    depth=2 → neighbors of neighbors (can be very broad, use with caution).
    """
    tag_lower = tag.lower()

    # Try exact match
    canonical = next(
        (k for k in PLANNING_AREA_NEIGHBORS if k.lower() == tag_lower), None
    )
    if not canonical:
        # Try alias lookup
        alias = AREA_ALIAS_TO_PLANNING_AREA.get(tag_lower)
        canonical = alias

    if not canonical:
        return [tag]

    neighbors = PLANNING_AREA_NEIGHBORS.get(canonical, [])
    result = [canonical] + neighbors
    if depth > 1:
        second = []
        for n in neighbors:
            second.extend(PLANNING_AREA_NEIGHBORS.get(n, []))
        result = list(dict.fromkeys(result + second))  # deduplicate, order-preserving

    return result