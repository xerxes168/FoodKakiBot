"""
ranking.py
----------
Post-retrieval ranking for restaurant/place candidates.

Pipeline:
  1. Filter out closed places (unless user explicitly wants closed / future visit)
  2. Score each candidate on four dimensions:
       - Preference match  (highest weight) — tag overlap with resolved query
       - Distance          (second)         — haversine km from user location
       - Popularity        (third)          — rating × log(review_count)
  3. Return candidates sorted descending by composite score.

Usage in app.py:
    from ranking import rank_candidates, is_open_now

    candidates = rank_candidates(
        candidates,
        resolved_tags=resolved,          # dict from session tag resolution
        user_lat=gps_lat,
        user_lng=gps_lng,
        allow_closed=False,              # set True if user says "opening soon" etc.
    )
"""

from __future__ import annotations

import math
from datetime import datetime, time
from typing import Any

# ── Scoring weights (must sum to 1.0) ─────────────────────────────────────────
W_PREFERENCE = 0.50
W_DISTANCE   = 0.30
W_POPULARITY = 0.20

# ── Distance scoring parameters ───────────────────────────────────────────────
# Score = 1 / (1 + distance_km / DIST_HALF_SCORE_KM)
# At DIST_HALF_SCORE_KM the distance component equals 0.5
# Tune this to your expected search radius.
DIST_HALF_SCORE_KM = 1.5   # 1.5 km → score 0.5; 0 km → score 1.0

# ── Popularity scoring parameters ─────────────────────────────────────────────
MAX_RATING      = 5.0
# Clamp review count at this value before log-normalising (avoids outlier bias)
MAX_REVIEW_CAP  = 2000
# Weight between rating and review_count within the popularity component
RATING_WEIGHT   = 0.65
REVIEW_WEIGHT   = 0.35

# ── Day-of-week mapping ───────────────────────────────────────────────────────
# Python weekday(): 0=Monday … 6=Sunday
# OpenStreetMap / Google-style day abbreviations used in opening_hours JSON
_DOW_KEYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ══════════════════════════════════════════════════════════════════════════════
# Opening hours helpers
# ══════════════════════════════════════════════════════════════════════════════

def _parse_time(t: str) -> time | None:
    """Parse 'HH:MM' or 'H:MM' into a datetime.time. Returns None on failure."""
    try:
        h, m = t.strip().split(":")
        return time(int(h), int(m))
    except Exception:
        return None


def _is_open_in_period(now_time: time, open_str: str, close_str: str) -> bool:
    """
    Check whether now_time falls within [open_str, close_str].
    Handles overnight spans (e.g. 22:00 – 02:00).
    """
    opens  = _parse_time(open_str)
    closes = _parse_time(close_str)
    if opens is None or closes is None:
        return True   # unparseable → assume open (fail-open)

    if closes <= opens:
        # Overnight: open if now >= opens OR now < closes
        return now_time >= opens or now_time < closes
    else:
        return opens <= now_time < closes


def is_open_now(place: dict[str, Any], now: datetime | None = None) -> bool:  # noqa: C901
    try:
        return _is_open_now_inner(place, now)
    except Exception:
        return True   # always fail-open on any unexpected error


