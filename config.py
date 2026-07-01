"""Configuration for the diamond-finder pipeline. Edit freely."""

import os

# ── Scraper framework (scrapers.py) ──────────────────────────────────────────
# NOTE: this repo is mid-transformation from deal-hunter into the Weekend Concierge
# (see PLAN.md/CLAUDE.md) — most of this file is still deal-hunter leftovers. These
# two knobs are the only pieces scrapers.py needs; the rest of config.py gets rewritten
# for the concierge pipeline in a later step.

# Every source scrapers.py knows how to run (keys into scrapers.SCRAPERS for the
# structured tier, scrapers.RAW_FETCH_SOURCES for the raw-fetch tier). "facebook" is
# a documented stub — left disabled until auth/anti-bot is worth solving.
ENABLED_SOURCES = [
    "plovdiv2019", "bilet",
    "eventim", "ticketstation", "ticketbg", "dtp", "rnhm", "oldplovdiv",
    "programata", "starazagora_tourist", "plovdiv_bg", "visitplovdiv", "marica",
]

# Volume cap applied to the deduped harvest before it's handed to FIND.
MAX_HARVEST_ITEMS = 200

# ── City list ───────────────────────────────────────────────────────────────
# Cities the diamond finder uses as a search anchor. The LLM receives these as
# the preferred destinations but can extend to nearby or thematically related
# places when a confirmed opportunity exists (e.g. a cruise from a regional port).
#
# Values are (min_nights, max_nights). The diamond finder uses only the city
# names (CITIES.keys()); the night-range values are used by _pick_weekend_block
# in providers.py to respect per-city minimum stay requirements.
CITIES = {
    # --- Bulgaria, by car (<3h) ---
    "Asenovgrad, Bulgaria": (1, 3),   "Banya, Bulgaria": (2, 4),
    "Bansko, Bulgaria": (2, 7),       "Burgas, Bulgaria": (2, 7),
    "Chiflik, Bulgaria": (2, 4),      "Hisarya, Bulgaria": (2, 4),
    "Koprivshtitsa, Bulgaria": (1, 3), "Nessebar, Bulgaria": (2, 7),
    "Pazardzhik, Bulgaria": (1, 3),   "Pamporovo, Bulgaria": (2, 7),
    "Smolyan, Bulgaria": (2, 5),      "Sofia, Bulgaria": (1, 4),
    "Sozopol, Bulgaria": (2, 7),      "Stara Zagora, Bulgaria": (1, 3),
    "Veliko Tarnovo, Bulgaria": (2, 4),
    # --- Greece, by car ---
    "Alexandroupoli, Greece": (2, 5), "Kavala, Greece": (2, 5),
    "Komotini, Greece": (2, 4),       "Xanthi, Greece": (2, 4),
    # --- Turkey ---
    "Edirne, Turkey": (2, 3),         "Istanbul, Turkey": (3, 6),
    # --- by low-cost flight (min >= 2: a 1-night trip isn't worth the flight) ---
    "Bari, Italy": (3, 7),    "Milan, Italy": (2, 7),   "Naples, Italy": (3, 7),
    "Rome, Italy": (3, 7),    "Bratislava, Slovakia": (2, 7), "Vienna, Austria": (2, 7),
    "Athens, Greece": (3, 7), "Budapest, Hungary": (2, 7),   "Krakow, Poland": (3, 7),
    "Belgrade, Serbia": (2, 5), "London, United Kingdom": (3, 7),
    "Birmingham, United Kingdom": (3, 7), "Manchester, United Kingdom": (3, 7),
}

# Cities grouped by transit tier — used to build the structured city block in
# FIND_PROMPT. Keeps country labels out of the individual city names so the LLM
# receives a clean, hierarchy-aware list instead of a flat comma-separated mess.
CITY_TIER_GROUPS = [
    ("Tier 1 — Drive ≤3h from Plovdiv or Direct PDV Flight", [
        ("Bulgaria", [
            "Asenovgrad", "Chepelare", "Velingrad", "Bansko", "Pamporovo", "Borovets", "Hisarya", 
            "Banya", "Burgas", "Sozopol", "Nessebar"
        ]),
        ("Greece (Drive)", [
            "Kavala", "Alexandroupoli", "Thassos"
        ]),
        ("Turkey (Drive)", [
            "Edirne", "Kirklareli"
        ]),
        ("Direct Flights from PDV", [
            "London", "Birmingham", "Manchester", "Milan", "Bratislava"
        ])
    ]),
    ("Tier 2 — Low-cost/Direct Flight from SOF", [
        ("Turkey", ["Istanbul", "Antalya", "Bodrum"]),
        ("Italy", ["Rome", "Bari", "Naples", "Bologna", "Venice"]),
        ("Greece", ["Athens", "Thessaloniki", "Chania", "Corfu"]),
        ("Central Europe", ["Vienna", "Budapest", "Prague", "Bratislava"]),
        ("Spain", ["Barcelona", "Madrid", "Valencia", "Malaga"]),
        ("Cyprus", ["Larnaca", "Paphos"]),
        ("Malta", ["Valetta"]),
        ("Germany", ["Munich", "Frankfurt", "Memmingen", "Berlin"]),
        ("France", ["Paris", "Nice"]),
        ("United Kingdom", ["London", "Bristol", "Edinburgh"])
    ]),
]


