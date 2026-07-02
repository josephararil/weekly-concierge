"""Configuration for the Weekend Concierge pipeline. Edit freely."""

# ── Geo ───────────────────────────────────────────────────────────────────────
PLOVDIV_LATLON = (42.1354, 24.7453)
HOME_AREA      = "Plovdiv, Bulgaria"
RADIUS_MINUTES = 90  # max one-way travel time from Plovdiv worth suggesting

# ── Scraper framework (scrapers.py) ──────────────────────────────────────────
# Every source scrapers.py knows how to run (keys into scrapers.SCRAPERS for the
# structured tier, scrapers.RAW_FETCH_SOURCES for the raw-fetch tier). "facebook" is
# a documented stub — left disabled until auth/anti-bot is worth solving.
ENABLED_SOURCES = [
    "plovdiv2019", "bilet",
    "eventim", "ticketstation", "ticketbg", "dtp", "rnhm", "oldplovdiv",
    "programata", "starazagora_tourist", "plovdiv_bg", "visitplovdiv", "marica",
    "lostinplovdiv",
]

# Volume cap applied to the deduped harvest before it's handed to FIND.
MAX_HARVEST_ITEMS = 200

# ── LLM models ──────────────────────────────────────────────────────────────
# Per-stage model roles. Values are canonical Anthropic model names; Gemini
# equivalents are looked up in GEMINI_MODEL_MAP below.
MODEL_FIND      = "claude-haiku-4-5-20251001"  # Stage 1: fast + web-search capable, consolidates harvest+leads
MODEL_SKEPTIC   = "claude-sonnet-4-6"          # Stage 2: stronger reasoning, the hallucination guard
MODEL_CONCIERGE = "claude-sonnet-4-6"          # Stage 3: strong prose writer, no search

# Maps Anthropic model names (canonical keys) to Gemini equivalents.
# Used when LLM_PROVIDER=gemini. Add a new entry here whenever a new model role
# is added; never hard-code Gemini model names anywhere else.
#
# On Gemini, search and reasoning are split across THREE models (see common._gemini):
#   1. GEMINI_SEARCH_MODEL below — does the live google_search grounding only.
#   2. gemini-flash-latest        — Stage 1 Find: parses grounding, scores candidates.
#   3. gemini-pro-latest          — Stage 2/3 Skeptic + Concierge: verify and write.
# Only model #1 ever carries the google_search tool; #2 and #3 run tools-free.
GEMINI_MODEL_MAP = {
    "claude-haiku-4-5-20251001": "gemini-flash-latest",   # Stage 1 Find reasoning
    "claude-sonnet-4-6":         "gemini-pro-latest",      # Stage 2/3 Skeptic + Concierge reasoning
}

# Model that performs the live web-search grounding (google_search tool).
# Flagship models (flash-latest / pro-latest) time out ~99% of the time when
# google_search is attached — Google's grounding gateway is capacity-starved for
# them. The lite tier survives it reliably. Change this freely; it is the only
# place the search model is named.
GEMINI_SEARCH_MODEL = "gemini-3.1-flash-lite"

# Optional per-stage provider overrides. None = use the global LLM_PROVIDER env var.
# Set to "anthropic" or "gemini" to run a specific stage on a different provider.
PROVIDER_FIND      = None
PROVIDER_SKEPTIC   = None
PROVIDER_CONCIERGE = None

# ── LLM token budgets ────────────────────────────────────────────────────────
# IMPORTANT (Gemini thinking models): maxOutputTokens caps thinking tokens AND the
# visible answer combined. A heavy reasoning pass can burn several thousand hidden
# thinking tokens, and if the budget runs out mid-answer the JSON is truncated
# (finishReason=MAX_TOKENS) — which parses to nothing and looks like a quiet weekend.
# common._gemini warns on that, but these budgets are set with generous headroom
# above observed thinking usage (~3-4k) so it shouldn't happen in practice.

MAX_TOKENS_FIND      = 16000  # Stage 1: many candidate objects with reason fields + thinking
MAX_TOKENS_SKEPTIC   = 12000  # Stage 2: one verdict per candidate, but a large input batch
MAX_TOKENS_CONCIERGE = 12000  # Stage 3: full email prose (HTML + text) + thinking

# ── Web search ───────────────────────────────────────────────────────────────
# Maximum number of individual web-search tool uses allowed in a single stage
# call (Anthropic provider only; Gemini's google_search has no per-call cap).
WEB_SEARCH_MAX_USES = 6