def _is_open_now_inner(place: dict[str, Any], now: datetime | None = None) -> bool:
    """
    Return True if the place is currently open.

    Expects place["opening_hours"] to be one of:
      A) A dict keyed by lowercase day name:
            {
              "monday":    [{"open": "09:00", "close": "22:00"}],
              "tuesday":   [{"open": "09:00", "close": "22:00"}],
              ...
            }
      B) A dict with "periods" list (Google Places format):
            {
              "periods": [
                {"open": {"day": 1, "time": "0900"}, "close": {"day": 1, "time": "2200"}},
                ...
              ]
            }
      C) A string "24/7"  → always open
      D) None / missing   → assume open (fail-open)

    Returns True if no opening hours data is available (fail-open).
    """
    if now is None:
        now = datetime.now()

    hours = place.get("opening_hours")
    if not hours:
        return True   # no data → don't filter out

    # Supabase sometimes stores null as the string "null" or "none"
    if isinstance(hours, str) and hours.lower() in ("null", "none", ""):
        return True

    # If stored as a JSON string, parse it first
    if isinstance(hours, str):
        if hours.strip().lower() in ("24/7", "always", "open 24 hours"):
            return True
        try:
            import json as _json
            hours = _json.loads(hours)
        except Exception:
            # Unparseable string format → fail-open
            return True

    if not isinstance(hours, (dict, list)):
        return True

    now_time = now.time()
    dow_idx  = now.weekday()   # 0=Mon, 6=Sun
    day_key  = _DOW_KEYS[dow_idx]

    # ── Format D: list of strings ["Monday: 9:00 AM – 10:00 PM", ...] ─────────
    if isinstance(hours, list):
        import re as _re
        day_name = day_key.capitalize()
        for entry in hours:
            if not isinstance(entry, str) or not entry.startswith(day_name + ":"):
                continue
            text = entry[len(day_name) + 1:].strip()
            if "open 24 hours" in text.lower():
                return True
            if "closed" in text.lower():
                return False
            m = _re.search(
                r"(\d{1,2}:\d{2}\s*(?:AM|PM))\s*[–\-]\s*(\d{1,2}:\d{2}\s*(?:AM|PM))",
                text, _re.IGNORECASE,
            )
            if m:
                try:
                    from datetime import datetime as _dt
                    def _ampm(s: str) -> time:
                        for fmt in ("%I:%M %p", "%I:%M%p"):
                            try:
                                return _dt.strptime(s.strip().upper(), fmt).time()
                            except ValueError:
                                continue
                        raise ValueError(s)
                    open_t  = _ampm(m.group(1))
                    close_t = _ampm(m.group(2))
                    return _is_open_in_period(
                        now_time,
                        f"{open_t.hour}:{open_t.minute:02d}",
                        f"{close_t.hour}:{close_t.minute:02d}",
                    )
                except Exception:
                    pass
            return True   # found today but couldn't parse → fail-open
        return True   # no entry for today → fail-open

    # ── Format E: {"weekday_text": [...]} wrapper ──────────────────────────────
    if isinstance(hours, dict) and "weekday_text" in hours and "periods" not in hours:
        wt = hours.get("weekday_text")
        if isinstance(wt, list):
            return _is_open_now_inner({"opening_hours": wt}, now)

    # ── Format B: Google Periods ───────────────────────────────────────────────
    if "periods" in hours:
        periods = hours["periods"]
        for period in periods:
            try:
                open_day = period["open"]["day"]   # Google: 0=Sun, 1=Mon … 6=Sat
                # Convert to Python weekday: 0=Mon … 6=Sun
                google_dow = (open_day - 1) % 7
                if google_dow != dow_idx:
                    continue

                # Support both {"hour": 11, "minute": 0} and {"time": "1100"}
                o = period["open"]
                c = period["close"]
                if "hour" in o:
                    open_t  = time(o["hour"], o.get("minute", 0))
                    close_t = time(c["hour"], c.get("minute", 0))
                else:
                    ts = o["time"]   # "1100"
                    open_t  = time(int(ts[:2]), int(ts[2:]))
                    ts = c["time"]
                    close_t = time(int(ts[:2]), int(ts[2:]))

                if _is_open_in_period(now_time,
                                      f"{open_t.hour}:{open_t.minute:02d}",
                                      f"{close_t.hour}:{close_t.minute:02d}"):
                    return True
            except Exception:
                continue
        return False

    # ── Format A: Day-keyed dict ───────────────────────────────────────────────
    day_periods = hours.get(day_key) or hours.get(day_key.capitalize())
    if not day_periods:
        # Try abbreviated key (mon, tue …)
        day_periods = hours.get(day_key[:3])
    if not day_periods:
        return True   # no entry for today → fail-open

    if isinstance(day_periods, str):
        # e.g. "09:00-22:00"
        if "-" in day_periods:
            parts = day_periods.split("-", 1)
            return _is_open_in_period(now_time, parts[0].strip(), parts[1].strip())
        return True

    if isinstance(day_periods, list):
        for period in day_periods:
            if isinstance(period, dict):
                o = period.get("open")  or period.get("opens")
                c = period.get("close") or period.get("closes")
                if o and c and _is_open_in_period(now_time, o, c):
                    return True
        return False

    return True   # unrecognised format → fail-open


# ══════════════════════════════════════════════════════════════════════════════
# Individual score components
# ══════════════════════════════════════════════════════════════════════════════

def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometres."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlam  = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _preference_score(place: dict[str, Any], resolved_tags: dict[str, Any]) -> float:
    """
    Score [0, 1] based on how many of the user's resolved query tags the place has.

    resolved_tags expected keys (all optional):
        cuisine   : str   e.g. "Japanese"
        budget    : str   e.g. "Budget"
        location  : str   e.g. "Katong"
        ambience  : str | list
        dietary   : str | list

    place["tags"] expected: list of tag name strings.
    """
    place_tags_lower = {t.lower() for t in (place.get("tags") or [])}
    if not place_tags_lower:
        return 0.0

    desired: list[str] = []
    for key in ("cuisine", "budget", "ambience", "dietary"):
        val = resolved_tags.get(key)
        if not val:
            continue
        if isinstance(val, list):
            desired.extend([v.lower() for v in val if v])
        elif isinstance(val, str):
            desired.append(val.lower())

    if not desired:
        return 0.5   # no preference signals → neutral score

    matched = sum(1 for d in desired if d in place_tags_lower)
    return matched / len(desired)