def cities_prompt_text():
    """Return cities formatted as a structured tier block for the find prompt."""
    lines = []
    for tier, regions in CITY_TIER_GROUPS:
        lines.append(f"{tier}:")
        for region, cities in regions:
            lines.append(f"  {region}: {', '.join(cities)}")
    return "\n".join(lines)


# ── LLM models ──────────────────────────────────────────────────────────────
# Per-stage model roles. Values are canonical Anthropic model names; Gemini
# equivalents are looked up in GEMINI_MODEL_MAP below.
MODEL_FIND    = "claude-sonnet-4-6"  # Stage 1: fast + web-search capable
MODEL_SKEPTIC = "claude-sonnet-4-6"          # Stage 2: stronger reasoning
MODEL_VERIFY  = "claude-sonnet-4-6"          # Stage 3: strong + search-capable

# Maps Anthropic model names (canonical keys) to Gemini equivalents.
# Used when LLM_PROVIDER=gemini. Add a new entry here whenever a new model role
# is added; never hard-code Gemini model names anywhere else.
#
# On Gemini, search and reasoning are split across THREE models (see common._gemini):
#   1. GEMINI_SEARCH_MODEL below — does the live google_search grounding only.
#   2. gemini-flash-latest        — Stage 1 Find: parses grounding, scores candidates.
#   3. gemini-pro-latest          — Stage 2/3 Skeptic + Verify: filters and verifies.
# Only model #1 ever carries the google_search tool; #2 and #3 run tools-free.
GEMINI_MODEL_MAP = {
    "claude-haiku-4-5-20251001": "gemini-flash-latest",   # Stage 1 Find reasoning
    "claude-sonnet-4-6":         "gemini-pro-latest",      # Stage 2/3 Skeptic + Verify reasoning
}

# Model that performs the live web-search grounding (google_search tool).
# Flagship models (flash-latest / pro-latest) time out ~99% of the time when
# google_search is attached — Google's grounding gateway is capacity-starved for
# them. The lite tier survives it reliably. Change this freely; it is the only
# place the search model is named.
GEMINI_SEARCH_MODEL = "gemini-3.1-flash-lite"

# Optional per-stage provider overrides. None = use the global LLM_PROVIDER env var.
# Set to "anthropic" or "gemini" to run a specific stage on a different provider.
PROVIDER_FIND    = None
PROVIDER_SKEPTIC = None
PROVIDER_VERIFY  = None

# ── LLM token budgets ────────────────────────────────────────────────────────
# IMPORTANT (Gemini thinking models): maxOutputTokens caps thinking tokens AND the
# visible answer combined. A heavy reasoning pass can burn several thousand hidden
# thinking tokens, and if the budget runs out mid-answer the JSON is truncated
# (finishReason=MAX_TOKENS) — which parses to nothing and looks like a quiet day.
# common._gemini now warns on that, but these budgets are set with generous headroom
# above observed thinking usage (~3-4k) so it shouldn't happen in practice.

# Stage 1 (find): most output-heavy — multiple full candidate objects with long
# reason fields, on top of the thinking pass over the grounded leads.
MAX_TOKENS_FIND    = 16000

# Stage 2 (skeptic): one verdict line per candidate, but the flagship still thinks
# hard before writing. Headroom for a large input batch + thinking.
MAX_TOKENS_SKEPTIC = 12000

# Stage 3 (verify): structured grounding output + thinking.
MAX_TOKENS_VERIFY = 12000

# ── Web search ───────────────────────────────────────────────────────────────
# Maximum number of individual web-search tool uses allowed in a single Stage 1
# call (Anthropic provider only; Gemini's google_search has no per-call cap).
# Higher values improve grounding quality but increase latency and token cost.
WEB_SEARCH_MAX_USES = 6

# ── Diamond finder gate ──────────────────────────────────────────────────────
# Minimum score (0–100) a Stage 1 candidate must reach to be forwarded to the
# skeptic. Candidates below this threshold still appear in city_signals.md but
# never trigger email. Raise this to make email rarer; lower with caution.
STAGE1_MIN_SCORE = 80

# Maximum number of diamonds included in the single email sent per run. Because
# surviving Stage 2 is rare, this cap is almost never reached in practice.
MAX_EMAILS_PER_RUN = 3

# Days before the same destination+window pair can trigger another email.
# Prevents daily spam about a deal that persists for weeks, while still resurfacing a
# still-live deal after a fortnight so a good window isn't forgotten.
SIGNAL_TTL_DAYS = 14

# ── Deterministic scoring model ──────────────────────────────────────────────
# The final tier is NOT decided by the LLM. The skeptic (Stage 3) returns a 0-100
# desirability score for each grounded candidate, judging quality/utility/fit and
# deliberately IGNORING the raw nightly price. The pipeline then applies deterministic
# modifiers — a price adjustment against the regional "par" price, and a small
# drive-vs-fly nudge — to produce the final score, from which the tier is derived.
# This keeps price handling precise and consistent (not left to LLM whim) while
# preserving every candidate's score in memory and the run log for tuning.
#
#   final = clamp(0, 100, llm_score + price_adj + transit_adj)
#   price_adj = min(PRICE_BONUS_CAP, PRICE_SCORE_WEIGHT * (1 - grounded_ppn / par))
#     → below par: bonus (capped, so a cheap dump can't auto-diamond a weak place);
#     → above par: penalty (UNCAPPED, so an overpriced deal sinks to skip on its own —
#       this is why no hard price ceiling is needed).
#   transit_adj = +TRANSIT_TIER1_BONUS (drivable Tier-1) | TRANSIT_TIER2_BONUS (fly Tier-2)