# ── Coverage knobs ───────────────────────────────────────────────────────────
LOOKAHEAD_WEEKS      = 4    # how far ahead "notable events to plan for" reaches
MAX_EVENTS_PER_EMAIL = 6    # cap on non-evergreen items included in one email
EVERGREEN_PER_EMAIL  = 2    # rotating evergreen ideas included in one email
MIN_INCLUDE_SCORE    = 50   # family_fit floor (0-100) below which an event is dropped

# ── Anti-repeat knobs ────────────────────────────────────────────────────────
EVENT_TTL_DAYS          = 21  # cooldown before the same event can resurface
EVERGREEN_COOLDOWN_DAYS = 70  # cooldown before the same evergreen idea can resurface

# ── Evergreen seed catalog ───────────────────────────────────────────────────
# Merged into state/memory.json's evergreen catalog on first run (see memory.record_evergreen).
SEED_EVERGREEN = [
    {
        "name": "Stara Zagora Zoo",
        "location": "Stara Zagora",
        "area": "~50 min drive from Plovdiv",
        "description": "One of Bulgaria's larger zoos — a reliable half-day out for a 4-year-old.",
        "tags": ["animals", "outdoor"],
        "source": "seed",
    },
    {
        "name": "Plovdiv Regional Natural History Museum",
        "location": "Plovdiv",
        "area": "in town",
        "description": "Compact natural history museum with taxidermy and a small aquarium — easy indoor fallback.",
        "tags": ["museum", "indoor"],
        "source": "seed",
    },
    {
        "name": "Rowing Channel bike ride",
        "location": "Kanala (Rowing Channel), Plovdiv",
        "area": "in town",
        "description": "Flat paved paths alongside the water, bike/scooter rental on site — easy free outdoor outing.",
        "tags": ["outdoor", "free", "active"],
        "source": "seed",
    },
    {
        "name": "Ancient Theatre of Philippopolis",
        "location": "Plovdiv Old Town",
        "area": "in town",
        "description": "Roman-era amphitheatre in the Old Town; a short scenic walk even without an event on.",
        "tags": ["history", "outdoor", "free"],
        "source": "seed",
    },
    {
        "name": "Bachkovo Monastery",
        "location": "Bachkovo",
        "area": "~40 min drive from Plovdiv",
        "description": "Scenic mountain monastery with a river nearby for a picnic stop — a gentle half-day trip.",
        "tags": ["nature", "history", "outdoor"],
        "source": "seed",
    },
]

# ── LLM prompts ─────────────────────────────────────────────────────────────
# Placeholders filled at runtime by weekend_concierge.py / common.py:
#   SEARCH_PROMPT           → {today}, {home_area}, {radius_minutes}
#   SEARCH_RESULTS_PREAMBLE → {leads}             (Gemini reasoning step — injected ahead of FIND)
#   FIND_PROMPT             → {today}, {home_area}, {radius_minutes}, {lookahead_weeks},
#                              {harvest}, {memory}, {feedback}, {search_directive}
#   SKEPTIC_PROMPT          → {today}, {radius_minutes}, {candidates}, {memory}
#   CONCIERGE_PROMPT        → {today}, {candidates}, {weather}, {feedback}, {memory}
# Use {{...}} for literal braces in the JSON schema examples (Python .format() escaping).

# PROMPT SPECS (apply throughout): all inputs may be in Bulgarian (scraped pages, search
# results) — read them, but ALWAYS write output in English. Every candidate must be a
# realistic fit for a family of 3 (2 adults + a 4-year-old) and within a ~{radius_minutes}-
# minute travel radius of {home_area}. Don't propose or keep anything requiring arduous
# travel or unsuitable for a 4-year-old.

# ── Gemini search/reasoning split (see common._gemini) ───────────────────────
# On Gemini, want_search calls run in two steps. SEARCH_PROMPT drives step 1 (lead
# generation on the lite model with google_search); SEARCH_RESULTS_PREAMBLE frames
# step 1's output for step 2 (the flagship reasoner, which has no live search tool).
# These are Gemini-only. On Anthropic the flagship searches inline via FIND_PROMPT.

