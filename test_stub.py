"""
Stub verification for the diamond finder pipeline (deterministic scoring model).

Runs the real pipeline in a throwaway temp directory (non-destructive) with:
  - common.llm stubbed by response_schema — Stage 1 FIND candidates, Stage 3 scorer scores.
  - find_city_anomalies.ground_deal stubbed per destination — live grounding results.

Coverage:
  - No hard ceiling: Kempinski (FIND est €158) is NOT gate-dropped; it grounds at €85 and,
    as a standout property (high LLM score), scores a DIAMOND — the "same €85 is a diamond
    for Kempinski but merely good/skip for an ordinary hotel" behaviour.
  - Regnum grounds at €112 and sinks to SKIP purely via the uncapped price penalty (no wall).
  - Arte Spa: grounding KILL (hallucination) → dropped before scoring.
  - Sofia: grounding low-confidence → data-quality guard blocks it before scoring.
  - Deterministic tiers: final = llm + price_adj + transit_adj.
  - Scores recorded in memory (llm_score/final_score) for every scored candidate.
  - Email digest shows EVERY scored candidate (diamond/good/skip) with its score breakdown,
    plus a "seen & dropped" footer for grounding kills (Arte) and guard blocks (Sofia).
  - Email digest: tier badges, baseline comparison, child-price caveat.

Run: python test_stub.py
"""

import json, os, sys, tempfile, shutil

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

_cwd = os.getcwd()
sandbox = tempfile.mkdtemp(prefix="dh_stub_")
os.makedirs(os.path.join(sandbox, "state"))
os.chdir(sandbox)

# Seed a prior Antalya baseline so the email "typically ~€X/night" line renders.
with open("state/memory.json", "w", encoding="utf-8") as f:
    json.dump({"baselines": {"Antalya 5-Star All-Inclusive|2027-01": {
        "realistic_price_eur": 100, "note": "seeded", "source": "test",
        "updated": "2026-12-01"}}, "ledger": []}, f)
for _name, _seed in [("signals_seen.json", {"seen": {}, "monthly_count": {}}),
                     ("city_signals.json", {})]:
    with open(f"state/{_name}", "w", encoding="utf-8") as f:
        json.dump(_seed, f)

import config as C
import common as X
import find_city_anomalies as fa

# ── Canned Stage 1 (FIND) ─────────────────────────────────────────────────────
_STAGE1 = {"candidates": [
    {"destination": "Antalya 5-Star All-Inclusive", "hotel_name": "Rixos Premium Antalya",
     "city": "Antalya", "country": "Turkey", "score": 88, "type": "hotel",
     "window": "Jan 10-14, 2027", "est_price_eur": 98,
     "reason": "5-star AI, indoor pools + kids club open in January.", "confidence": "high"},
    {"destination": "Kempinski Hotel Grand Arena, Bansko, Bulgaria", "hotel_name": "Kempinski Grand Arena",
     "city": "Bansko", "country": "Bulgaria", "score": 83, "type": "hotel",
     "window": "Jul 2026", "est_price_eur": 158,
     "reason": "5-star ski resort, spa open year-round.", "confidence": "high"},
    {"destination": "Regnum Bansko, Bulgaria", "hotel_name": "Regnum Bansko", "city": "Bansko",
     "country": "Bulgaria", "score": 84, "type": "hotel", "window": "Aug 8-10, 2026",
     "est_price_eur": 84, "reason": "Luxury alpine resort, indoor pool.", "confidence": "high"},
    {"destination": "Arte Spa & Park, Velingrad, Bulgaria", "hotel_name": "Arte Spa Park",
     "city": "Velingrad", "country": "Bulgaria", "score": 81, "type": "hotel",
     "window": "Jul 15-18, 2026", "est_price_eur": 80,
     "reason": "Thermal spa package.", "confidence": "medium"},
    {"destination": "Sofia City Break, Bulgaria", "hotel_name": "Sofia Balkan Palace",
     "city": "Sofia", "country": "Bulgaria", "score": 82, "type": "hotel",
     "window": "Sep 5-7, 2026", "est_price_eur": 90,
     "reason": "City weekend.", "confidence": "medium"},
]}

