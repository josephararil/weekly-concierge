"""Booking.com (apidojo) hotel grounding provider for the Stage-3 seam.

Plain functions + dicts only. No classes, no ABC, no factory.
On any failure the public entry point (ground_api) falls back to _ground_llm.
"""

import re
from datetime import date, timedelta
from urllib.parse import urlencode

import requests

import config as C


# ── Internal HTTP ─────────────────────────────────────────────────────────────

def _get(path, params):
    """GET BOOKING_BASE_URL+path; return parsed JSON or raise."""
    if not C.RAPIDAPI_KEY:
        raise ValueError("RAPIDAPI_KEY not set")
    headers = {
        "X-RapidAPI-Key":  C.RAPIDAPI_KEY,
        "X-RapidAPI-Host": C.BOOKING_RAPIDAPI_HOST,
    }
    resp = requests.get(
        C.BOOKING_BASE_URL + path,
        params=params,
        headers=headers,
        timeout=C.HOTEL_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ── Fuzzy matching helpers ────────────────────────────────────────────────────

_STRIP_WORDS = re.compile(r'\b(hotel|the|resort|spa|&|and)\b', re.IGNORECASE)
_PUNCT = re.compile(r'[^\w\s]')


def _normalize(s):
    """Lowercase, strip noise words and punctuation, return token set."""
    s = _STRIP_WORDS.sub(' ', s.lower())
    s = _PUNCT.sub(' ', s)
    return set(s.split())


def _match_score(hotel_name, card_name):
    """Token-overlap ratio: |target ∩ card| / |target|. Returns 0.0 if target is empty."""
    target = _normalize(hotel_name)
    card = _normalize(card_name)
    if not target:
        return 0.0
    return len(target & card) / len(target)


def _longest_token(hotel_name):
    """Return the longest token from the normalized hotel name (the brand token)."""
    tokens = _normalize(hotel_name)
    return max(tokens, key=len) if tokens else ""


# ── Hotel resolution ──────────────────────────────────────────────────────────

def resolve_hotel(diamond):
    """Return {dest_id, search_type, name, match_name, country, kind, raw} or None.

    kind="hotel": exact hotel-type dest (APIDojo search_type=hotel) → verified path.
    kind="city":  city-type dest → alternatives path (best-value city hotels).
    None: nothing useful found → caller falls back to LLM.

    Priority: hotel-type entry overlapping hotel_name > city-type entry.
    Landmark entries are not used.
    """
    hotel_name = (diamond.get("hotel_name") or "").strip()
    city       = (diamond.get("city")       or "").strip()
    country    = (diamond.get("country")    or "").strip()

    # HOTEL_MAPPING override first (case-insensitive substring match on hotel_name + city)
    if hotel_name:
        lookup = f"{hotel_name} {city}".lower().strip()
        for alias, ref_data in C.HOTEL_MAPPING.items():
            if alias.lower() in lookup or lookup in alias.lower():
                return {
                    "dest_id":     ref_data["dest_id"],
                    "search_type": ref_data["search_type"],
                    "name":        ref_data.get("name", alias),
                    "match_name":  hotel_name,
                    "country":     ref_data.get("country", ""),
                    "kind":        ref_data.get("kind", "hotel"),
                    "raw":         ref_data,
                }

    # Query text: hotel + city when hotel_name is set; city only otherwise
    text = f"{hotel_name} {city}".strip() if hotel_name else city
    if not text:
        return None

    data = _get("/locations/auto-complete", {"languagecode": "en-us", "text": text})
    if not isinstance(data, list) or not data:
        return None

    hotel_tokens = _normalize(hotel_name) if hotel_name else set()
    hotel_entry = None
    city_entry  = None

    for item in data:
        dtype        = (item.get("dest_type") or "").lower()
        name         = item.get("name") or item.get("label") or ""
        item_country = item.get("country") or ""

        # Country validation: skip entries whose country doesn't match
        if country and country.lower() not in item_country.lower():
            continue

        if dtype == "hotel" and hotel_entry is None and hotel_tokens:
            if hotel_tokens & _normalize(name):
                hotel_entry = item
        elif dtype == "city" and city_entry is None:
            city_entry = item

    # Prefer exact hotel-type dest; fall back to city for the alternatives path
    chosen = hotel_entry or city_entry
    if chosen is None:
        return None

    dtype = (chosen.get("dest_type") or "").lower()
    kind  = "hotel" if dtype == "hotel" else "city"
    return {
        "dest_id":     chosen["dest_id"],
        "search_type": "hotel" if kind == "hotel" else "city",
        "name":        chosen.get("name") or chosen.get("label") or (hotel_name or city),
        "match_name":  hotel_name,
        "country":     chosen.get("country") or "",
        "kind":        kind,
        "raw":         chosen,
    }


# ── Property listing ──────────────────────────────────────────────────────────

def list_properties(ref, chk_in, chk_out):
    """Return list of property_card dicts from /properties/v2/list."""
    params = {
        "offset":                    0,
        "arrival_date":              chk_in,
        "departure_date":            chk_out,
        "dest_ids":                  ref["dest_id"],
        "search_type":               ref["search_type"],
        "room_qty":                  C.HOTEL_ROOMS,
        "guest_qty":                 C.HOTEL_ADULTS,
        "children_qty":              len(C.HOTEL_CHILDREN_AGES),
        "children_age":              ",".join(map(str, C.HOTEL_CHILDREN_AGES)),
        "price_filter_currencycode": "EUR",
        "order_by":                  "price" if ref["kind"] == "city" else "distance",
        "languagecode":              "en-us",
        "units":                     "metric",
    }
    data = _get("/properties/v2/list", params)
    result = data.get("result") or []
    return [r for r in result if r.get("type") == "property_card"]


# ── Rate fetching ─────────────────────────────────────────────────────────────

def price(ref, chk_in, chk_out):
    """Return HotelRate dict or None.

    Fuzzy-matches ref["name"] against listing cards; returns None if no match
    (do NOT substitute a different hotel). The caller falls back to LLM.

    HotelRate = {name, checkin, checkout, nights, price_per_night_eur,
                 total_eur, booking_url, source, currency, review_score, stars}
    """
    cards = list_properties(ref, chk_in, chk_out)
    if not cards:
        return None

    match_name = ref.get("match_name") or ref.get("name", "")
    brand_token = _longest_token(match_name)

    if ref.get("search_type") == "hotel":
        # Single-property listing — take the first card; sanity-check the brand token.
        card = cards[0]
        if brand_token and brand_token not in _normalize(card.get("hotel_name", "")):
            return None
        matched = card
    else:
        # Landmark/city listing — pick the best-scoring card by token overlap.
        best_score = 0.0
        matched = None
        for card in cards:
            score = _match_score(match_name, card.get("hotel_name", ""))
            if score > best_score:
                best_score = score
                matched = card

        if matched is None or best_score < 0.6:
            return None
        if brand_token and brand_token not in _normalize(matched.get("hotel_name", "")):
            return None

    breakdown = matched.get("composite_price_breakdown") or {}
    ppn_block = breakdown.get("gross_amount_per_night") or {}
    ppn = ppn_block.get("value")
    if ppn is None:
        return None

    total_raw = matched.get("min_total_price")
    if total_raw is None:
        gross_block = breakdown.get("gross_amount") or {}
        total_raw = gross_block.get("value")
    if total_raw is None:
        return None

    chk_in_d  = date.fromisoformat(chk_in)
    chk_out_d = date.fromisoformat(chk_out)
    nights = (chk_out_d - chk_in_d).days
    if nights <= 0:
        return None

    name = matched.get("hotel_name") or ref["name"]
    booking_url = "https://www.booking.com/searchresults.html?" + urlencode({
        "ss":             name,
        "checkin":        chk_in,
        "checkout":       chk_out,
        "group_adults":   C.HOTEL_ADULTS,
        "group_children": len(C.HOTEL_CHILDREN_AGES),
        "age":            ",".join(map(str, C.HOTEL_CHILDREN_AGES)),
    })

    return {
        "name":                name,
        "checkin":             chk_in,
        "checkout":            chk_out,
        "nights":              nights,
        "price_per_night_eur": round(float(ppn), 2),
        "total_eur":           round(float(total_raw), 2),
        "booking_url":         booking_url,
        "source":              "Booking.com (apidojo)",
        "currency":            "EUR",
        "review_score":        matched.get("review_score"),
        "stars":               matched.get("class"),
    }


# ── Window parsing ────────────────────────────────────────────────────────────

# Matches "Sep 10-14, 2026" or "10-14 Sep 2026" styles (one month token, a day range)
_EXPLICIT_RE = re.compile(
    r"(?:(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]+)[\s,]+(\d{4}))"
    r"|(?:([A-Za-z]+)\s+(\d{1,2})\s*[-–]\s*(\d{1,2})[\s,]+(\d{4}))"
)
# Full date on BOTH sides, month spelled out each time: "17 July 2026 - 20 July 2026", or
# with the year only on the right: "17 July - 20 July 2026". FIND emits this shape, and it
# MUST be tried before _EXPLICIT_RE — whose leading \d{1,2} otherwise latches onto the last
# two digits of the left-hand year ("...20[26] - 20 July 2026" → checkin 26th, checkout 20th).
_LONG_RANGE_DAY_FIRST = re.compile(
    r"(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?\s*[-–]\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})"
)
_LONG_RANGE_MONTH_FIRST = re.compile(
    r"([A-Za-z]+)\s+(\d{1,2})(?:\s+(\d{4}))?\s*[-–]\s*([A-Za-z]+)\s+(\d{1,2})\s+(\d{4})"
)
_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _mk_date(yr, mon_name, day):
    """Build a date from parts, or None if month name / day is invalid."""
    mon = _MONTH_MAP.get((mon_name or "")[:3].lower())
    if not mon:
        return None
    try:
        return date(int(yr), mon, int(day))
    except (ValueError, TypeError):
        return None


def _extract_date_range(window):
    """Try to parse any date range from window; return (chk_in_str, chk_out_str) or None.

    Returns None unless check-out is strictly after check-in, so a malformed or backwards
    window falls through cleanly instead of shipping a bad range to apidojo.
    """
    d_in = d_out = None

    m = _LONG_RANGE_DAY_FIRST.search(window)
    if m:
        day1, mon1, yr1, day2, mon2, yr2 = m.groups()
        d_in  = _mk_date(yr1 or yr2, mon1, day1)
        d_out = _mk_date(yr2, mon2, day2)
    elif (m := _LONG_RANGE_MONTH_FIRST.search(window)):
        mon1, day1, yr1, mon2, day2, yr2 = m.groups()
        d_in  = _mk_date(yr1 or yr2, mon1, day1)
        d_out = _mk_date(yr2, mon2, day2)
    elif (m := _EXPLICIT_RE.search(window)):
        groups = m.groups()
        if groups[4]:  # "Sep 10-14, 2026"
            mon_s = groups[4]; d1 = groups[5]; d2 = groups[6]; yr = groups[7]
        else:          # "10-14 Sep 2026"
            mon_s = groups[2]; d1 = groups[0]; d2 = groups[1]; yr = groups[3]
        d_in  = _mk_date(yr, mon_s, d1)
        d_out = _mk_date(yr, mon_s, d2)

    if d_in and d_out and d_out > d_in:
        return (str(d_in), str(d_out))
    return None


def _parse_window(window, destination=None):
    """Return (chk_in, chk_out) strings or None.

    - Explicit short range (≤7 nights) → use as-is.
    - Month-wide window → pick a Fri–Sun block mid-window.
    - Unparseable → None.
    """
    dates = _extract_date_range(window)
    if dates:
        chk_in_d  = date.fromisoformat(dates[0])
        chk_out_d = date.fromisoformat(dates[1])
        nights = (chk_out_d - chk_in_d).days
        if nights <= 7:
            return dates
        return _pick_weekend_block(chk_in_d, chk_out_d, destination)

    m = re.search(r"([A-Za-z]+)\s+(\d{4})", window)
    if m:
        mon = _MONTH_MAP.get(m.group(1)[:3].lower())
        yr  = int(m.group(2))
        if mon:
            start = date(yr, mon, 1)
            nxt = date(yr + (mon // 12), mon % 12 + 1, 1) if mon < 12 else date(yr + 1, 1, 1)
            end = nxt - timedelta(days=1)
            return _pick_weekend_block(start, end, destination)
    return None


def _pick_weekend_block(start, end, destination):
    """Pick a Fri–Sun 2-night block mid-window, bounded by city min nights if known."""
    min_nights = 2
    if destination:
        dest_lower = destination.lower()
        for city_key, (mn, _mx) in C.CITIES.items():
            if city_key.lower().split(",")[0] in dest_lower:
                min_nights = mn
                break

    nights = max(min_nights, 2)
    mid = start + (end - start) // 2
    days_to_fri = (4 - mid.weekday()) % 7
    fri = mid + timedelta(days=days_to_fri)
    sun = fri + timedelta(days=nights)
    if sun <= end:
        return (str(fri), str(sun))
    candidate_out = start + timedelta(days=nights)
    if candidate_out <= end:
        return (str(start), str(candidate_out))
    return None


# ── Window explicitness ──────────────────────────────────────────────────────

def _is_explicit_short_window(window):
    """True iff the window string contains an explicit date range of at most 7 nights."""
    dates = _extract_date_range(window)
    if not dates:
        return False
    return (date.fromisoformat(dates[1]) - date.fromisoformat(dates[0])).days <= 7


# ── Alternatives listing (city-wide best-value) ───────────────────────────────

def _price_alternatives(ref, chk_in, chk_out):
    """Return up to 3 HotelRate dicts from a city-wide price-sorted search.

    Keeps only property_cards with review_score >= 8.0 (cheapest first — the listing is
    already order_by=price). No price ceiling: the deterministic scorer downstream handles
    price. Returns [] if nothing qualifies (caller raises → LLM fallback).
    """
    cards = list_properties(ref, chk_in, chk_out)
    chk_in_d  = date.fromisoformat(chk_in)
    chk_out_d = date.fromisoformat(chk_out)
    nights = (chk_out_d - chk_in_d).days
    if nights <= 0:
        return []

    results = []
    for card in cards:
        score = card.get("review_score")
        if score is None or float(score) < 8.0:
            continue
        breakdown = card.get("composite_price_breakdown") or {}
        ppn_block = breakdown.get("gross_amount_per_night") or {}
        ppn = ppn_block.get("value")
        if ppn is None:
            continue
        total_raw = card.get("min_total_price")
        if total_raw is None:
            total_raw = (breakdown.get("gross_amount") or {}).get("value")
        if total_raw is None:
            continue
        name = card.get("hotel_name") or ""
        booking_url = "https://www.booking.com/searchresults.html?" + urlencode({
            "ss": name, "checkin": chk_in, "checkout": chk_out,
            "group_adults":   C.HOTEL_ADULTS,
            "group_children": len(C.HOTEL_CHILDREN_AGES),
            "age":            ",".join(map(str, C.HOTEL_CHILDREN_AGES)),
        })
        results.append({
            "name":                name,
            "checkin":             chk_in,
            "checkout":            chk_out,
            "nights":              nights,
            "price_per_night_eur": round(float(ppn), 2),
            "total_eur":           round(float(total_raw), 2),
            "booking_url":         booking_url,
            "source":              "Booking.com (apidojo)",
            "currency":            "EUR",
            "review_score":        score,
            "stars":               card.get("class"),
        })
        if len(results) >= 3:
            break
    return results


# ── Verdict logic ─────────────────────────────────────────────────────────────

def _decide_verdict(g, est):
    """g = grounded €/night, est = est_price_eur (Stage-1 estimate).
    Grounding no longer kills on price — the deterministic scorer downstream handles
    price entirely. confirm if the live price is close to the estimate, else correct."""
    if est and g <= est * 1.15:
        return "confirm", "high"
    return "correct", "high"


# ── Stage-3 result builder (no LLM) ──────────────────────────────────────────

def _to_stage3(rate, verdict, confidence, ref, today, mode="verified"):
    """Build a Stage-3 result dict from a single HotelRate (verified path). No LLM call."""
    chk_in  = rate["checkin"]
    chk_out = rate["checkout"]
    try:
        ci = date.fromisoformat(chk_in)
        co = date.fromisoformat(chk_out)
        dates_str = f"{ci.strftime('%b %-d')}-{co.strftime('%-d, %Y')}"
    except (ValueError, AttributeError):
        # Windows strftime doesn't support %-d; fall back to zero-padded
        try:
            ci = date.fromisoformat(chk_in)
            co = date.fromisoformat(chk_out)
            dates_str = f"{ci.strftime('%b %d')}-{co.strftime('%d, %Y')}"
        except Exception:
            dates_str = f"{chk_in} to {chk_out}"

    hotel_name = ref.get("name") or rate["name"]
    ppn  = rate["price_per_night_eur"]
    tot  = rate["total_eur"]
    nts  = rate["nights"]
    src  = f"Booking.com (apidojo) live {today}"
    burl = rate.get("booking_url")
    stars = rate.get("stars")
    score = rate.get("review_score")

    option = {
        "dates":               dates_str,
        "nights":              nts,
        "price_per_night_eur": ppn,
        "total_eur":           tot,
        "source":              src,
    }
    if burl:
        option["booking_url"] = burl

    stars_str = f", {int(stars)}-star" if stars else ""
    score_str = f", review {score}" if score else ""

    if verdict == "confirm":
        summary = (
            f"Verified {hotel_name}{stars_str} for {dates_str}: €{ppn}/night "
            f"(€{tot} total, {nts} nights){score_str}. "
            f"Price matches the Stage-1 estimate."
        )
    elif verdict == "correct":
        summary = (
            f"Verified {hotel_name}{stars_str} for {dates_str}: €{ppn}/night "
            f"(€{tot} total, {nts} nights){score_str}. "
            f"Price corrected from the Stage-1 estimate."
        )
    else:
        summary = (
            f"Grounded price for {hotel_name} ({dates_str}) is €{ppn}/night, "
            f"which exceeds the country ceiling. Not emailing."
        )

    how_to_book = (
        f"Book at {burl}" if burl
        else f"Search for '{hotel_name}' on Booking.com."
    )

    grounding_parts = [
        f"Booking.com (apidojo) /properties/v2/list for {chk_in}–{chk_out}, "
        f"currency=EUR, adults={C.HOTEL_ADULTS}, children={C.HOTEL_CHILDREN_AGES}."
    ]
    if stars:
        grounding_parts.append(f"Property class: {int(stars)}-star.")
    if score:
        grounding_parts.append(f"Review score: {score}.")
    grounding_parts.append(f"Live rate: €{ppn}/night (€{tot} total).")

    return {
        "destination":       hotel_name,
        "verdict":           verdict,
        "options":           [option],
        "how_to_book":       how_to_book,
        "grounding":         " ".join(grounding_parts),
        "assistant_summary": summary,
        "confidence":        confidence,
    }


# ── Alternatives Stage-3 builder ─────────────────────────────────────────────

def _to_stage3_alternatives(rates, ref, today):
    """Build a Stage-3 result for the alternatives path (city-wide best-value options).

    verdict=correct, confidence=medium. Never seeds baselines downstream.
    """
    rate0 = rates[0]
    chk_in  = rate0["checkin"]
    chk_out = rate0["checkout"]
    try:
        ci = date.fromisoformat(chk_in)
        co = date.fromisoformat(chk_out)
        dates_str = f"{ci.strftime('%b %-d')}-{co.strftime('%-d, %Y')}"
    except (ValueError, AttributeError):
        try:
            ci = date.fromisoformat(chk_in)
            co = date.fromisoformat(chk_out)
            dates_str = f"{ci.strftime('%b %d')}-{co.strftime('%d, %Y')}"
        except Exception:
            dates_str = f"{chk_in} to {chk_out}"

    named_hotel = ref.get("match_name") or ""
    city_name   = ref.get("name") or named_hotel
    src = f"Booking.com (apidojo) live {today}"

    options = []
    for r in rates:
        opt = {
            "dates":               dates_str,
            "nights":              r["nights"],
            "price_per_night_eur": r["price_per_night_eur"],
            "total_eur":           r["total_eur"],
            "source":              src,
            "name":                r["name"],
        }
        if r.get("booking_url"):
            opt["booking_url"] = r["booking_url"]
        options.append(opt)

    intro = f"Couldn't confirm {named_hotel} live; " if named_hotel else ""
    hotel_list = "; ".join(
        f"{r['name']} €{r['price_per_night_eur']}/night" for r in rates
    )
    summary = f"{intro}best-value family stays in {city_name} for {dates_str}: {hotel_list}."

    return {
        "destination":       city_name,
        "verdict":           "correct",
        "options":           options,
        "how_to_book":       "Search the listed properties on Booking.com for the dates above.",
        "grounding":         (
            f"Booking.com (apidojo) city search {chk_in}-{chk_out}, "
            f"order_by=price, review_score>=8.0. "
            f"{len(rates)} option(s) returned."
        ),
        "assistant_summary": summary,
        "confidence":        "medium",
    }


# ── Public entry point ────────────────────────────────────────────────────────

def ground_api(diamond, mem_text, today):
    """Stage-3 grounding via Booking.com (apidojo). Falls back to _ground_llm on any failure.

    kind=="hotel" → verified path (single exact property, confidence high).
    kind=="city"  → alternatives path (city best-value list, confidence medium).
    Any failure → LLM fallback.
    """
    try:
        if not C.RAPIDAPI_KEY:
            raise ValueError("RAPIDAPI_KEY not set")

        destination = diamond.get("destination", "")
        est         = diamond.get("est_price_eur") or 0

        ref = resolve_hotel(diamond)
        if not ref:
            raise ValueError(f"Could not resolve hotel for: {destination}")

        dates = _parse_window(diamond.get("window", ""), destination)
        if not dates:
            raise ValueError(f"Could not parse window: {diamond.get('window')}")

        chk_in, chk_out = dates

        if ref["kind"] == "hotel":
            # Verified path: hotel-type dest → single property
            rate = price(ref, chk_in, chk_out)
            if not rate:
                raise ValueError(f"Brand sanity failed for {destination} {chk_in}-{chk_out}")
            verdict, _ = _decide_verdict(rate["price_per_night_eur"], est)
            confidence = "high" if _is_explicit_short_window(diamond.get("window", "")) else "medium"
            return _to_stage3(rate, verdict, confidence, ref, today)
        else:
            # Alternatives path: city-wide best-value options
            rates = _price_alternatives(ref, chk_in, chk_out)
            if not rates:
                raise ValueError(f"No qualifying alternatives in {destination} {chk_in}-{chk_out}")
            return _to_stage3_alternatives(rates, ref, today)

    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"  [providers] Booking.com grounding failed ({exc}), falling back to LLM")
        import find_city_anomalies as fa  # lazy import to avoid circular dependency
        return fa._ground_llm(diamond, mem_text, today)