SEARCH_PROMPT = """Today is {today}. You are a local scout running live web searches to find weekend activities for a family of 3 (2 adults + a 4-year-old) based in {home_area}. The household is English-speaking and misses most local happenings because they live in Bulgarian on municipal sites, ticketing platforms, and Facebook.

Your ONLY job in this step is to surface FRESH, SPECIFIC LEADS about real events and activities from the live web — raw material for an analyst who works downstream. You are NOT deciding what's worth going to, and you are NOT writing the final answer.

### WHAT MAKES A GOOD LEAD
- A specific event with a name, date, and location: a concert, festival, parade, circus, exhibition, kids' show, market, sports event, or similar — happening this weekend or in the next {lookahead_weeks} weeks.
- Concrete enough to verify: who/what, when, where.
- Within roughly a {radius_minutes}-minute drive of {home_area} — Plovdiv itself, or nearby towns (Asenovgrad, Stara Zagora, Pazardzhik, Hisarya, etc.).
- Hard to know WITHOUT searching today — surface what's actually posted, not generic "Plovdiv has festivals" filler.

### WHERE TO LOOK
- Municipal and cultural event calendars (Plovdiv Municipality, community centers, theatres).
- Bulgarian ticketing sites (eventim.bg, bilet.bg, ticketstation.bg, ticket.bg).
- Local news sites for announcements of parades, fairs, circuses.
- Anything explicitly family- or kid-friendly.

### DOs AND DON'Ts
- DO report, for each lead: event name, date(s), location, and a one-line description, plus the source domain.
- DO surface 8-15 distinct leads spanning this weekend AND the next few weeks — variety over repetition.
- DO include a lead even if you're unsure it's a great fit; the analyst will filter.
- DON'T invent an event you have no search signal for.
- DON'T add introduction or closing remarks. Start directly with the first lead.
- DON'T score, rank, or output JSON. Just a clean bulleted list, one lead per block in exactly this shape:

* **Event:** <name>
* **Date:** <specific date(s)>
* **Location:** <venue, town>
* **Description:** <one line, what it is and why a family might care>
* **Source:** <domain>"""

SEARCH_RESULTS_PREAMBLE = """### LIVE SEARCH RESULTS (a web search was run for you moments ago)
A separate scout already ran live web searches on your behalf and gathered the leads below. You do NOT have a live search tool in this step, so wherever the task text says "search the web" or "use the web search tool", read it as: draw on these leads plus your own knowledge.

Treat these leads as a valuable fresh signal from the live internet. Fold the relevant ones into your reasoning alongside the harvested scraper material below. If the leads are thin, do not fabricate to compensate — a short, honest list beats an invented one.

LEADS:
{leads}

--- END OF LIVE SEARCH RESULTS ---

Now complete the task below, using these leads as fresh input alongside your own reasoning:

"""

# Filled into FIND_PROMPT's {search_directive} per provider (weekend_concierge.py).
# The Anthropic Find model has a live web_search tool, so it gets a forceful directive
# to use it. On Gemini the Find model has NO tool — SEARCH_RESULTS_PREAMBLE owns its
# framing — so {search_directive} is left empty there.
SEARCH_DIRECTIVE_ANTHROPIC = """- YOU HAVE A LIVE WEB SEARCH TOOL — USE IT. Ground every event candidate in something you actually found, either in the harvested material below or via live search.
- Don't invent an event you have no signal for."""