# Per-night "par" price: the point where the price modifier is neutral. Below it earns a
# bonus, above it a penalty. It is a reference, NOT a wall — a standout property can still
# be a diamond above par if its desirability score is high enough. Tune freely.
DIAMOND_PAR_EUR = {"Bulgaria": 80, "Turkey": 85}
DEFAULT_DIAMOND_PAR_EUR = 110  # rest of Europe

PRICE_SCORE_WEIGHT = 50   # strength of the price modifier
PRICE_BONUS_CAP    = 15   # max score bonus for a below-par price (penalty is uncapped)
TRANSIT_TIER1_BONUS = 3   # drivable / direct-PDV destinations
TRANSIT_TIER2_BONUS = -3  # fly-from-SOF or long-drive destinations

# Final-score tier thresholds. (Illustrative "> 80 = diamond" from tuning talk lives here —
# raise/lower freely.)
DIAMOND_SCORE_THRESHOLD = 85
GOOD_SCORE_THRESHOLD    = 68


def get_diamond_par(destination):
    """Return the per-night 'par' price (EUR) for a destination — the neutral point of the
    price modifier. Substring-matches country names; falls back to the default."""
    dest_lower = (destination or "").lower()
    for country, par in DIAMOND_PAR_EUR.items():
        if country.lower() in dest_lower:
            return par
    return DEFAULT_DIAMOND_PAR_EUR


def _tier1_city_tokens():
    """Lowercased city names in the Tier-1 (drivable / direct-PDV) transit group."""
    tokens = set()
    for _region, cities in CITY_TIER_GROUPS[0][1]:
        for c in cities:
            tokens.add(c.lower())
    return tokens


_TIER1_TOKENS = _tier1_city_tokens()


def transit_tier(destination):
    """1 if the destination is a Tier-1 (drivable / direct-PDV) place, else 2.
    Unknown/extended destinations default to 2 (assume a flight is involved)."""
    d = (destination or "").lower()
    return 1 if any(tok in d for tok in _TIER1_TOKENS) else 2


def compute_final_score(llm_score, grounded_ppn, destination):
    """Apply the deterministic modifiers to an LLM desirability score.
    Returns (final_score, price_adj, transit_adj) — all rounded ints."""
    base = llm_score if isinstance(llm_score, (int, float)) else 0
    par = get_diamond_par(destination)
    if grounded_ppn and par:
        raw = PRICE_SCORE_WEIGHT * (1 - grounded_ppn / par)
        price_adj = min(PRICE_BONUS_CAP, raw)   # bonus capped, penalty uncapped
    else:
        price_adj = 0
    transit_adj = TRANSIT_TIER1_BONUS if transit_tier(destination) == 1 else TRANSIT_TIER2_BONUS
    final = max(0, min(100, base + price_adj + transit_adj))
    return round(final), round(price_adj), transit_adj


def tier_for_score(final_score):
    """Map a final score to a tier: 'diamond' | 'good' | 'skip'."""
    if final_score >= DIAMOND_SCORE_THRESHOLD:
        return "diamond"
    if final_score >= GOOD_SCORE_THRESHOLD:
        return "good"
    return "skip"


# ── Hotel grounding ─────────────────────────────────────────────────────────
HOTEL_PROVIDER        = os.environ.get("HOTEL_PROVIDER", "apidojo").strip().lower()
RAPIDAPI_KEY          = os.environ.get("RAPIDAPI_KEY", "")
BOOKING_RAPIDAPI_HOST = os.environ.get("BOOKING_RAPIDAPI_HOST", "apidojo-booking-v1.p.rapidapi.com")
BOOKING_BASE_URL      = os.environ.get("BOOKING_BASE_URL", f"https://{BOOKING_RAPIDAPI_HOST}")
HOTEL_ADULTS        = 2
HOTEL_ROOMS         = 1
HOTEL_CHILDREN_AGES = [4]
HOTEL_CURRENCY      = "EUR"
HOTEL_HTTP_TIMEOUT  = 20
HOTEL_MAPPING = {
    # Optional override for known/ambiguous destinations (skips /locations/auto-complete):
    # "kempinski grand arena": {"dest_id": "-835297", "search_type": "city", "name": "Kempinski Hotel Grand Arena Bansko"},
}


# ── LLM prompts ─────────────────────────────────────────────────────────────
# Placeholders filled at runtime by find_city_anomalies.py / common.py:
#   SEARCH_PROMPT          → {today}, {cities}   (Gemini search step — lead generation)
#   SEARCH_RESULTS_PREAMBLE→ {leads}             (Gemini reasoning step — injected ahead of FIND/VERIFY)
#   FIND_PROMPT            → {today}, {cities}, {memory}, {search_directive}
#   SKEPTIC_PROMPT         → {today}, {min_score}, {candidates}, {memory}
#   VERIFY_PROMPT          → {today}, {candidate}, {memory}
# Use {{...}} for literal braces in the JSON schema examples (Python .format() escaping).