# ── Canned Stage 3 (SCORER) — desirability scores, price held neutral ─────────
_SCORES = [
    {"deal_id": 1, "destination": "Antalya 5-Star All-Inclusive", "score": 86,
     "why": "Standout AI resort, high in-window utility.", "red_flags": "Confirm kids club Jan hours."},
    {"deal_id": 2, "destination": "Kempinski Hotel Grand Arena, Bansko, Bulgaria", "score": 90,
     "why": "Genuinely special 5-star property with full family spa.", "red_flags": "Confirm pool heating."},
    {"deal_id": 3, "destination": "Regnum Bansko, Bulgaria", "score": 80,
     "why": "Comfortable resort, pleasant but not exceptional.", "red_flags": "Check August weekend rates."},
    # Arte is a grounding kill and Sofia is guard-blocked, so they never reach the scorer;
    # include them anyway to prove the pipeline ignores scores for non-scored candidates.
    {"deal_id": 4, "destination": "Arte Spa & Park, Velingrad, Bulgaria", "score": 70, "why": "x", "red_flags": "x"},
    {"deal_id": 5, "destination": "Sofia City Break, Bulgaria", "score": 75, "why": "x", "red_flags": "x"},
]

def _stub_llm(messages, model, max_tokens=2000, want_search=False, response_schema=None,
              provider=None, search_prompt=None):
    if response_schema is C.STAGE1_RESPONSE_SCHEMA:
        print("  [stub] llm: Stage 1 FIND")
        return json.dumps(_STAGE1)
    if response_schema is C.STAGE2_RESPONSE_SCHEMA:
        print("  [stub] llm: Stage 3 SCORER")
        return json.dumps(_SCORES)
    raise AssertionError(f"unexpected llm schema={response_schema}")

def _opt(ppn, total, dates, nights):
    return {"dates": dates, "nights": nights, "price_per_night_eur": ppn, "total_eur": total,
            "booking_url": "https://www.booking.com/hotel/x.html",
            "source": "Booking.com (apidojo) live 2026-06-28"}

_GROUND = {
    "Antalya 5-Star All-Inclusive": {"destination": "Rixos Premium Antalya", "verdict": "correct",
        "confidence": "high", "how_to_book": "Book at booking.com", "grounding": "apidojo live",
        "assistant_summary": "Rixos Premium Antalya, Jan 10-14: €70/night (€280 total).",
        "options": [_opt(70, 280, "Jan 10-14, 2027", 4)]},
    "Kempinski Hotel Grand Arena, Bansko, Bulgaria": {"destination": "Kempinski Grand Arena",
        "verdict": "correct", "confidence": "high", "how_to_book": "Book at booking.com",
        "grounding": "apidojo live", "assistant_summary": "Kempinski Grand Arena, Jul 10-13: €85/night.",
        "options": [_opt(85, 255, "Jul 10-13, 2026", 3)]},
    "Regnum Bansko, Bulgaria": {"destination": "Regnum Bansko", "verdict": "correct",
        "confidence": "high", "how_to_book": "Book at booking.com", "grounding": "apidojo live",
        "assistant_summary": "Regnum Bansko, Aug 8-10: €112/night.",
        "options": [_opt(112, 224, "Aug 8-10, 2026", 2)]},
    # Grounding kill (hallucination) → dropped before scoring.
    "Arte Spa & Park, Velingrad, Bulgaria": {"destination": "Arte Spa Park", "verdict": "kill",
        "confidence": "high", "options": [], "how_to_book": "", "grounding": "overpriced for market",
        "assistant_summary": "At €165/night, top of the Velingrad market — no arbitrage."},
    # Low-confidence grounding → data-quality guard blocks it before scoring.
    "Sofia City Break, Bulgaria": {"destination": "Sofia Balkan Palace", "verdict": "correct",
        "confidence": "low", "how_to_book": "", "grounding": "search returned no firm rate",
        "assistant_summary": "Could not verify a firm Sofia rate.",
        "options": [_opt(88, 176, "Sep 5-7, 2026", 2)]},
}
def _stub_ground(diamond, mem_text, today):
    return _GROUND.get(diamond.get("destination"), {})