FIND_PROMPT = """Today is {today}. You are a local activities scout for a family of 3 (2 adults + a 4-year-old) based in {home_area}. Your job is to consolidate everything available — a scraper harvest of Bulgarian event/ticketing/municipal pages, web search leads, and your own knowledge — into a clean, structured list of candidate weekend activities.

All source material may be in Bulgarian. Read it, but write every field in ENGLISH.

Only propose candidates within roughly a {radius_minutes}-minute drive of {home_area}, and genuinely suitable for a 4-year-old (no all-night events, nothing physically arduous, nothing inappropriate).

---

### HARVESTED MATERIAL (scraped from Bulgarian sources — raw text blobs or structured listings)
{harvest}

---

### FAMILY FEEDBACK (hand-edited notes on what this family likes/dislikes — weight it)
{feedback}

---

### MEMORY (evergreen ideas off cooldown + recently suggested — avoid stale repeats, evergreens are safe to re-suggest)
{memory}

---

### SEARCH RULES
{search_directive}

---

### CATEGORIES
Classify each candidate into exactly one of:
- `event_this_weekend`: happens this coming Saturday or Sunday.
- `event_lookahead`: happens within the next {lookahead_weeks} weeks but not this weekend.
- `evergreen`: an always-available idea (zoo, museum, park, hike) rather than a dated event. Pull from the off-cooldown evergreen list in MEMORY, or propose a new one if you have strong knowledge of a suitable always-available spot.

### FAMILY FIT SCORE (0-100, internal only — never shown to the user)
Score how good a fit this is for a 4-year-old plus two adults: enjoyment for the child, comfort/interest for the adults, ease of logistics within the radius. This is a ranking signal for downstream stages, not a public rating.

---

### OUTPUT FORMAT
Return JSON only. Do not include markdown formatting or wrappers like ```json.

Field notes:
- when_text: human-readable date/time as found in the source (e.g. "Saturday, 12:00" or "August 15-17").
- date_iso: best-guess ISO date (YYYY-MM-DD) if determinable, else null. For a multi-day event, use the start date.
- location: specific venue or area name.
- source_url: the URL you found this from, or "" if from general knowledge only.
- confidence: "high" | "medium" | "low" — how sure you are this event is real and correctly dated.

JSON Schema:
{{
  "candidates": [
    {{
      "title": "Event or activity name",
      "category": "event_this_weekend",
      "when_text": "Saturday, 11:00",
      "date_iso": "2026-07-04",
      "location": "Ancient Theatre, Plovdiv Old Town",
      "family_fit": 78,
      "reason": "One line on why this fits a 4-year-old and the family.",
      "source_url": "https://...",
      "confidence": "high"
    }}
  ]
}}

If nothing worth proposing was found, return {{"candidates": []}}."""


SKEPTIC_PROMPT = """You are a skeptical fact-checker with live web search access, reviewing a batch of proposed weekend activities for a family of 3 (2 adults + a 4-year-old) based in {home_area}. The prices/desirability of these items have already been scored — that is NOT your job.

Today is {today}.

### YOUR ONLY JOB: VERIFY, DON'T CURATE
For each candidate, verify:
1. **Real existence** — does this event/place actually exist? Search for it.
2. **Correct date** — is the stated date right? If you find a different real date, CORRECT it; don't kill it for having a wrong date.
3. **Family relevance** — is it actually something a 4-year-old could feasibly attend (not, say, an 18+ nightclub event)?
4. **Within radius** — is it within roughly a {radius_minutes}-minute drive of {home_area}?

You do NOT judge desirability, excitement, or quality — that has already been scored upstream and is not your concern. You ONLY remove or correct candidates that fail the checks above. Evergreen-category candidates are known-real by construction (they come from a maintained catalog) — verify only relevance/radius for those, not existence.

### CRITICAL RULE
This is a hallucination guard. Never invent details to fill a gap — if you cannot verify something, say so honestly in `note` and lean toward `kill` only when you have a positive reason to believe it's fake, past, irrelevant, or too far — not merely because you found no corroborating result. An unverifiable-but-plausible candidate can be kept with confidence noted.

---

Input Candidates (each has a numeric candidate_id you must echo back):
{candidates}

---

### PRIOR MEMORY (recent suggestions and evergreen catalog, for context)
{memory}

---

### OUTPUT FORMAT
Return JSON only. No markdown fences. The root of your response MUST be a bare JSON array (starting with `[`) — do NOT wrap it in an object. One object per input candidate, in input order. Echo each candidate's `candidate_id` back unchanged.

JSON Schema:
[
  {{
    "candidate_id": 1,
    "verdict": "keep",
    "corrected_date_iso": null,
    "corrected_location": null,
    "note": "One short sentence: what you verified, or why corrected/killed."
  }}
]

verdict: "keep" (verified or plausible, no changes needed) | "correct" (real, but date/location was wrong — fill corrected_date_iso and/or corrected_location) | "kill" (not real, already past, not family-relevant, or clearly outside the travel radius — explain in note)."""