# ── Gemini search/reasoning split (see common._gemini) ───────────────────────
# On Gemini, want_search calls run in two steps. SEARCH_PROMPT drives step 1 (lead
# generation on the lite model with google_search); SEARCH_RESULTS_PREAMBLE frames
# step 1's output for step 2 (the flagship reasoner, which has no live search tool).
# These are Gemini-only. On Anthropic the flagship searches inline via FIND_PROMPT.

# Step 1 — optimize for FRESH, VARIED LEADS, not accuracy. Better 99 useless leads and
# 1 gem than the same five evergreen hotels every day. The downstream skeptic + live
# price grounding cut hard, so cast a wide net here and prize novelty over caution.
SEARCH_PROMPT = """Today is {today}. You are a sharp travel scout running live web searches for a family of 3 (2 adults + a 4-year-old) based in Plovdiv, Bulgaria. Our home currency is EUR.

Your ONLY job in this step is to surface FRESH, SPECIFIC LEADS for UPCOMING travel from the live web — raw material for an analyst who works downstream. You are NOT deciding what is a good deal, and you are NOT writing the final answer. You are casting a wide net for timely opportunities that nobody could guess from general knowledge alone.

### WHAT MAKES A GOOD LEAD
- It is happening NOW or was announced recently, for travel STILL AHEAD of us: a flash sale, a fresh price drop, a new hotel opening or reopening, an unsold last-minute allocation, an error/sale fare, a newly launched route, a festival or event creating an off-peak trough. Ignore sales and dates that have already passed.
- It is SPECIFIC: a named hotel / resort / cruise / airline, a concrete price, and concrete dates that fall in the future relative to {today}.
- It is hard to know WITHOUT searching today. We do NOT need evergreen facts ("Bansko is cheap off-season", "Antalya has all-inclusive resorts") — the analyst already knows those. Surprise us with something live.
- Variety beats repetition. Spread leads across different destinations, categories, and seasons rather than five versions of the same hotel. Assume yesterday you already reported the obvious ones; find different ones today.

### WHO IT'S FOR (so leads stay relevant — note fit, but do NOT filter hard here)
- A family with a 4-year-old: comfort, manageable logistics, child-friendly amenities.
- Reachable from Plovdiv: drive <= 3h, or fly from Plovdiv (PDV) or Sofia (SOF).

### WHERE TO LOOK (sweep across all of these)
- Premium off-season troughs: 4-5 star family resorts dropping hard between seasons with indoor/kids facilities still open.
- Flight error fares / flash sales from PDV or SOF.
- Last-minute package dumps: unsold flight+hotel bundles.
- Regional cruises departing Istanbul, Athens (Piraeus), or Thessaloniki.
- New openings, reopenings, or launch promotions.
- Event- or shoulder-season windows where prices trough.

Anchor destinations (a starting point, NOT a cage — chase a great lead anywhere reachable):
{cities}

### DOs AND DON'Ts
- DO report, for each lead: destination + named property, the price and exact dates, the live hook (why it is timely right now), and the source domain — verbatim.
- DO surface deals priced in LOCAL currency too (BGN, TRY, RSD…), not only EUR — Turkish-lira and Bulgarian-lev listings often hide the deepest regional value. Report the original price AND a rough EUR equivalent, so the tool-less downstream analyst doesn't have to guess an exchange rate.
- DO surface 8-15 distinct leads. Quantity and variety matter at this step; the analyst will cut ruthlessly later.
- DO include a lead even if you are unsure it is a great deal. Leads, not verdicts.
- DON'T return generic seasonal advice or a destination with no specific live hook.
- DON'T add any introduction or closing remarks. Start directly with the first lead.
- DON'T score, rank, analyse, or output JSON or markdown tables. Just a clean, scannable bulleted list of findings, one lead per block in exactly this shape:

* **Destination / Property:** <name>
* **Dates:** <specific future dates>
* **Price:** <original price> (~€<EUR estimate>)
* **Hook:** <why it is timely today>
* **Source:** <domain>"""

# Step 2 — frames step 1's leads for the flagship reasoner. The leads are a fresh
# SEED, not a fence: the reasoner must also draw on its own knowledge, and must not
# fall back to an empty answer just because the leads are thin. Applied via .replace
# (not .format) so leads containing braces can't break it.
SEARCH_RESULTS_PREAMBLE = """### LIVE SEARCH RESULTS (a web search was run for you moments ago)
A separate scout already ran live web searches on your behalf and gathered the leads below. You do NOT have a live search tool in this step, so wherever the task text says "search the web" or "use the web search tool", read it as: draw on these leads plus your own knowledge.

Treat these leads as a valuable fresh signal from the live internet — data you would not otherwise have. Fold the interesting ones into your reasoning. But they are a SEED, not a boundary: you are NOT limited to them. Also reason from your own knowledge of seasonal patterns, regional pricing, and arbitrage to propose strong candidates of your own. If the leads are thin or empty, do NOT give up and do NOT return an empty answer for that reason — reason your best from what you know. Every price is verified against live hotel data downstream, so put forward your best-reasoned candidates with honest est_price_eur estimates.

LEADS:
{leads}

--- END OF LIVE SEARCH RESULTS ---

Now complete the task below, using these leads as fresh input alongside your own reasoning:

"""

