"""Offline unit tests for providers.py (Booking.com / apidojo grounding).

Monkey-patches providers._get with canned apidojo JSON so no network calls are made.
Non-destructive: snapshots and restores state/ files on entry/exit.

Run: python test_providers.py

Separately verify that the _ground_llm seam still works:
  HOTEL_PROVIDER="" python test_stub.py
"""

import os, sys

os.makedirs("state", exist_ok=True)

# ── Snapshot state files ──────────────────────────────────────────────────────
_STATE_FILES = [
    os.path.join("state", f)
    for f in ("signals_seen.json", "city_signals.json", "city_signals.md",
              "memory.json", "memory.md")
]
_snapshots = {}
for _sf in _STATE_FILES:
    if os.path.exists(_sf):
        with open(_sf, encoding="utf-8") as _fh:
            _snapshots[_sf] = _fh.read()
    else:
        _snapshots[_sf] = None

_real_ground_llm = None
_failed = []

try:
    import requests
    import config as C
    import providers as P
    import find_city_anomalies as fa

    C.RAPIDAPI_KEY = "dummy-test-key"
    _real_ground_llm = fa._ground_llm

    # ── Canned auto-complete data ─────────────────────────────────────────────

    # Real-shape Kempinski AC: landmark 234283 + hotel-type 29085 (from live curl)
    _AC_KEMPINSKI = [
        {"dest_id": "234283", "dest_type": "landmark",
         "name": "Hotel Kempinski Grand Arena Bansko", "country": "Bulgaria"},
        {"dest_id": "29085",  "dest_type": "hotel",
         "name": "Kempinski Hotel Grand Arena",         "country": "Bulgaria"},
    ]
    # Regnum: hotel-type entry present
    _AC_REGNUM_WITH_HOTEL = [
        {"dest_id": "city-1001", "dest_type": "city",  "name": "Bansko",              "country": "Bulgaria"},
        {"dest_id": "htl-5001",  "dest_type": "hotel",  "name": "Regnum Hotel Bansko", "country": "Bulgaria"},
    ]
    # No hotel-type → city fallback (alternatives path)
    _AC_BANSKO_CITY_ONLY = [
        {"dest_id": "city-1001", "dest_type": "city", "name": "Bansko", "country": "Bulgaria"},
    ]
    # Empty → resolve_hotel returns None
    _AC_EMPTY = []

    # ── Canned property-list data ─────────────────────────────────────────────

    # Single Kempinski card (from search_type=hotel dest_id=29085) — under ceiling
    _PROPS_KEMPINSKI_UNDER = {
        "result": [{
            "type": "property_card",
            "hotel_name": "Kempinski Hotel Grand Arena",
            "class": 5, "review_score": 9.1,
            "composite_price_breakdown": {
                "gross_amount_per_night": {"value": 85.0, "currency": "EUR"},
                "gross_amount":           {"value": 170.0, "currency": "EUR"},
            },
            "min_total_price": 170.0,
        }]
    }
    # Kempinski — over Bulgaria ceiling (100)
    _PROPS_KEMPINSKI_OVER = {
        "result": [{
            "type": "property_card",
            "hotel_name": "Kempinski Hotel Grand Arena",
            "class": 5, "review_score": 9.1,
            "composite_price_breakdown": {
                "gross_amount_per_night": {"value": 155.0, "currency": "EUR"},
                "gross_amount":           {"value": 310.0, "currency": "EUR"},
            },
            "min_total_price": 310.0,
        }]
    }
    # Wrong brand — brand-token sanity check should fail
    _PROPS_WRONG_BRAND = {
        "result": [{
            "type": "property_card",
            "hotel_name": "Hilton Bansko",
            "class": 4, "review_score": 8.0,
            "composite_price_breakdown": {
                "gross_amount_per_night": {"value": 80.0, "currency": "EUR"},
            },
            "min_total_price": 160.0,
        }]
    }
    # City-wide list (order_by=price): mixed review / price. No price cap anymore, so
    # qualifying = review>=8.0: Platinum (8.5, 70), Mountain (8.3, 85), Luxury (9.0, 120).
    # Filtered: Budget (review 7.2 < 8.0).
    _PROPS_CITY_BANSKO = {
        "result": [
            {"type": "property_card", "hotel_name": "Budget Stay", "review_score": 7.2, "class": 3,
             "composite_price_breakdown": {"gross_amount_per_night": {"value": 45.0}},
             "min_total_price": 90.0},
            {"type": "property_card", "hotel_name": "Platinum Family Hotel", "review_score": 8.5, "class": 4,
             "composite_price_breakdown": {"gross_amount_per_night": {"value": 70.0}},
             "min_total_price": 140.0},
            {"type": "property_card", "hotel_name": "Mountain View Resort", "review_score": 8.3, "class": 4,
             "composite_price_breakdown": {"gross_amount_per_night": {"value": 85.0}},
             "min_total_price": 170.0},
            {"type": "property_card", "hotel_name": "Luxury Suite Over Ceiling", "review_score": 9.0, "class": 5,
             "composite_price_breakdown": {"gross_amount_per_night": {"value": 120.0}},
             "min_total_price": 240.0},
        ]
    }
    # City-wide list — nothing qualifies (both review < 8.0; there is no price cap now)
    _PROPS_CITY_NONE = {
        "result": [
            {"type": "property_card", "hotel_name": "Cheap Bad Hotel", "review_score": 6.5, "class": 2,
             "composite_price_breakdown": {"gross_amount_per_night": {"value": 30.0}},
             "min_total_price": 60.0},
            {"type": "property_card", "hotel_name": "Pricey Mediocre", "review_score": 7.8, "class": 5,
             "composite_price_breakdown": {"gross_amount_per_night": {"value": 200.0}},
             "min_total_price": 400.0},
        ]
    }

    # ── _get factory helpers ──────────────────────────────────────────────────

    def _make_get(ac_data, props_data):
        def _get_fn(path, params):
            if path == "/locations/auto-complete":
                return ac_data
            return props_data
        return _get_fn

    def _get_http_error(path, params):
        raise requests.RequestException("Simulated HTTP 503")

    # ── Stub for _ground_llm ──────────────────────────────────────────────────

    _LLM_FALLBACK = {
        "verdict": "confirm", "options": [], "confidence": "high",
        "assistant_summary": "LLM fallback", "how_to_book": "", "grounding": "",
    }

    def _stub_llm(diamond, mem_text, today):
        _stub_llm.calls += 1
        return _LLM_FALLBACK

    _stub_llm.calls = 0

    # ── Assertion helpers ─────────────────────────────────────────────────────

    def ok(name):
        print(f"  [OK] {name}")

    def chk(name, cond, detail=""):
        if cond:
            ok(name)
        else:
            msg = f"  [FAIL] {name}" + (f": {detail}" if detail else "")
            print(msg)
            _failed.append(name)

    # ── Tests ─────────────────────────────────────────────────────────────────

    print("\n=== test_providers.py ===\n")

    # ── 1. resolve_hotel: Kempinski real AC shape → hotel-type preferred ──────
    P._get = _make_get(_AC_KEMPINSKI, _PROPS_KEMPINSKI_UNDER)
    C.HOTEL_MAPPING = {}
    ref = P.resolve_hotel({"hotel_name": "Kempinski Hotel Grand Arena", "city": "Bansko", "country": "Bulgaria"})
    chk("resolve_hotel: Kempinski hotel-type dest preferred (dest_id=29085, kind=hotel)",
        ref is not None
        and ref.get("dest_id") == "29085"
        and ref.get("kind") == "hotel"
        and ref.get("search_type") == "hotel"
        and ref.get("match_name") == "Kempinski Hotel Grand Arena",
        f"got: {ref}")

    # ── 2. resolve_hotel: no hotel-type → city fallback (kind=city) ───────────
    P._get = _make_get(_AC_BANSKO_CITY_ONLY, _PROPS_CITY_BANSKO)
    ref_city = P.resolve_hotel({"hotel_name": "Regnum Hotel", "city": "Bansko", "country": "Bulgaria"})
    chk("resolve_hotel: city fallback when no hotel-type entry (kind=city)",
        ref_city is not None
        and ref_city.get("kind") == "city"
        and ref_city.get("search_type") == "city",
        f"got: {ref_city}")

    # ── 3. resolve_hotel: country mismatch → None ─────────────────────────────
    P._get = _make_get(_AC_KEMPINSKI, _PROPS_KEMPINSKI_UNDER)
    ref_mm = P.resolve_hotel({"hotel_name": "Kempinski Hotel Grand Arena", "city": "Bansko", "country": "Greece"})
    chk("resolve_hotel: country mismatch -> None",
        ref_mm is None, f"expected None, got {ref_mm}")

    # ── 4. resolve_hotel: empty hotel_name → city entry (alternatives) ────────
    P._get = _make_get(_AC_BANSKO_CITY_ONLY, _PROPS_CITY_BANSKO)
    ref_empty = P.resolve_hotel({"hotel_name": "", "city": "Bansko", "country": "Bulgaria"})
    chk("resolve_hotel: empty hotel_name -> city entry for alternatives",
        ref_empty is not None
        and ref_empty.get("kind") == "city"
        and ref_empty.get("dest_id") == "city-1001",
        f"got: {ref_empty}")

    # ── 5. resolve_hotel: empty AC list → None ────────────────────────────────
    P._get = _make_get(_AC_EMPTY, _PROPS_KEMPINSKI_UNDER)
    ref_none = P.resolve_hotel({"hotel_name": "Kempinski Hotel Grand Arena", "city": "Bansko", "country": "Bulgaria"})
    chk("resolve_hotel: empty AC list -> None",
        ref_none is None, f"expected None, got {ref_none}")

    # ── 6. HOTEL_MAPPING short-circuits /locations/auto-complete ─────────────
    _get_calls = []
    P._get = lambda path, params: (_get_calls.append(path), _make_get(_AC_KEMPINSKI, _PROPS_KEMPINSKI_UNDER)(path, params))[1]
    # alias "kempinski grand arena" is a substring of lookup "kempinski grand arena bansko"
    C.HOTEL_MAPPING = {"kempinski grand arena": {"dest_id": "99999", "search_type": "hotel", "name": "Kempinski Custom"}}
    ref_map = P.resolve_hotel({"hotel_name": "Kempinski Grand Arena", "city": "Bansko", "country": "Bulgaria"})
    C.HOTEL_MAPPING = {}
    chk("HOTEL_MAPPING short-circuits auto-complete",
        ref_map is not None
        and ref_map.get("dest_id") == "99999"
        and len(_get_calls) == 0,
        f"dest_id={ref_map and ref_map.get('dest_id')!r}, calls={_get_calls}")

    P._get = _make_get(_AC_KEMPINSKI, _PROPS_KEMPINSKI_UNDER)

    # ── 7. _match_score: overlap regression ──────────────────────────────────
    # Old issubset: {"kempinski","grand","arena","bansko"}.issubset({"kempinski","grand","arena"}) = False
    # New overlap:  |{"kempinski","grand","arena","bansko"} & {"kempinski","grand","arena"}| / 4 = 0.75
    score_label_vs_card = P._match_score(
        "Hotel Kempinski Grand Arena Bansko",  # landmark label
        "Kempinski Hotel Grand Arena"          # actual property card
    )
    score_matchname_vs_card = P._match_score(
        "Kempinski Hotel Grand Arena",         # Stage-1 hotel_name (match_name)
        "Kempinski Hotel Grand Arena"
    )
    chk("_match_score regression: landmark label 0.75, match_name 1.0 — both pass 0.6",
        score_label_vs_card >= 0.6 and score_matchname_vs_card >= 0.99,
        f"label_score={score_label_vs_card:.2f}, matchname_score={score_matchname_vs_card:.2f}")

    # ── 8. price(): verified path (kind=hotel) — brand matches → HotelRate ───
    ref_kemp = {
        "dest_id": "29085", "search_type": "hotel", "kind": "hotel",
        "name": "Kempinski Hotel Grand Arena", "match_name": "Kempinski Hotel Grand Arena",
    }
    rate = P.price(ref_kemp, "2026-08-08", "2026-08-10")
    chk("price(): kind=hotel, brand matches -> HotelRate ppn=85",
        rate is not None
        and rate["price_per_night_eur"] == 85.0
        and rate["nights"] == 2
        and rate["source"] == "Booking.com (apidojo)",
        f"got: {rate}")

    # ── 9. price(): brand token mismatch → None ───────────────────────────────
    P._get = _make_get(_AC_KEMPINSKI, _PROPS_WRONG_BRAND)
    rate_none = P.price(ref_kemp, "2026-08-08", "2026-08-10")
    chk("price(): brand token mismatch (Hilton vs Kempinski) -> None",
        rate_none is None, f"got: {rate_none}")

    P._get = _make_get(_AC_KEMPINSKI, _PROPS_KEMPINSKI_UNDER)

    # ── 10–11. _decide_verdict: confirm / correct (no price ceiling — fully soft) ─
    v, c = P._decide_verdict(80.0, 85.0)
    chk("_decide_verdict: confirm (g <= est*1.15)",
        v == "confirm" and c == "high", f"got ({v!r},{c!r})")

    v, c = P._decide_verdict(120.0, 85.0)
    chk("_decide_verdict: correct (g > est*1.15, never kills on price)",
        v == "correct" and c == "high", f"got ({v!r},{c!r})")

    # ── 13. _to_stage3 verified: dates has 4-digit year + passes _dates_in_window
    ref_s3 = {"name": "Kempinski Hotel Grand Arena", "match_name": "Kempinski Hotel Grand Arena",
               "dest_id": "29085", "search_type": "hotel", "kind": "hotel"}
    rate_s3 = {
        "name": "Kempinski Hotel Grand Arena", "checkin": "2026-08-08", "checkout": "2026-08-10",
        "nights": 2, "price_per_night_eur": 85.0, "total_eur": 170.0,
        "booking_url": "https://www.booking.com/hotel/bg/kempinski.html",
        "source": "Booking.com (apidojo)", "review_score": 9.1, "stars": 5,
    }
    r = P._to_stage3(rate_s3, "confirm", "high", ref_s3, "2026-06-29")
    opt_dates = (r.get("options") or [{}])[0].get("dates", "")
    chk("_to_stage3 verified: options[0].dates contains 4-digit year",
        "2026" in opt_dates, f"dates={opt_dates!r}")
    chk("_to_stage3 verified: dates passes _dates_in_window",
        fa._dates_in_window(opt_dates, "Aug 2026"), f"dates={opt_dates!r} vs 'Aug 2026'")

    # ── 14. _price_alternatives: filters review>=8.0 only (no price cap) → 3 results
    P._get = _make_get(_AC_BANSKO_CITY_ONLY, _PROPS_CITY_BANSKO)
    ref_alts = {"dest_id": "city-1001", "search_type": "city", "kind": "city",
                "name": "Bansko", "match_name": "Regnum Hotel"}
    alts = P._price_alternatives(ref_alts, "2026-08-08", "2026-08-10")
    chk("_price_alternatives: 3 qualifying options (review>=8.0, no price cap)",
        len(alts) == 3
        and alts[0].get("name") not in ("", None)
        and all(float(r.get("review_score", 0)) >= 8.0 for r in alts),
        f"got {len(alts)} alts: {[(r.get('name'), r.get('price_per_night_eur')) for r in alts]}")

    # ── 15. _price_alternatives: none qualify (all review<8.0) → empty list ──
    P._get = _make_get(_AC_BANSKO_CITY_ONLY, _PROPS_CITY_NONE)
    alts_none = P._price_alternatives(ref_alts, "2026-08-08", "2026-08-10")
    chk("_price_alternatives: none qualify -> []",
        alts_none == [], f"got: {alts_none}")

    P._get = _make_get(_AC_KEMPINSKI, _PROPS_KEMPINSKI_UNDER)

    # ── 16. _to_stage3_alternatives: verdict=correct, confidence=medium, multi-options
    P._get = _make_get(_AC_BANSKO_CITY_ONLY, _PROPS_CITY_BANSKO)
    alts2 = P._price_alternatives(ref_alts, "2026-08-08", "2026-08-10")
    alts_r = P._to_stage3_alternatives(alts2, ref_alts, "2026-06-29")
    chk("_to_stage3_alternatives: verdict=correct, confidence=medium, 3 options",
        alts_r.get("verdict") == "correct"
        and alts_r.get("confidence") == "medium"
        and len(alts_r.get("options", [])) == 3
        and "Couldn't confirm Regnum Hotel" in alts_r.get("assistant_summary", ""),
        f"got: verdict={alts_r.get('verdict')!r}, conf={alts_r.get('confidence')!r}, "
        f"n_opts={len(alts_r.get('options',[]))}, summary={alts_r.get('assistant_summary','')[:60]!r}")

    P._get = _make_get(_AC_KEMPINSKI, _PROPS_KEMPINSKI_UNDER)

    # Shared diamond for ground_api tests
    _kemp_diamond = {
        "destination": "Kempinski Hotel Grand Arena, Bansko, Bulgaria",
        "hotel_name":  "Kempinski Hotel Grand Arena",
        "city":        "Bansko",
        "country":     "Bulgaria",
        "est_price_eur": 90,
        "window": "Aug 8-10, 2026",  # explicit short window
    }
    _city_diamond = {
        "destination": "Bansko, Bulgaria",
        "hotel_name":  "Regnum Hotel",
        "city":        "Bansko",
        "country":     "Bulgaria",
        "est_price_eur": 80,
        "window": "Aug 2026",  # month-wide → guessed dates
    }

    # ── 17. ground_api: empty RAPIDAPI_KEY → LLM fallback ────────────────────
    fa._ground_llm = _stub_llm; _stub_llm.calls = 0
    C.RAPIDAPI_KEY = ""
    result = P.ground_api(_kemp_diamond, "memory", "2026-06-29")
    C.RAPIDAPI_KEY = "dummy-test-key"
    fa._ground_llm = _real_ground_llm
    chk("ground_api: empty RAPIDAPI_KEY -> LLM fallback",
        _stub_llm.calls > 0 and result.get("assistant_summary") == "LLM fallback",
        f"calls={_stub_llm.calls}")

    # ── 18. ground_api: resolve_hotel returns None → LLM fallback ────────────
    fa._ground_llm = _stub_llm; _stub_llm.calls = 0
    P._get = _make_get(_AC_EMPTY, _PROPS_KEMPINSKI_UNDER)
    result = P.ground_api(_kemp_diamond, "memory", "2026-06-29")
    fa._ground_llm = _real_ground_llm
    chk("ground_api: resolve_hotel None (empty AC) -> LLM fallback",
        _stub_llm.calls > 0 and result.get("assistant_summary") == "LLM fallback",
        f"calls={_stub_llm.calls}")

    # ── 19. ground_api: kind=hotel, price well above estimate → verdict=correct ─
    #    (no ceiling kill anymore — grounding returns the real price; the scorer handles it)
    fa._ground_llm = _stub_llm; _stub_llm.calls = 0
    P._get = _make_get(_AC_KEMPINSKI, _PROPS_KEMPINSKI_OVER)
    result = P.ground_api(_kemp_diamond, "memory", "2026-06-29")
    fa._ground_llm = _real_ground_llm
    chk("ground_api: kind=hotel, ppn>>est -> verdict=correct (no ceiling kill, not LLM fallback)",
        _stub_llm.calls == 0
        and result.get("verdict") == "correct",
        f"llm_calls={_stub_llm.calls}, verdict={result.get('verdict')!r}, conf={result.get('confidence')!r}")

    # ── 20. ground_api: kind=city, alternatives → correct, medium ────────────
    fa._ground_llm = _stub_llm; _stub_llm.calls = 0
    P._get = _make_get(_AC_BANSKO_CITY_ONLY, _PROPS_CITY_BANSKO)
    result = P.ground_api(_city_diamond, "memory", "2026-06-29")
    fa._ground_llm = _real_ground_llm
    chk("ground_api: kind=city, qualifying alts -> verdict=correct, confidence=medium",
        _stub_llm.calls == 0
        and result.get("verdict") == "correct"
        and result.get("confidence") == "medium"
        and len(result.get("options", [])) >= 1,
        f"llm_calls={_stub_llm.calls}, verdict={result.get('verdict')!r}, "
        f"conf={result.get('confidence')!r}, n_opts={len(result.get('options',[]))}")

    # ── 21. ground_api: kind=city, none qualify → LLM fallback ───────────────
    fa._ground_llm = _stub_llm; _stub_llm.calls = 0
    P._get = _make_get(_AC_BANSKO_CITY_ONLY, _PROPS_CITY_NONE)
    result = P.ground_api(_city_diamond, "memory", "2026-06-29")
    P._get = _make_get(_AC_KEMPINSKI, _PROPS_KEMPINSKI_UNDER)
    fa._ground_llm = _real_ground_llm
    chk("ground_api: kind=city, none qualify -> LLM fallback",
        _stub_llm.calls > 0 and result.get("assistant_summary") == "LLM fallback",
        f"calls={_stub_llm.calls}")

    # ── 22. ground_api: HTTP error → LLM fallback ────────────────────────────
    fa._ground_llm = _stub_llm; _stub_llm.calls = 0
    P._get = _get_http_error
    result = P.ground_api(_kemp_diamond, "memory", "2026-06-29")
    P._get = _make_get(_AC_KEMPINSKI, _PROPS_KEMPINSKI_UNDER)
    fa._ground_llm = _real_ground_llm
    chk("ground_api: HTTP error -> LLM fallback",
        _stub_llm.calls > 0 and result.get("assistant_summary") == "LLM fallback",
        f"calls={_stub_llm.calls}")

    # ── 23–27. _extract_date_range: window-format regressions ────────────────
    # The bug: FIND emits "17 July 2026 - 20 July 2026" (full date both sides). The old
    # regex latched onto the last two digits of the left-hand year → checkin AFTER checkout,
    # apidojo returned nothing, and every candidate fell through to the LLM. Guard against it.
    chk("_extract_date_range: full date both sides -> forward range",
        P._extract_date_range("17 July 2026 - 20 July 2026") == ("2026-07-17", "2026-07-20"),
        f"got {P._extract_date_range('17 July 2026 - 20 July 2026')}")

    chk("_extract_date_range: year only on right side",
        P._extract_date_range("17 July - 20 July 2026") == ("2026-07-17", "2026-07-20"),
        f"got {P._extract_date_range('17 July - 20 July 2026')}")

    chk("_extract_date_range: month-first full date both sides",
        P._extract_date_range("July 17 2026 - July 20 2026") == ("2026-07-17", "2026-07-20"),
        f"got {P._extract_date_range('July 17 2026 - July 20 2026')}")

    chk("_extract_date_range: short forms still parse",
        P._extract_date_range("Sep 10-14, 2026") == ("2026-09-10", "2026-09-14")
        and P._extract_date_range("10-14 Sep 2026") == ("2026-09-10", "2026-09-14"),
        f"got {P._extract_date_range('Sep 10-14, 2026')} / {P._extract_date_range('10-14 Sep 2026')}")

    chk("_extract_date_range: backwards window -> None",
        P._extract_date_range("20 July 2026 - 17 July 2026") is None,
        f"got {P._extract_date_range('20 July 2026 - 17 July 2026')}")

    # ── 28. ground_api: full-date window resolves via apidojo, NOT the LLM ────
    #    The payoff of the parse fix — the exact window shape that used to force fallback.
    fa._ground_llm = _stub_llm; _stub_llm.calls = 0
    P._get = _make_get(_AC_KEMPINSKI, _PROPS_KEMPINSKI_UNDER)
    _kemp_longwin = {**_kemp_diamond, "window": "8 August 2026 - 10 August 2026"}
    result = P.ground_api(_kemp_longwin, "memory", "2026-06-29")
    fa._ground_llm = _real_ground_llm
    chk("ground_api: full-date window grounds via apidojo (no LLM fallback), verdict=confirm",
        _stub_llm.calls == 0
        and result.get("verdict") == "confirm"
        and (result.get("options") or [{}])[0].get("price_per_night_eur") == 85.0,
        f"llm_calls={_stub_llm.calls}, verdict={result.get('verdict')!r}, "
        f"opt0={ (result.get('options') or [{}])[0] }")

    # ── Summary ───────────────────────────────────────────────────────────────
    total = 28
    passed = total - len(_failed)
    print(f"\n{passed}/{total} tests passed.")
    if _failed:
        print(f"FAILED: {_failed}")
        sys.exit(1)

finally:
    try:
        if _real_ground_llm is not None:
            import find_city_anomalies as _fa
            _fa._ground_llm = _real_ground_llm
    except Exception:
        pass
    for _sf, _content in _snapshots.items():
        if _content is None:
            if os.path.exists(_sf):
                os.remove(_sf)
        else:
            with open(_sf, "w", encoding="utf-8") as _fh:
                _fh.write(_content)
    print("State files restored.")