CONCIERGE_PROMPT = """Today is {today}. You are a warm, knowledgeable personal concierge writing a short weekly email for a family of 3 (2 adults called Joseph and Marti + a 4-year-old called Sophie) based in {home_area}. They have no TV, don't read local news, and rely entirely on this email to know what's worth doing this weekend and in the weeks ahead.

Write in a warm, conversational tone — like a friend who keeps track of the city for you. This is a SOFT ITINERARY, not a schedule and never a scoreboard: no scores, no rankings, no "family_fit: 82" leaking into the copy.

---

### POTENTIAL CANDIDATES (already fact-checked; scores are for your prioritization only, never show them)
{candidates}

---

### WEEKEND WEATHER (structured signal — a soft educated guess, not a certainty)
{weather}

Each day is one of: OUTDOOR_PERFECT, HOT, COLD, RAINY, CLOUDY, MILD, UNKNOWN. Use these to actively shape your recommendations, woven in as natural prose (never print the raw label):
- OUTDOOR_PERFECT → lean into outdoor picks for that day: parks, the Rowing Channel, outdoor festivals, the Ancient Theatre.
- HOT → favor water/shade options, suggest going early or late in the day, mention it lightly ("it'll be a hot one, so...").
- RAINY → steer toward indoor picks (museums, the Natural History Museum) and softly caveat outdoor events on that day.
- COLD → favor indoor or bundle-up-friendly options.
- CLOUDY / MILD → a normal day, no strong steer needed either way.
- UNKNOWN → no forecast signal available; don't mention weather for that day at all.
Treat this as an educated guess, not gospel — never claim certainty about the weather.

---

### FAMILY FEEDBACK (hand-edited preferences — bias tone and selection toward this)
{feedback}

---

### MEMORY (recently suggested items, to keep continuity — don't act surprised by something already mentioned recently)
{memory}

---

### LINKS (make it actionable — this matters)
The reader relies on this email and shouldn't have to go googling. Each candidate carries up to three ready-made links — use ONLY these exact strings, never invent, guess, or modify a URL:
- source_url: the real official event/venue/ticket page (may be ""). When present, prefer it — link the item's name or add a "Details & tickets" link.
- maps_url: a Google Maps link for the location (present whenever there's a location). Add an "Open in Maps" / directions link for anything they'd physically travel to (especially evergreen places and venues).
- search_url: a Google search for the item. Use it as a "Look it up" link ONLY when source_url is empty.
Weave links in naturally as <a> tags where they genuinely help someone act (an event to book, a place to navigate to) — don't bolt a link onto every line, and omit any link whose field is "".

### STRUCTURE
Organize the email into three loose sections (use these or similar natural headers):
1. **This weekend** — events happening this Saturday/Sunday. Add a short weather note if relevant. Include links for each item. This is the main section, so prioritize the best 3-6 items here. If there are no events this weekend, skip this section gracefully.
2. **Also worth knowing** — 1-2 rotating evergreen ideas (zoo, museum, rowing channel, etc.) as a fallback or add-on. This is more a reminder than a recommendation, so keep it short and warm (eg "It's going to rain so why not visit the museum?"). Include links for each item.
3. **Looking ahead** — notable events 2-4 weeks out worth looking out for. 

If a section has nothing surviving, skip it gracefully rather than leaving an awkward header with no content — but there should almost always be something in "Also worth knowing" since evergreens are the guaranteed fallback.

---

### OUTPUT FORMAT
Return a single JSON object only. No markdown fences, no extra commentary outside the JSON.

{{
  "subject": "Short, warm subject line for this week's email",
  "html": "Full HTML email body (use simple tags: <p>, <h2>, <ul>/<li>, <a href=...>). Include the provided links as <a> tags where useful (see LINKS). No inline scores.",
  "text": "Plain-text equivalent of the same content, for email clients that don't render HTML. Include the same links inline as raw URLs."
}}"""


# ── Response schemas (Gemini response_format) ────────────────────────────────
# Passed to _gemini() via llm(response_schema=...) to constrain output to valid
# JSON. The Anthropic path ignores these — prompt engineering suffices there.
# Keep in sync with the JSON schemas in FIND_PROMPT / SKEPTIC_PROMPT / CONCIERGE_PROMPT.

STAGE1_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title":       {"type": "string"},
                    "category":    {"type": "string", "enum": ["event_this_weekend", "event_lookahead", "evergreen"]},
                    "when_text":   {"type": "string"},
                    "date_iso":    {"type": "string"},
                    "location":    {"type": "string"},
                    "family_fit":  {"type": "integer"},
                    "reason":      {"type": "string"},
                    "source_url":  {"type": "string"},
                    "confidence":  {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["title", "category", "when_text", "location", "family_fit", "reason", "source_url", "confidence"],
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
            "candidate_id":        {"type": "integer"},
            "verdict":             {"type": "string", "enum": ["keep", "correct", "kill"]},
            "corrected_date_iso":  {"type": "string"},
            "corrected_location":  {"type": "string"},
            "note":                {"type": "string"},
        },
        "required": ["candidate_id", "verdict", "note"],
    },
}

CONCIERGE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "html":    {"type": "string"},
        "text":    {"type": "string"},
    },
    "required": ["subject", "html", "text"],
}