# Filled into FIND_PROMPT's {search_directive} per provider (find_city_anomalies.py).
# The Anthropic Find model has a live web_search tool, so it gets a forceful directive
# to use it. On Gemini the Find model has NO tool — SEARCH_RESULTS_PREAMBLE owns its
# framing — so {search_directive} is left empty there. This keeps FIND_PROMPT free of
# instructions that are false for whichever model is actually running it.
SEARCH_DIRECTIVE_ANTHROPIC = """- YOU HAVE A LIVE WEB SEARCH TOOL — USE IT. Ground every candidate in real, currently-available offers found on the live web within the last 48 hours.
- Don't surface a destination you have no live signal for. (Estimating the est_price_eur figure itself is fine — inventing a deal that isn't there is not.)"""

FIND_PROMPT = """Today is {today}. You are a pragmatic, data-driven Travel Arbitrage Analyst. Your job is to find 3-5 concrete, actionable travel opportunities for a family of 3 (2 adults, 1 child aged 4) based in Plovdiv, Bulgaria.

Your objective is to find high-utility value plays where a premium experience or location drops dramatically in price while maintaining high utility and comfort for a 4-year-old.

---

### PRIOR CORRECTIONS (from past pipeline runs — treat as ground truth; do not repeat past hallucinations)
{memory}

---

### SCORING CALIBRATION (Threshold: >= 80 Triggers an Email Alert)

Your scoring dictates the pipeline routing. A score of 80 or above means the deal is so strong it warrants immediately emailing the user. Be conservative.

- **Score 90-100 (Absolute No-Brainer):** Rare, highly actionable, massive price-to-utility disconnect. Zero logistical flaws.
- **Score 80-89 (High-Value Play):** Clearly above-average value with solid live evidence. Either a low-friction local play (Tier 1) with an excellent discount, or a higher-friction play (Tier 2) with a discount so steep it completely offsets the transit hassle.
- **Score 60-79 (Log Only - No Email):** Interesting low-season or standard budget pricing, but transit friction or utility loss doesn't justify interrupting the user.
- **Score Below 60:** Weak deals or unverified data included purely for logging purposes.

---

### GEOGRAPHIC & LOGISTICAL HIERARCHY

1. Tier 1: Local High Utility (Drive <= 3 hours from Plovdiv OR flights from Plovdiv Airport [PDV])
   - Baseline: Low transit friction for a 4-year-old. 
   - Evaluation: If an elite domestic spa resort (e.g., Velingrad, Bansko) or regional destination drops to entry-level pricing while keeping family infrastructure fully open, score it **80-95**.

2. Tier 2: High-Friction Transit (Flights from Sofia Airport [SOF] OR Drives > 3 hours)
   - Baseline: High transit friction for a 4-year-old. 
   - Evaluation: Standard discounts or normal cheap flights from SOF must be scored **below 80**. To cross the **80+ email threshold**, a Sofia transit option must either offer a staggering price drop on a premium experience (e.g., a 5-star Antalya resort collapsing to €100/night with active indoor kid facilities), OR be a high-excitement destination at strong absolute value (see DESTINATION EXCITEMENT below).

Target destinations, grouped by transit tier (aligns with the scoring hierarchy above):
{cities}

---

### DESTINATION EXCITEMENT & OPTIMAL STAY LENGTH
A deal's value is not just the nightly price — it is whether the *stay length* fits the destination. Match the recommended `window` to how much a family with a 4-year-old can genuinely enjoy there before boredom sets in:
- **High-excitement destinations** (vibrant cities and standout beach/island spots — e.g. Paris, Rome, Istanbul, Vienna, Barcelona, Athens, Malta, the Greek islands): a long stay (5-7+ nights) is itself part of the value. A week here at a great price is a top-tier diamond — score it high and recommend the longer window.
- **Low-excitement destinations** (quiet local spa/mountain towns — e.g. Bansko, Pamporovo, Velingrad, Hisarya, Sandanski): the magic is a SHORT, punchy break (2-3 nights). These exhaust their appeal fast for an active family. A 7-night stay here is NOT a diamond no matter how cheap the nightly rate — recommend a 2-3 night window and do not inflate the score for a long stay.
- When you set `window`, pick the length that is genuinely optimal for that destination, not the longest the price allows. Use your own judgement on where a destination falls; the examples above are anchors, not an exhaustive list.
- **Excitement can substitute for discount depth — high-excitement destinations ONLY.** A vibrant city or standout island/beach spot can clear the 80+ email bar on STRONG ABSOLUTE VALUE alone — a genuinely good price for that special place — even without a steep drop from a peak. The test: would a savvy traveller say *"that's a great price for Rome / Malta / the islands — grab it"*? If yes, and the logistics are manageable for a 4-year-old, score it 80+. A merely average price for an exciting place is NOT enough — it must be a clear win. Low-excitement towns get no such pass: they still need BOTH a steep, real discount AND a short 2-3 night window to reach 80+.

---

### HUNTING CATEGORIES
Hunt for opportunities across these specific categories:
- Premium Off-Season Troughs: 4 to 5-star family resorts with massive predictable price drops post-holidays or between seasons where indoor/kids infrastructure remains fully open.
- Regional Cruises: Family-friendly itineraries departing from Istanbul, Athens (Piraeus), or Thessaloniki.
- Flight Error/Sale Fares: Confirmed active low fares from SOF or PDV.
- Package Dumps: Last-minute unsold flight+hotel bundles.

---

### SEARCH & VERIFICATION RULES
{search_directive}
- Never fabricate hotel names or flight availability. When you are estimating a price rather than citing one you found, put your best figure in est_price_eur — the downstream stages verify every price against live data.
- Prices may be quoted in local currency: convert to EUR for est_price_eur. BGN is pegged at ~1.96 to the EUR; TRY and RSD float, so use a sensible current rate (this is only an estimate — Stage 3 grounds the real figure).
- Check child-safety/amenities: Ensure any off-season resort has an operating indoor heated pool, kids' area, or relevant infrastructure active *during* the specified travel window.

---

### OUTPUT FORMAT
Return JSON only. Do not include markdown formatting or wrappers like ```json. Output a single JSON object matching the schema below.

Field notes:
- est_price_eur: your best estimate of the typical per-night price in EUR for this deal at this window — a single number (not a range, not a string). A rough estimate is fine; live prices are verified downstream.
- type: exactly one of "hotel", "cruise", "flight", or "package". (Downstream logic keys on "hotel".)
- hotel_name: the specific hotel or resort property (e.g., "Kempinski Hotel Grand Arena"). Use "" for city-level, cruise, or flight deals with no single named property.
- city: the city where the deal is located (e.g., "Bansko"). Required.
- country: the country (e.g., "Bulgaria"). Required.

JSON Schema:
{{
  "candidates": [
    {{
      "destination": "City, resort name, or cruise line/route",
      "hotel_name": "Specific property name, or empty string for city-level/cruise/flight deals",
      "city": "Bansko",
      "country": "Bulgaria",
      "score": 82,
      "type": "hotel",
      "window": "Specific exact dates or tight window (e.g., Jan 10-17)",
      "est_price_eur": 79,
      "reason": "Cite live prices found via search. Quantify the utility vs. price play (e.g., peak price vs current live price). State why it fits a 4-year-old.",
      "confidence": "high"
    }}
  ]
}}

If no verifiable deals meet these criteria today, return `{{"candidates": []}}`."""