def _distance_score(place: dict[str, Any], user_lat: float | None, user_lng: float | None) -> float:
    """
    Score [0, 1]. 1.0 = same location, decays smoothly with distance.
    Returns 0.5 if no GPS available (neutral — don't penalise or reward).
    """
    if user_lat is None or user_lng is None:
        return 0.5

    lat = place.get("lat") or place.get("latitude")
    lng = place.get("lng") or place.get("longitude")
    if lat is None or lng is None:
        return 0.5

    try:
        km = _haversine_km(float(user_lat), float(user_lng), float(lat), float(lng))
    except (TypeError, ValueError):
        return 0.5

    # Smooth decay: 1/(1 + d/half_score)
    return 1.0 / (1.0 + km / DIST_HALF_SCORE_KM)


def _popularity_score(place: dict[str, Any]) -> float:
    """
    Score [0, 1] combining rating and review count.

    Reads: place["rating"] (float, 0–5) and place["review_count"] / place["reviews"] (int).
    """
    raw_rating = place.get("rating") or place.get("average_rating") or 0.0
    raw_reviews = (
        place.get("review_count")
        or place.get("reviews_count")
        or place.get("reviews")
        or 0
    )

    try:
        rating  = float(raw_rating)
        reviews = int(raw_reviews)
    except (TypeError, ValueError):
        return 0.0

    rating_score = min(rating, MAX_RATING) / MAX_RATING

    # Log-normalise review count: log(1 + n) / log(1 + cap)
    review_score = (
        math.log1p(min(reviews, MAX_REVIEW_CAP))
        / math.log1p(MAX_REVIEW_CAP)
    )

    return RATING_WEIGHT * rating_score + REVIEW_WEIGHT * review_score


# ══════════════════════════════════════════════════════════════════════════════
# Main ranking entry point
# ══════════════════════════════════════════════════════════════════════════════

def rank_candidates(
    candidates: list[dict[str, Any]],
    *,
    resolved_tags: dict[str, Any] | None = None,
    user_lat: float | None = None,
    user_lng: float | None = None,
    allow_closed: bool = False,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Filter and rank place candidates.

    Args:
        candidates:    Raw list of place dicts from retrieve_hybrid / retrieve_nearby.
        resolved_tags: Session tag dict (cuisine, budget, ambience, dietary …).
        user_lat:      User GPS latitude  (None → distance component is neutral).
        user_lng:      User GPS longitude (None → distance component is neutral).
        allow_closed:  If True, skip the opening-hours filter (user asked about
                       future visit, or explicitly wants closed results).
        now:           Override current datetime (useful for testing).

    Returns:
        List of candidates sorted best-first, each with an injected "_score" dict.
    """
    if resolved_tags is None:
        resolved_tags = {}
    if now is None:
        now = datetime.now()

    results: list[dict[str, Any]] = []

    for place in candidates:
        # ── Score components ───────────────────────────────────────────────────
        pref_s = _preference_score(place, resolved_tags)
        dist_s = _distance_score(place, user_lat, user_lng)
        pop_s  = _popularity_score(place)

        composite = (
            W_PREFERENCE * pref_s
            + W_DISTANCE  * dist_s
            + W_POPULARITY * pop_s
        )

        place = {
            **place,
            "_score": {
                "composite":  round(composite, 4),
                "preference": round(pref_s, 4),
                "distance":   round(dist_s, 4),
                "popularity": round(pop_s, 4),
                "is_open":    is_open_now(place, now),   # informational only
            },
        }
        results.append((composite, place))

    # ── Sort descending by composite score ────────────────────────────────────
    results.sort(key=lambda x: x[0], reverse=True)
    return [place for _, place in results]


def detect_allow_closed(user_message: str) -> bool:
    """
    Heuristic: return True if the user's message suggests they don't mind
    (or want) results that may be closed right now.

    Examples: "opening hours", "what time does X open", "open tomorrow",
              "closes at", "late night", "is X open"
    """
    msg = user_message.lower()
    signals = [
        "opening hours", "open tomorrow", "open on", "closes at", "open until",
        "what time", "when does", "when do", "open late", "late night",
        "midnight", "24 hour", "24-hour", "supper", "after midnight",
        "early morning", "breakfast time", "open for lunch", "open for dinner",
        "future visit", "planning to go", "going next",
    ]
    return any(s in msg for s in signals)