_email = {}
def _stub_send(subject, html, text):
    _email["subject"], _email["html"], _email["text"] = subject, html, text

X.llm = _stub_llm
fa.ground_deal = _stub_ground
X.send_email = _stub_send

try:
    print("\n=== Running stub test (scoring model) ===\n")
    fa.main()

    print("\n=== Assertions ===")
    assert _email, "send_email was never called — no diamonds/goods reached email"
    html, text = _email["html"], _email["text"]

    # Kempinski: standout property at €85 → DIAMOND (the key context-dependence demo).
    assert "Kempinski" in html, "Kempinski (diamond at €85) should be emailed"
    assert "💎 Diamond" in html, "diamond badge missing"
    # Antalya diamond too (cheap + standout).
    assert "Rixos" in html or "Antalya" in html, "Antalya diamond missing from email"
    assert "2 diamond" in _email["subject"], _email["subject"]
    print("Kempinski + Antalya emailed as diamonds [OK]")

    # Regnum: grounded €112 sinks to SKIP via price penalty (no ceiling), but the digest now
    # SHOWS skips (with their score breakdown) so the reader sees what the pipeline weighed.
    assert "Regnum" in html, "Regnum (skip) should now appear in the digest body"
    assert "· Skipped" in html, "skip badge missing"
    assert "= <b>63</b>/100" in html, "Regnum score breakdown missing/incorrect"
    print("Regnum skip shown in digest with score breakdown [OK]")

    # Arte (grounding kill) and Sofia (guard block) appear in the 'seen & dropped' footer.
    assert "seen &amp; dropped" in html.lower(), "dropped footer missing"
    assert "Arte" in html and "killed" in html, "killed Arte missing from footer"
    assert "Sofia" in html and "blocked" in html, "guard-blocked Sofia missing from footer"
    print("Arte/Sofia shown in 'seen & dropped' footer [OK]")

    # Baseline comparison + child caveat present.
    assert "30% under" in html, f"Antalya baseline comparison wrong: {html[html.find('Antalya'):html.find('Antalya')+300]}"
    assert "reconfirm the 4-year-old" in html, "child-price caveat missing"
    print("Baseline comparison + child caveat present [OK]")

    # Memory: scores recorded, verdicts reflect tiers, no over_ceiling.
    mem = json.load(open("state/memory.json", encoding="utf-8"))
    led = {e["destination"]: e for e in mem["ledger"]}
    assert led["Kempinski Hotel Grand Arena, Bansko, Bulgaria"]["verdict"] == "diamond"
    assert led["Regnum Bansko, Bulgaria"]["verdict"] == "skip", led["Regnum Bansko, Bulgaria"]
    assert led["Regnum Bansko, Bulgaria"]["final_score"] == 63, led["Regnum Bansko, Bulgaria"]
    assert led["Arte Spa & Park, Velingrad, Bulgaria"]["verdict"] == "kill"
    assert led["Sofia City Break, Bulgaria"]["verdict"] == "blocked"
    assert all(e.get("verdict") != "over_ceiling" for e in mem["ledger"]), "over_ceiling should be gone"
    assert led["Kempinski Hotel Grand Arena, Bansko, Bulgaria"]["llm_score"] == 90
    print("Memory ledger: tiers + scores recorded, no over_ceiling [OK]")

    # city_signals.json carries the full score breakdown.
    sig = {s["city"]: s for s in json.load(open("state/city_signals.json", encoding="utf-8"))["signals"]}
    assert sig["Regnum Bansko, Bulgaria"]["price_adj"] == -20, sig["Regnum Bansko, Bulgaria"]
    assert sig["Kempinski Hotel Grand Arena, Bansko, Bulgaria"]["tier"] == "diamond"
    print("city_signals.json: score breakdown present [OK]")

    md = open("state/city_signals.md", encoding="utf-8").read()
    assert "💎" in md and "final" in md.lower() and "over ceiling" not in md.lower()
    print("city_signals.md: scores shown, no ceiling language [OK]")

    print("\nAll assertions passed.")
finally:
    os.chdir(_cwd)
    shutil.rmtree(sandbox, ignore_errors=True)