SKEPTIC_PROMPT = """You are a pragmatic, high-utility Travel Value Analyst scoring travel finds for a young family. The prices you see have ALREADY been verified against live Booking.com data — this is reality, not a salesman's estimate.

Today is {today}.
Target Demographics & Logistics:
- Party: Family of 3 (2 adults, 1 child aged 4).
- Location Base: Plovdiv, Bulgaria.
- Transit Limits: Departure strictly from Plovdiv Airport (PDV), Sofia Airport (SOF), or a reasonable drive from Plovdiv.
- Currency: EUR.

### HOW YOUR SCORE IS USED (read carefully — it changes how you should score)
You return a single desirability `score` from 0-100 for each candidate. You do NOT decide keep/kill or any tier. Here is exactly what the pipeline does with your score:

  final_score = your_score + price_adjustment + transit_adjustment
  → final_score >= {diamond_threshold}  becomes a DIAMOND (emailed, "grab it")
  → final_score >= {good_threshold}  becomes a GOOD find (emailed, "worth knowing")
  → below that  is dropped (logged only)

- `price_adjustment` is applied deterministically by the pipeline - it rewards a
  nightly HOTEL price below the region's par and penalises one above it. Your score may still reflect
  a SEVERE price discrepancy (a rate dramatically below or above the regional norm) but keep in mind the 
  pipeline already does it, so try to avoid double-counting. Treat the price as an input to your reasoning, not a scoring lever.
- `transit_adjustment` gives a small automatic nudge for drive-vs-fly. You should still reflect
  SEVERE logistics (open-jaw itineraries, >4h drive with a 4-year-old vs nice flight out of PDV airport or a <3h drive) in your score,
  but don't double-count ordinary "it's a flight away".

### WHAT YOUR SCORE MEASURES: VALUE DELIVERED, NOT LUXURY OR PRESTIGE
Your score is the VALUE this delivers to THIS family — the numerator of value-for-money. The
pipeline divides by the nightly hotel price, so you must NOT reward a find for being expensive,
famous, or prestigious. A modest local place that delivers a genuinely great, low-friction
family break can — and often SHOULD — score higher than a glamorous far-flung one. A €150
weekend in a nearby spa town can be far better value than a €400 weekend in Rome, but a €150 weekend
in Rome is better than a €150 weekend in a nearby spa town. Score the ultimate
value delivered for the price, not the postcard.  

A high score means high NET family utility:
- How much genuine enjoyment/benefit does a family with a 4-year-old actually get here? (A
  4-year-old does not care about world-class museums — open pools, space, ease and fun do.)
- PLUS how luxurious or comfortable the stay is for the adults (quiet, spacious, good food, spa, etc.).
- MINUS logistics friction — a large deduction, not a footnote. A destination with no direct
  Plovdiv flight means flying via Sofia with a toddler: transfers, lost travel days, and
  FLIGHT COST that the hotel price does NOT include. Flights are out of scope for the pipeline,
  so YOU are the only stage that can weigh their burden — do it heavily.
- MINUS in-window utility loss (closed pools, dead resort town — see deductions).
"Attraction"/excitement is ONE modest input for a 4-year-old, never the main factor.

### WHAT DRIVES A HIGH SCORE (85-100)
- High net family value delivered with LOW friction: the family genuinely enjoys it, getting
  there is easy (ideally a short drive or direct PDV flight), and the amenities that make it
  worthwhile are open in-window.
- Real, in-window utility: indoor/heated pools and kids' facilities actually OPEN during these
  dates; comfortable and fun for a 4-year-old.
- The stay length fits (a short 1-2 night break for a quiet town; longer only where there is
  genuinely a week of family value to extract).
- A far-flight destination reaches this band ONLY if the family value is high enough to survive
  the flight cost and hassle — otherwise it belongs lower.

### WHAT DRIVES A LOW SCORE (net-value deductions — NOT the nightly hotel price)
1. Low-Season Utility Trap: amenities that make the place worth it are CLOSED in-window (outdoor-only pools in winter, shut kids' clubs, a dead resort town).
2. Toddler Tax / Logistics: >4h door-to-door, flying via Sofia with a 4-year-old, open-jaw or multi-leg itineraries, plus flight cost/hassle the hotel price does not cover. Weigh heavily.
3. Prestige-without-value: a famous/luxury/exciting place whose real in-window benefit to a family with a 4-year-old is modest, or is eaten by travel friction. Do NOT score it high just because the destination is desirable in the abstract.
4. Stay-Length Mismatch: a long stay (5+ nights) in a low-excitement local town (Bansko, Pamporovo, Velingrad, Hisarya, Sandanski) where an active family runs out of things to do — score low unless the window is already a short 2-4 night break.

### CALIBRATION EXAMPLES (score = net family VALUE delivered; hold nightly hotel price neutral)
- Nearby 4-star spa, easy <2h drive, pools + kids' area open, relaxed 3-night family weekend: **~82**. Modest place, but high net value — real family enjoyment, zero friction. (Low live price → pipeline lifts to diamond; high → it sinks. That's the pipeline's job, not yours.)
- Antalya 5-star all-inclusive, mid-Jan, indoor pools + kids' club + dining all OPEN, one manageable flight: **~84**. High utility, but the flight burden keeps it off the very top.
- Rome or Athens city trip via Sofia, 4 nights: **~58**. The city is exciting, but a 4-year-old gets modest benefit and the trip burns travel days + flight costs the hotel price ignores — net value is middling, so it only becomes a pick if the hotel is genuinely cheap.
- Sunny Beach 4-star beachfront in OCTOBER, outdoor pools freezing, entertainment shut: **~30**. Utility collapses in-window regardless of how cheap it is.
- 7-night Athens→Ravenna open-jaw cruise: **~20**. Logistics nightmare for a 4-year-old; the itinerary itself is the problem.

---

Each input candidate carries LIVE grounded figures for context:
- `grounded_price_per_night_eur` / `grounded_total_eur` / `grounded_nights` / `grounded_dates` — the real, bookable HOTEL price. Hold it neutral in your score (the pipeline handles it), except for a severe discrepancy as noted above. Note it does NOT include flights.
- `diamond_par_eur` — the region's neutral price point, shown so you understand what the pipeline will reward/penalise. Not for you to apply.
- `grounding_summary` — the live verification note (often includes star rating and review score — DO use quality signals like stars/reviews).

Input Candidates (each has a numeric deal_id you must echo back):
{candidates}

---

### PRIOR OUTCOMES (from past runs — scores and notes for calibration)
{memory}

---

### OUTPUT FORMAT
Return JSON only. No markdown fences. The root of your response MUST be a bare JSON array (starting with `[`) — do NOT wrap it in an object like {{"results": [...]}}. One object per input candidate, in input order. Echo each candidate's `deal_id` back unchanged (the integer key matching your score to the deal) and copy `destination` verbatim as a fallback. Do not invent, renumber, or omit deal_ids.

JSON Schema:
[
  {{
    "deal_id": 1,
    "destination": "Exact string from input",
    "score": 72,
    "why": "One direct sentence on the NET FAMILY VALUE (utility minus friction) that justifies the score — not the nightly hotel price.",
    "red_flags": "Specific hidden cost or logistical risk to verify before booking."
  }}
]

Score honestly and independently on net family value — the pipeline turns your scores into the final verdict, and every score is recorded, so an accurate 72 is more useful than a nudged 85."""


