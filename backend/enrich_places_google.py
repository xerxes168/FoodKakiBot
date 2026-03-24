#!/usr/bin/env python3
"""Enrich Supabase places with Google Places API tags (cuisine, budget, allergies).

This script:
1) Loads places from Supabase.
2) Calls Google Places Details for each place (by place_id or text query).
3) Infers cuisine/budget/allergy tags from the API response text.
4) Ensures tags exist and links them in place_tags.

Run without --apply to dry-run. Use --limit first.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv() -> bool:  # type: ignore[misc]
        return False

try:
    from supabase import Client, create_client
except Exception:
    Client = Any  # type: ignore[misc,assignment]
    create_client = None  # type: ignore[assignment]


CUISINE_KEYWORDS: Dict[str, Sequence[str]] = {
    "Japanese": (
        "japanese",
        "sushi",
        "sashimi",
        "ramen",
        "udon",
        "soba",
        "yakitori",
        "izakaya",
        "tempura",
        "tonkatsu",
        "donburi",
    ),
    "Korean": (
        "korean",
        "kimchi",
        "bibimbap",
        "bulgogi",
        "tteokbokki",
        "jjajangmyeon",
        "banchan",
        "samgyeopsal",
    ),
    "Chinese": (
        "chinese",
        "dim sum",
        "dumpling",
        "szechuan",
        "sichuan",
        "cantonese",
        "wonton",
        "xiao long bao",
        "char siew",
    ),
    "Mala": ("mala", "ma la", "spicy pot"),
    "Indian": (
        "indian",
        "biryani",
        "tandoori",
        "naan",
        "masala",
        "paneer",
        "roti prata",
        "thosai",
    ),
    "Malay": (
        "malay",
        "nasi lemak",
        "satay",
        "rendang",
        "mee rebus",
        "lontong",
    ),
    "Thai": ("thai", "tom yum", "green curry", "pad thai", "som tam"),
    "Vietnamese": ("vietnamese", "pho", "banh mi", "bun cha", "spring roll"),
    "Italian": ("italian", "pasta", "risotto", "pizza", "lasagna", "carbonara"),
    "French": ("french", "confit", "croissant", "escargot", "foie gras"),
    "Mexican": ("mexican", "taco", "burrito", "quesadilla", "nachos"),
    "Western": ("western", "steak", "burger", "fish and chips", "brunch"),
    "Seafood": ("seafood", "crab", "lobster", "oyster", "prawn", "clam"),
    "Vegetarian": ("vegetarian", "vegan", "plant-based", "meat-free"),
    "Halal": ("halal",),
    "Dessert": ("dessert", "cake", "pastry", "gelato", "ice cream", "sweet"),
    "Cafe": ("cafe", "coffee", "latte", "espresso", "brunch"),
    "Bubble Tea": ("bubble tea", "boba", "milk tea", "pearls"),
}

CUISINE_ALIASES: Dict[str, str] = {
    "jpn": "Japanese",
    "jp": "Japanese",
    "korea": "Korean",
    "chinese food": "Chinese",
    "veg": "Vegetarian",
    "western food": "Western",
}

ALLERGY_KEYWORDS: Dict[str, Sequence[str]] = {
    "Gluten-Free": ("gluten free", "gluten-free", "gf"),
    "Dairy-Free": ("dairy free", "dairy-free", "lactose free", "lactose-free", "no dairy"),
    "Nut-Free": ("nut free", "nut-free", "peanut-free", "no nuts", "tree nut"),
    "Shellfish-Free": ("shellfish free", "shellfish-free", "no shellfish"),
    "Egg-Free": ("egg free", "egg-free", "no egg"),
    "Soy-Free": ("soy free", "soy-free", "no soy"),
}

PLACE_TYPE_CUISINE_MAP: Dict[str, str] = {
    "japanese_restaurant": "Japanese",
    "korean_restaurant": "Korean",
    "chinese_restaurant": "Chinese",
    "indian_restaurant": "Indian",
    "thai_restaurant": "Thai",
    "vietnamese_restaurant": "Vietnamese",
    "italian_restaurant": "Italian",
    "french_restaurant": "French",
    "mexican_restaurant": "Mexican",
    "seafood_restaurant": "Seafood",
    "vegetarian_restaurant": "Vegetarian",
    "vegan_restaurant": "Vegetarian",
    "halal_restaurant": "Halal",
    "cafe": "Cafe",
    "coffee_shop": "Cafe",
    "dessert_shop": "Dessert",
    "ice_cream_shop": "Dessert",
    "bakery": "Dessert",
    "steak_house": "Western",
    "american_restaurant": "Western",
    "barbecue_restaurant": "Western",
    "bbq_restaurant": "Western",
}

PRICE_TAG_BUDGET = "Budget"
PRICE_TAG_MID_RANGE = "Mid Range"
PRICE_TAG_EXPENSIVE = "Expensive"
DEFAULT_CUISINE_TAG = "General"
TAG_CATEGORY_BUDGET = "budget"
TAG_CATEGORY_CUISINE = "cuisine"
TAG_CATEGORY_ALLERGY = "allergy"
TAG_CATEGORY_AREA = "area"

NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")
PRICE_NUMBER_RE = re.compile(r"\$(\d+(?:\.\d{1,2})?)")

LEGACY_DEFAULT_FIELDS = "place_id,name,formatted_address,types,price_level,reviews,editorial_summary"
PLACES_NEW_ESSENTIAL_FIELDS: Tuple[str, ...] = (
    "addressComponents",
    "addressDescriptor",
    "adrFormatAddress",
    "formattedAddress",
    "location",
    "plusCode",
    "postalAddress",
    "shortFormattedAddress",
    "types",
    "viewport",
)

AREA_COMPONENT_TYPES = (
    "neighborhood",
    "sublocality_level_1",
    "sublocality",
    "locality",
    "administrative_area_level_2",
)


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def normalize_tag_name(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    return text


def is_english_tag(value: str) -> bool:
    candidate = normalize_tag_name(value)
    if not candidate:
        return False
    if NON_ASCII_RE.search(candidate):
        return False
    return bool(re.search(r"[A-Za-z]", candidate))


def sanitize_area_candidate(value: str) -> Optional[str]:
    candidate = normalize_tag_name(value)
    if not candidate:
        return None
    if not is_english_tag(candidate):
        return None
    if candidate.lower() in {"singapore", "sg"}:
        return None
    if re.search(r"\d", candidate):
        return None
    if "," in candidate:
        return None
    if len(candidate) > 40:
        return None
    return candidate


def to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def canonical_cuisine_name(name: str) -> str:
    key = normalize_text(name)
    if key in CUISINE_ALIASES:
        return CUISINE_ALIASES[key]
    for cuisine in CUISINE_KEYWORDS:
        if normalize_text(cuisine) == key:
            return cuisine
    return name.strip()


def extract_reviews_text(raw_reviews: Any) -> str:
    if not raw_reviews:
        return ""
    texts: List[str] = []
    if isinstance(raw_reviews, dict) and "reviews" in raw_reviews:
        raw_reviews = raw_reviews["reviews"]
    if isinstance(raw_reviews, list):
        for item in raw_reviews:
            if isinstance(item, dict):
                review_text = str(item.get("text") or "").strip()
                if review_text:
                    texts.append(review_text)
            elif isinstance(item, str):
                if item.strip():
                    texts.append(item.strip())
    return "\n".join(texts)


def infer_cuisine_tags(text: str, label_name: str = "") -> List[str]:
    score: Dict[str, int] = {}
    norm_text = normalize_text(text)

    label = canonical_cuisine_name(label_name)
    if label in CUISINE_KEYWORDS:
        score[label] = score.get(label, 0) + 4

    for cuisine, keywords in CUISINE_KEYWORDS.items():
        for kw in keywords:
            kw_norm = normalize_text(kw)
            if not kw_norm:
                continue
            if kw_norm in norm_text:
                score[cuisine] = score.get(cuisine, 0) + (2 if " " in kw_norm else 1)

    ranked = sorted(score.items(), key=lambda pair: (-pair[1], pair[0]))
    selected: List[str] = []
    for cuisine, points in ranked:
        if points < 2:
            continue
        if is_english_tag(cuisine):
            selected.append(cuisine)
        if len(selected) >= 3:
            break
    return selected


def extract_place_types(details: Dict[str, Any]) -> List[str]:
    types: List[str] = []
    primary = details.get("primaryType")
    if isinstance(primary, str) and primary:
        types.append(primary)
    raw_types = details.get("types") or []
    if isinstance(raw_types, list):
        for value in raw_types:
            if isinstance(value, str) and value:
                types.append(value)
    return types


def infer_cuisine_tags_from_types(types: Sequence[str]) -> List[str]:
    matched: List[str] = []
    for value in types:
        key = normalize_text(value).replace(" ", "_")
        if key in PLACE_TYPE_CUISINE_MAP:
            matched.append(PLACE_TYPE_CUISINE_MAP[key])
    # Preserve order, remove duplicates
    seen: Set[str] = set()
    deduped: List[str] = []
    for tag in matched:
        if tag in seen:
            continue
        seen.add(tag)
        deduped.append(tag)
    return deduped


def infer_allergy_tags(text: str) -> List[str]:
    norm_text = normalize_text(text)
    matched: List[str] = []
    for tag_name, keywords in ALLERGY_KEYWORDS.items():
        for kw in keywords:
            if normalize_text(kw) in norm_text:
                matched.append(tag_name)
                break
    return matched


def extract_area_from_details(details: Dict[str, Any]) -> Optional[str]:
    comps = details.get("address_components")
    if isinstance(comps, list):
        for preferred_type in AREA_COMPONENT_TYPES:
            for comp in comps:
                types = set(comp.get("types") or [])
                if preferred_type in types:
                    candidate = str(comp.get("long_name") or "").strip()
                    if candidate:
                        return candidate

    comps_new = details.get("addressComponents")
    if isinstance(comps_new, list):
        for preferred_type in AREA_COMPONENT_TYPES:
            for comp in comps_new:
                types = set(comp.get("types") or [])
                if preferred_type in types:
                    candidate = str(comp.get("longText") or comp.get("shortText") or "").strip()
                    if candidate:
                        return candidate

    return None


def infer_area_tag(details: Dict[str, Any]) -> Optional[str]:
    raw = extract_area_from_details(details)
    return sanitize_area_candidate(raw or "")


def normalize_price_level(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str):
        mapping = {
            "PRICE_LEVEL_FREE": 0,
            "PRICE_LEVEL_INEXPENSIVE": 1,
            "PRICE_LEVEL_MODERATE": 2,
            "PRICE_LEVEL_EXPENSIVE": 3,
            "PRICE_LEVEL_VERY_EXPENSIVE": 4,
        }
        key = value.strip().upper()
        if key in mapping:
            return mapping[key]
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def infer_price_range_tag(price_level: Any, text: str) -> str:
    level = normalize_price_level(price_level)
    if level is not None:
        if level <= 1:
            return PRICE_TAG_BUDGET
        if level == 2:
            return PRICE_TAG_MID_RANGE
        return PRICE_TAG_EXPENSIVE

    norm_text = normalize_text(text)
    if any(token in norm_text for token in ("cheap", "affordable", "budget", "value for money", "wallet-friendly")):
        return PRICE_TAG_BUDGET
    if any(token in norm_text for token in ("expensive", "pricey", "premium", "high-end", "fine dining", "$$$")):
        return PRICE_TAG_EXPENSIVE
    if "$$" in norm_text:
        return PRICE_TAG_MID_RANGE

    amounts = [float(m.group(1)) for m in PRICE_NUMBER_RE.finditer(text)]
    if amounts:
        avg = sum(amounts) / len(amounts)
        if avg <= 15:
            return PRICE_TAG_BUDGET
        if avg <= 35:
            return PRICE_TAG_MID_RANGE
        return PRICE_TAG_EXPENSIVE

    return PRICE_TAG_MID_RANGE


def infer_tag_category(tag_name: str, area_tag: Optional[str]) -> Optional[str]:
    if not tag_name:
        return None
    if tag_name in (PRICE_TAG_BUDGET, PRICE_TAG_MID_RANGE, PRICE_TAG_EXPENSIVE):
        return TAG_CATEGORY_BUDGET
    if tag_name in CUISINE_KEYWORDS or tag_name == DEFAULT_CUISINE_TAG:
        return TAG_CATEGORY_CUISINE
    if tag_name in ALLERGY_KEYWORDS:
        return TAG_CATEGORY_ALLERGY
    if area_tag and tag_name == area_tag:
        return TAG_CATEGORY_AREA
    return None


def chunked(values: Sequence[Dict[str, Any]], size: int) -> Iterable[Sequence[Dict[str, Any]]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def fetch_all_table_rows(supabase: Client, table: str, select_expr: str, page_size: int = 1000) -> List[Dict[str, Any]]:
    all_rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        response = (
            supabase.table(table)
            .select(select_expr)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = response.data or []
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return all_rows


def pick_tags_query(supabase: Client) -> Tuple[str, List[Dict[str, Any]]]:
    candidates = [
        "id, name, category",
        "id, name",
    ]
    last_error: Optional[Exception] = None
    for select_expr in candidates:
        try:
            rows = fetch_all_table_rows(supabase, "tags", select_expr)
            return select_expr, rows
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(f"Unable to query tags table with supported columns. Last error: {last_error}")


def find_tag_by_name(supabase: Client, tag_name: str) -> Optional[Dict[str, Any]]:
    lookups = [
        supabase.table("tags").select("id, name").eq("name", tag_name).limit(1),
        supabase.table("tags").select("id, name").ilike("name", tag_name).limit(1),
    ]
    for query in lookups:
        try:
            response = query.execute()
            row = (response.data or [None])[0]
            if row and row.get("id") is not None:
                return row
        except Exception:
            continue
    return None


def extract_place_id_from_uri(uri: str) -> Optional[str]:
    if not uri:
        return None
    try:
        parsed = urllib.parse.urlparse(uri)
    except Exception:
        return None
    params = urllib.parse.parse_qs(parsed.query or "")
    for key in ("place_id", "placeid"):
        if key in params and params[key]:
            return params[key][0]
    if "q" in params and params["q"]:
        q = params["q"][0]
        if q.startswith("place_id:"):
            return q.split("place_id:", 1)[1]
    return None


class GooglePlacesClient:
    def __init__(self, api_key: str, delay_sec: float = 0.05) -> None:
        self.api_key = api_key
        self.delay_sec = delay_sec

    def place_details(self, place_id: str, fields: str = "") -> Optional[Dict[str, Any]]:
        payload, status = self._fetch_details(place_id, fields=fields)
        if status == "OK":
            return payload.get("result") or {}
        if fields:
            payload, status = self._fetch_details(place_id, fields="")
            if status == "OK":
                return payload.get("result") or {}
        return None

    def _fetch_details(self, place_id: str, fields: str = "") -> Tuple[Dict[str, Any], str]:
        params = {"place_id": place_id, "key": self.api_key}
        if fields:
            params["fields"] = fields
        url = "https://maps.googleapis.com/maps/api/place/details/json?" + urllib.parse.urlencode(params)
        payload: Dict[str, Any] = {}
        status = "ERROR"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                status = str(payload.get("status") or "ERROR")
        except Exception:
            status = "ERROR"
        finally:
            if self.delay_sec > 0:
                time.sleep(self.delay_sec)
        return payload, status

    def place_details_new(self, place_id: str, field_mask: str = "") -> Optional[Dict[str, Any]]:
        payload, ok = self._fetch_details_new(place_id, field_mask=field_mask)
        if ok:
            return payload
        return None

    def _fetch_details_new(self, place_id: str, field_mask: str = "") -> Tuple[Dict[str, Any], bool]:
        url = f"https://places.googleapis.com/v1/places/{urllib.parse.quote(place_id)}"
        headers = {"X-Goog-Api-Key": self.api_key}
        if field_mask:
            headers["X-Goog-FieldMask"] = field_mask
        payload: Dict[str, Any] = {}
        ok = False
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                ok = "error" not in payload
        except Exception:
            ok = False
        finally:
            if self.delay_sec > 0:
                time.sleep(self.delay_sec)
        return payload, ok

    def find_place_id(self, query: str, lat: Optional[float] = None, lng: Optional[float] = None, radius_m: int = 1000) -> Optional[str]:
        params = {
            "input": query,
            "inputtype": "textquery",
            "fields": "place_id",
            "key": self.api_key,
        }
        if lat is not None and lng is not None:
            params["locationbias"] = f"circle:{int(radius_m)}@{lat},{lng}"
        url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json?" + urllib.parse.urlencode(params)
        payload: Dict[str, Any] = {}
        status = "ERROR"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                status = str(payload.get("status") or "ERROR")
        except Exception:
            status = "ERROR"
        finally:
            if self.delay_sec > 0:
                time.sleep(self.delay_sec)
        if status != "OK":
            return None
        candidates = payload.get("candidates") or []
        if not candidates:
            return None
        return candidates[0].get("place_id")


def pick_place_query(supabase: Client) -> Tuple[str, List[Dict[str, Any]]]:
    candidates = [
        "id, gmaps_place_id, gmaps_uri, name, address",
        "id, gmaps_uri, name, address",
        "id, name, address",
    ]
    last_error: Optional[Exception] = None
    for select_expr in candidates:
        try:
            rows = fetch_all_table_rows(supabase, "places", select_expr)
            return select_expr, rows
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(f"Unable to query places table with supported columns. Last error: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich places with Google Places tags")
    parser.add_argument("--apply", action="store_true", help="Actually write changes to Supabase (default is dry-run)")
    parser.add_argument("--limit", type=int, default=0, help="Optional row limit for testing (0 means all rows)")
    parser.add_argument("--sleep", type=float, default=0.05, help="Sleep seconds between API calls")
    parser.add_argument("--use-find", action="store_true", help="Use Find Place when gmaps_place_id is missing")
    parser.add_argument(
        "--find-radius",
        type=int,
        default=1000,
        help="Radius in meters for Find Place location bias (used with --use-find)",
    )
    parser.add_argument(
        "--include-allergies",
        action="store_true",
        help="Include allergy tags from Places details text",
    )
    parser.add_argument("--skip-if-tagged", action="store_true", help="Skip places that already have at least one tag")
    parser.add_argument(
        "--places-new",
        action="store_true",
        help="Use Places API (New) endpoint with FieldMask instead of legacy Place Details",
    )
    parser.add_argument(
        "--fields",
        default="",
        help="Legacy fields (comma-separated) or Places API (New) FieldMask",
    )
    parser.add_argument("--report", default="", help="Optional report output path (.json, .csv, or .xlsx)")
    parser.add_argument(
        "--report-details",
        action="store_true",
        help="Include raw details JSON in report output",
    )
    args = parser.parse_args()

    load_dotenv()

    if create_client is None:
        raise RuntimeError("Supabase SDK is not installed. Install requirements first.")

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("Missing SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY/SUPABASE_ANON_KEY")

    maps_key = os.environ.get("GOOGLE_PLACES_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not maps_key:
        raise RuntimeError("Missing GOOGLE_PLACES_API_KEY (or GOOGLE_MAPS_API_KEY) for Places API")

    supabase = create_client(supabase_url, supabase_key)
    places_client = GooglePlacesClient(api_key=maps_key, delay_sec=args.sleep)

    fields = (args.fields or "").strip()
    if not fields:
        if args.places_new:
            fields = ",".join(PLACES_NEW_ESSENTIAL_FIELDS)
        else:
            fields = LEGACY_DEFAULT_FIELDS

    place_select, place_rows = pick_place_query(supabase)
    tags_select, tags_rows = pick_tags_query(supabase)
    place_tag_rows = fetch_all_table_rows(supabase, "place_tags", "place_id, tag_id")

    if args.limit > 0:
        place_rows = place_rows[: args.limit]

    print(f"Places query used: {place_select}")
    print(f"Places loaded: {len(place_rows)}")
    print(f"Tags query used: {tags_select}")
    print(f"Places API: {'NEW' if args.places_new else 'LEGACY'}")
    print(f"Fields: {fields}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")

    tags_by_norm: Dict[str, Dict[str, Any]] = {
        normalize_text(t.get("name")): t for t in tags_rows if t.get("name") is not None
    }
    tags_has_category = "category" in tags_select
    existing_pairs: Set[Tuple[str, str]] = {
        (str(r.get("place_id")), str(r.get("tag_id")))
        for r in place_tag_rows
        if r.get("place_id") is not None and r.get("tag_id") is not None
    }
    place_ids_with_tags: Set[str] = {str(r.get("place_id")) for r in place_tag_rows if r.get("place_id") is not None}

    created_tags = 0
    planned_links = 0
    failed_link_inserts = 0
    failed_tag_ensures = 0
    skipped_missing_place_id = 0
    skipped_tagged = 0
    report_rows: List[Dict[str, Any]] = []

    synthetic_tag_id = -1
    max_tag_id = 0
    for row in tags_rows:
        try:
            max_tag_id = max(max_tag_id, int(str(row.get("id"))))
        except Exception:
            continue

    def ensure_tag_id(tag_name: str, category: Optional[str]) -> Optional[str]:
        nonlocal created_tags, synthetic_tag_id, failed_tag_ensures, max_tag_id
        clean_name = normalize_tag_name(tag_name)
        if not clean_name:
            return None

        key = normalize_text(clean_name)
        existing = tags_by_norm.get(key)
        if existing:
            return str(existing["id"])

        if not args.apply:
            synthetic = {"id": str(synthetic_tag_id), "name": clean_name}
            if tags_has_category and category:
                synthetic["category"] = category
            synthetic_tag_id -= 1
            tags_by_norm[key] = synthetic
            created_tags += 1
            return str(synthetic["id"])

        insert_payload = {"name": clean_name}
        if tags_has_category and category:
            insert_payload["category"] = category
        insert_error: Optional[Exception] = None
        try:
            response = supabase.table("tags").insert(insert_payload).execute()
            inserted = (response.data or [None])[0]
            if inserted and inserted.get("id") is not None:
                tags_by_norm[key] = inserted
                created_tags += 1
                return str(inserted["id"])
        except Exception as exc:
            insert_error = exc

        inserted = find_tag_by_name(supabase, clean_name)
        if not inserted or inserted.get("id") is None:
            failed_tag_ensures += 1
            if insert_error is not None:
                print(f"[warn] Could not ensure tag '{clean_name}' ({insert_error})")
            else:
                print(f"[warn] Could not ensure tag '{clean_name}'")
            return None

        tags_by_norm[key] = inserted
        return str(inserted["id"])

    place_tags_to_insert: List[Dict[str, Any]] = []

    for place in place_rows:
        place_id = str(place.get("id"))
        if args.skip_if_tagged and place_id in place_ids_with_tags:
            skipped_tagged += 1
            continue

        gmaps_place_id = place.get("gmaps_place_id")
        if not gmaps_place_id:
            gmaps_place_id = extract_place_id_from_uri(str(place.get("gmaps_uri") or ""))

        if not gmaps_place_id and args.use_find:
            name = str(place.get("name") or "").strip()
            address = str(place.get("address") or "").strip()
            query = " ".join(part for part in [name, address] if part)
            if query:
                lat = to_float(place.get("latitude"))
                lng = to_float(place.get("longitude"))
                gmaps_place_id = places_client.find_place_id(
                    query,
                    lat=lat,
                    lng=lng,
                    radius_m=args.find_radius,
                )

        if not gmaps_place_id:
            skipped_missing_place_id += 1
            report_rows.append(
                {
                    "place_id": place_id,
                    "place_name": place.get("name") or "",
                    "status": "missing_place_id",
                    "tags": "",
                }
            )
            continue

        if args.places_new:
            details = places_client.place_details_new(str(gmaps_place_id), field_mask=fields)
        else:
            details = places_client.place_details(str(gmaps_place_id), fields=fields)
        if not details:
            report_rows.append(
                {
                    "place_id": place_id,
                    "place_name": place.get("name") or "",
                    "status": "details_not_found",
                    "tags": "",
                    "details_json": "" if args.report_details else None,
                }
            )
            continue

        reviews_text = extract_reviews_text(details.get("reviews"))
        editorial_summary = ""
        if isinstance(details.get("editorial_summary"), dict):
            editorial_summary = str(details["editorial_summary"].get("overview") or "").strip()
        elif isinstance(details.get("editorialSummary"), dict):
            editorial_summary = str(details["editorialSummary"].get("overview") or "").strip()
        elif details.get("editorial_summary"):
            editorial_summary = str(details.get("editorial_summary") or "").strip()
        elif details.get("editorialSummary"):
            editorial_summary = str(details.get("editorialSummary") or "").strip()

        types_text = " ".join([str(t) for t in (details.get("types") or []) if t])
        details_name = str(details.get("name") or "")
        if args.places_new:
            display = details.get("displayName")
            if isinstance(display, dict):
                details_name = str(display.get("text") or "") or details_name
            details_name = details_name or str(place.get("name") or "")
        combined_text = " ".join(
            part
            for part in [
                details_name,
                types_text,
                editorial_summary,
                reviews_text,
            ]
            if part
        )

        type_cuisine_tags = infer_cuisine_tags_from_types(extract_place_types(details))
        text_cuisine_tags = infer_cuisine_tags(combined_text)
        cuisine_tags = type_cuisine_tags + [t for t in text_cuisine_tags if t not in type_cuisine_tags]
        if not cuisine_tags:
            cuisine_tags = [DEFAULT_CUISINE_TAG]
        allergy_tags = infer_allergy_tags(combined_text) if args.include_allergies else []
        price_level = details.get("price_level")
        if price_level is None:
            price_level = details.get("priceLevel")
        price_tag = infer_price_range_tag(price_level, combined_text)
        area_tag = infer_area_tag(details)

        proposed = [t for t in [area_tag, *cuisine_tags, price_tag, *allergy_tags] if t]
        seen_norm: Set[str] = set()
        deduped: List[str] = []
        for tag in proposed:
            if not tag or not is_english_tag(tag):
                continue
            key = normalize_text(tag)
            if key in seen_norm:
                continue
            seen_norm.add(key)
            deduped.append(tag)

        row_links = 0
        for tag_name in deduped:
            category = infer_tag_category(tag_name, area_tag)
            tag_id = ensure_tag_id(tag_name, category)
            if not tag_id:
                continue
            pair = (place_id, tag_id)
            if pair in existing_pairs:
                continue
            existing_pairs.add(pair)
            planned_links += 1
            row_links += 1
            place_tags_to_insert.append({"place_id": place_id, "tag_id": tag_id})

        report_row: Dict[str, Any] = {
            "place_id": place_id,
            "place_name": place.get("name") or "",
            "status": "ok",
            "tags": "|".join(deduped),
            "area_tag": area_tag or "",
            "allergy_tags": "|".join(allergy_tags),
            "new_links_planned_or_inserted": row_links,
        }
        if args.report_details:
            report_row["details_json"] = json.dumps(details, ensure_ascii=False)
        report_rows.append(report_row)

    if args.apply and place_tags_to_insert:
        for batch in chunked(place_tags_to_insert, 250):
            try:
                supabase.table("place_tags").insert(list(batch)).execute()
            except Exception:
                for row in batch:
                    try:
                        supabase.table("place_tags").insert(row).execute()
                    except Exception:
                        failed_link_inserts += 1

    print("\nSummary")
    print(f"- Existing tags loaded: {len(tags_rows)}")
    print(f"- Existing place_tags loaded: {len(place_tag_rows)}")
    print(f"- New tags {'created' if args.apply else 'planned'}: {created_tags}")
    if args.apply:
        print(f"- Failed tag ensure operations: {failed_tag_ensures}")
    print(f"- New place_tags {'inserted' if args.apply else 'planned'}: {planned_links}")
    if args.apply:
        print(f"- Failed place_tags inserts: {failed_link_inserts}")
    print(f"- Skipped (missing place_id): {skipped_missing_place_id}")
    if args.skip_if_tagged:
        print(f"- Skipped (already tagged): {skipped_tagged}")

    if args.report:
        report_path = args.report
        fieldnames = ["place_id", "place_name", "status", "tags", "area_tag", "allergy_tags", "new_links_planned_or_inserted"]
        if args.report_details:
            fieldnames.append("details_json")
        if report_path.endswith(".csv"):
            import csv

            with open(report_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(report_rows)
        elif report_path.endswith(".xlsx"):
            try:
                from openpyxl import Workbook
            except Exception as exc:
                raise RuntimeError("openpyxl is required to write .xlsx reports") from exc

            wb = Workbook()
            ws = wb.active
            ws.title = "google_places_enrich"
            ws.append(fieldnames)
            for row in report_rows:
                ws.append([row.get(name, "") for name in fieldnames])
            wb.save(report_path)
        else:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report_rows, f, ensure_ascii=False, indent=2)
        print(f"- Report written: {report_path}")


if __name__ == "__main__":
    main()