VERIFY_PROMPT = """Today is {today}. You are a Personal Travel Concierge with live web-search access. One travel deal has survived a two-stage expert filter. Your job is to ground it in reality: find real prices at specific bookable dates, a real booking path, and produce an honest assistant-style summary.

Candidate deal to verify:
{candidate}

Prior price memory (corrections and baselines from past runs):
{memory}

---

### YOUR TASK

1. **Web-search the actual current price** for 1–3 SPECIFIC bookable sub-windows inside the deal's stated travel window — e.g., "Aug 8–10", "Aug 22–24" — not a month-wide minimum. Report price per night and total for each concrete window you check.
2. **Provide a booking path**: a direct URL if bookable online (booking.com, the property's own site, a tour operator); if not bookable online, explain how and where to book and cite the source that grounds the price (article, operator page, phone number).
3. **Critically re-check the claimed price.** If reality contradicts it (e.g., quoted at €72–95/night but Aug weekends are actually €186), set verdict=correct with the corrected figures. Do NOT kill a deal because the corrected price is higher or the deal looks "unexceptional" — a real, bookable price (however ordinary) is forwarded to a downstream scorer that decides value. Reserve kill for a deal that is NOT REAL.
4. Write an `assistant_summary` in personal-assistant tone (1–3 sentences): what you found, what specific dates, what price, and the booking path.

### STAY IN YOUR LANE — PRICE & BOOKABILITY, NOT DESIRABILITY
Your job is to verify the real price and that the deal is genuinely bookable. It is NOT to judge whether the deal is good value, exciting, or a fit for the family — a separate scorer does that with full context. If you notice a quality, seasonal, or amenity concern (a pool closed for maintenance, a dead resort town off-season, mediocre reviews), NOTE it plainly in `grounding`/`assistant_summary` so the scorer can weigh it — but still return confirm/correct with the real price. Do NOT turn such a concern into a kill.

### RULES
- Search for specific date windows — not a monthly minimum. A €72/night rate valid only for a single midweek Tuesday is not a family deal.
- If live search returned no useful price data (tool unavailable or search results empty), set confidence=low. Do NOT fabricate prices, dates, or booking URLs in that case.
- Never invent booking URLs. Include real URLs you actually found; if you did not find one, omit booking_url (or set it to null).
- If no live search was available (search tool absent or rejected), clearly flag this in assistant_summary and set confidence=low.

### OUTPUT
Return a single JSON object only. No markdown fences, no extra commentary outside the JSON.

{{
  "destination": "exact destination string from the candidate input",
  "verdict": "confirm|correct|kill",
  "options": [
    {{
      "dates": "Aug 8-10, 2026",
      "nights": 2,
      "price_per_night_eur": 79,
      "total_eur": 158,
      "booking_url": "https://www.booking.com/hotel/...  (omit or null if none found)",
      "source": "booking.com live search {today}"
    }}
  ],
  "how_to_book": "Book at the URL above. Alternatively call the property at +359 XX XXX XXXX.",
  "grounding": "What live evidence supports this: URLs searched, what was actually found, dates checked.",
  "assistant_summary": "I searched Regnum Bansko for Aug 8–10 and found Standard Rooms at €79/night (€158 total for 2 nights). Book directly at: https://...",
  "confidence": "high"
}}

verdict: confirm (deal is real and price holds as stated) | correct (deal is real but at a different price or dates than claimed — forward it) | kill (the property/deal is NOT REAL: it does not exist, has no availability in-window, or no supporting evidence was found. NOT for a high price or a quality/seasonal/amenity concern — those are the scorer's job.)
confidence: high (live search confirmed specific price and dates) | medium (indirect evidence, e.g. rate cards or press) | low (no live data available — do not fabricate)"""


# ── Response schemas (Gemini response_format) ────────────────────────────────
# Passed to _gemini() via llm(response_schema=...) to constrain output to valid
# JSON. The Anthropic path ignores these — prompt engineering suffices there.
# Keep in sync with the JSON schemas in FIND_PROMPT / SKEPTIC_PROMPT / VERIFY_PROMPT.

STAGE1_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "destination":   {"type": "string"},
                    "hotel_name":    {"type": "string"},
                    "city":          {"type": "string"},
                    "country":       {"type": "string"},
                    "score":         {"type": "integer"},
                    "type":          {"type": "string"},
                    "window":        {"type": "string"},
                    "est_price_eur": {"type": "number"},
                    "reason":        {"type": "string"},
                    "confidence":    {"type": "string"},
                },
                "required": ["destination", "city", "country", "score", "type", "window", "est_price_eur", "reason", "confidence"],
            },
        },
    },
    "required": ["candidates"],
}

STAGE2_RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "deal_id":     {"type": "integer"},
            "destination": {"type": "string"},
            "score":       {"type": "integer"},
            "why":         {"type": "string"},
            "red_flags":   {"type": "string"},
        },
        "required": ["deal_id", "destination", "score", "why", "red_flags"],
    },
}

STAGE3_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "destination":       {"type": "string"},
        "verdict":           {"type": "string", "enum": ["confirm", "correct", "kill"]},
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dates":               {"type": "string"},
                    "nights":              {"type": "integer"},
                    "price_per_night_eur": {"type": "number"},
                    "total_eur":           {"type": "number"},
                    "booking_url":         {"type": "string"},
                    "source":              {"type": "string"},
                },
                "required": ["dates", "nights", "price_per_night_eur", "total_eur", "source"],
            },
        },
        "how_to_book":       {"type": "string"},
        "grounding":         {"type": "string"},
        "assistant_summary": {"type": "string"},
        "confidence":        {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["destination", "verdict", "options", "how_to_book", "grounding", "assistant_summary", "confidence"],
}
