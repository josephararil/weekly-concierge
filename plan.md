# Weekend Concierge — new pipeline plan

## Context

The user lives near Plovdiv, Bulgaria in an English-speaking household with no TV/newspapers
and few local roots. They repeatedly miss things worth doing on weekends (parades, concerts, a
circus in town, a nearby petting zoo) simply because that information lives in Bulgarian on
Facebook, municipal sites, and ticketing platforms they never see. This is the "eternal Saturday
problem."

The goal is a **brand-new, separate repo** — do NOT touch the deal-hunter — that reuses
deal-hunter's proven skeleton (LLM + web search, per-stage models, committed JSON state, weekly
GitHub Actions run, one email digest) but answers *"what should we do this weekend?"* instead of
*"what great deals exist?"*. It emails a warm, curated **soft-itinerary** every **Thursday**,
covering (1) events happening this weekend, (2) 1–2 rotating evergreen ideas (zoo, museums,
rowing channel), and (3) notable events 2–4 weeks out worth planning for. It runs passively on
free GitHub Actions; the user does nothing but read the email.

Same Pareto ethos as deal-hunter: flat functions, plain stdlib + `requests` (+ BeautifulSoup),
short readable modules, no frameworks.

## Architecture

```
weekend_concierge.py  (main, adapted from find_city_anomalies.py)
  │
  ├─ Load memory (state/memory.json) + feedback (preferences.md) + weather (weather.py)
  │
  ├─ HARVEST (scrapers.py, NO LLM) ─ run every enabled source, collect raw listings
  │     Two-tier registry (see below). Per-source failure is caught → [] (never crashes run).
  │
  ├─ Stage 1 · FIND (Gemini flash + web search) ─ consolidate scraper harvest + Google
  │     leads + own knowledge into structured candidates. Each: title, category
  │     (event_this_weekend | event_lookahead | evergreen), when/date, location + area,
  │     family_fit score 0–100, one-line reason, source_url, confidence. Injects evergreen
  │     catalog (off-cooldown) + feedback so it can propose/rotate evergreens too.
  │
  ├─ Stage 2 · SKEPTIC (gemini-pro-latest + web search) ─ ONE batch call over all candidates.
  │     Verifies each event's real existence + correct date + family relevance + 90-min radius.
  │     Returns keep | correct(date/location) | kill. Kills hallucinated/past/irrelevant/too-far
  │     items. Evergreen items (known-real, from catalog) bypass the kill logic but can be
  │     dropped for poor fit. This is the hallucination guard — the whole reason the email is
  │     trustworthy enough to act on.
  │
  ├─ Anti-repeat filter (state/signals_seen.json) ─ suppress items still in cooldown.
  │     Events keyed slug(title)|date|role (role = lookahead|thisweekend, so a look-ahead item
  │     re-surfaces as "happening now" the actual weekend). Evergreen keyed evergreen|slug(name),
  │     cooldown EVERGREEN_COOLDOWN_DAYS (~70). If nothing evergreen is off-cooldown, fall back
  │     to least-recently-suggested so the email is never empty.
  │
  ├─ Stage 3 · CONCIERGE (gemini-pro-latest, NO search) ─ writes the email.
  │     Input: surviving candidates + scores + weather summary + feedback + recent-suggestion
  │     memory. Output: warm HTML + text, grouped This Weekend / Also On / Looking Ahead, a soft
  │     (not strict) itinerary, weather woven in softly. Scores are internal — never shown.
  │
  ├─ Memory write (state/memory.json + .md) ─ ledger entry per candidate (sent/killed + score +
  │     note); grow evergreen catalog with any new always-available spots SKEPTIC confirmed;
  │     prune; save.
  │
  ├─ Email (common.send_email) ─ ALWAYS sends on Thursday (weekly ritual; evergreen guarantees
  │     content). Mark included items seen; bump monthly_count.
  │
  └─ Always writes state/: weekend_signals.json (machine), weekend_log.md (human), memory.json,
        memory.md, signals_seen.json  ← all committed back by CI.
```

Flow matches the user's sketch: **(LLM search + scraper) → FIND → SKEPTIC → Email**, with a
dedicated concierge writer as the email step and a deterministic anti-repeat gate between.

## Files

| File | Action | Notes |
|---|---|---|
| `common.py` | **Copy verbatim** from deal-hunter | Domain-agnostic: `llm()`, `send_email()`, `parse_json_block()`, `load_json/save_json`, `today_iso()`, `resolved_provider()`, retry, Gemini two-step search. No changes. |
| `memory.py` | **Adapt** | Keep `load/save/prune/summarize_for_prompt` shape. Repurpose `baselines`→`evergreen` catalog (`name → {location, area, description, tags, last_suggested, discovered, source}`); repurpose `record_baseline`→`record_evergreen`, `record_outcome`→`record_suggestion` (fields: date, title, category, when, location, url, score, verdict, note). `summarize_for_prompt` emits recent suggestions + off-cooldown evergreens. |
| `config.py` | **Rewrite** | Activities knobs + source registry + prompts + schemas (below). Keep per-stage model-role + Gemini-map + token-budget pattern. |
| `scrapers.py` | **NEW — centerpiece** | Two-tier per-source registry; the piece everything depends on. |
| `weather.py` | **NEW — small** | open-meteo (no API key) → soft weekend summary. |
| `weekend_concierge.py` | **NEW (adapt `find_city_anomalies.py`)** | Main pipeline; reuses its main()/anti-spam/memory-write shape. |
| `preferences.md` | **NEW** | Hand-edited feedback file, injected into FIND + CONCIERGE prompts. Seed with a template ("Loved: … / Not interested in: … / Constraints: …"). |
| `.github/workflows/weekly.yml` | **NEW (copy `daily.yml`)** | Cron `0 6 * * 4` (Thursday ~09:00 EEST); change job/concurrency name; drop RAPIDAPI/HOTEL_PROVIDER env. |
| `requirements.txt` | **Copy + add** | Add `beautifulsoup4>=4.12`, `lxml>=5.0`. |
| `state/memory.json` | seed `{"evergreen": {}, "ledger": []}` | |
| `state/signals_seen.json` | seed `{"seen": {}, "monthly_count": {}}` | |
| `state/weekend_signals.json`, `state/weekend_log.md`, `state/memory.md` | generated | |
| `CLAUDE.md` | **NEW** | Document the new pipeline (mirrors deal-hunter's doc quality). |

## Scraper framework (`scrapers.py`) — the critical piece

Designed so **adding a source is a one-line entry** and improving one is a localized change.

**Normalized `RawItem` dict:** `{source, title, when_text, date_iso|None, location, url, description}`.

**Two tiers per source:**
1. **Raw-fetch (default, one-liner):** fetch the URL, extract main text, return a single
   `RawItem` whose `description` is the page text blob. FIND parses events out of it. New
   sources start here and contribute immediately.
2. **Structured (upgrade):** a dedicated parser (BeautifulSoup) returns a list of clean
   `RawItem`s with per-event dates/locations. Worth it for high-value sites.

**Registry:** `SCRAPERS = {"eventim": scrape_eventim, "plovdiv2019": scrape_plovdiv2019, ...}`
plus `RAW_FETCH_SOURCES = {"bilet": "https://…", ...}` for one-line entries. `config.ENABLED_SOURCES`
turns each on/off. `harvest(today) -> list[RawItem]` runs every enabled source inside try/except
(logs `source: N items` or `source: FAILED`), dedupes by (title, date), caps total volume before
handing to FIND.

**Shared helpers:** `fetch(url)` (polite UA, timeout, retry — reuse `common._post_with_retry`
style), `text_of(html)`, `bg_date(text) -> date_iso|None` (Bulgarian month-name map: януари…декември).

**v1 implementation plan (per user's list — all contribute day one):**
- **Structured references (build 2–3):** `eventim.bg` (structured ticketing), `plovdiv2019.eu`
  (**CRITICAL** per user), and one more tractable (`grabo.bg` or `bilet.bg`). Implementer fetches
  each page, inspects HTML, writes the parser.
- **Raw-fetch (one-line each, immediate):** `ticketstation.bg`, `ticket.bg`, `dtp.bg`,
  `rnhm.org`, `oldplovdiv.bg`, `programata.bg` (Sofia), `tourist.stara-zagora.bg`, `plovdiv.bg`/
  `visitplovdiv`, `marica.bg`.
- **Facebook:** documented stub (`scrape_facebook` raising NotImplemented note re: auth/anti-bot);
  FIND's web search partially compensates. Revisit later.

Modularity is explicit so the user can keep adding sources with Claude Code over time.

## Config knobs (`config.py`)

- **Geo:** `PLOVDIV_LATLON`, `RADIUS_MINUTES = 90`, `HOME_AREA` text for prompts.
- **Models:** `MODEL_FIND` (flash), `MODEL_SKEPTIC`/`MODEL_CONCIERGE` (pro-latest); reuse
  `GEMINI_MODEL_MAP`, `GEMINI_SEARCH_MODEL`, `PROVIDER_*` overrides, `MAX_TOKENS_*`.
- **Coverage:** `LOOKAHEAD_WEEKS = 4`, `MIN_INCLUDE_SCORE` (family-fit floor for events).
  No count cap on events or evergreens sent to the concierge — every survivor that clears
  FIND + SKEPTIC + anti-repeat is included.
- **Anti-repeat:** `EVENT_TTL_DAYS` (≈21), `EVERGREEN_COOLDOWN_DAYS = 70`.
- **Sources:** `ENABLED_SOURCES`, `RAW_FETCH_SOURCES`.
- **Evergreen seed:** `SEED_EVERGREEN` (hand list: Stara Zagora zoo, Plovdiv Regional Natural
  History Museum, rowing channel bike ride, Ancient Theatre, Bachkovo, …) merged into the catalog
  on first run.
- **Prompts + schemas:** `SEARCH_PROMPT`, `SEARCH_RESULTS_PREAMBLE`, `FIND_PROMPT`,
  `SKEPTIC_PROMPT`, `CONCIERGE_PROMPT`; `STAGE1/2_RESPONSE_SCHEMA` (Gemini). Prompts instruct:
  search/read Bulgarian sources, **write the email in English**, weight a 4-year-old's enjoyment,
  keep the 90-min radius, treat weather as a soft educated guess. (User will iterate prompts.)

## Weather (`weather.py`)

`weekend_weather(latlon, today) -> dict[str, str]`: open-meteo daily forecast (no key), reading
Sat/Sun max temp, precip probability, and WMO weather code. Returns `{"Sat": ..., "Sun": ...}`,
each mapped to one categorical label: `RAINY` (precip prob > 50% or WMO rain codes 51–65/80–82),
`HOT` (max temp > 32°C), `COLD` (max temp < 12°C), `CLOUDY` (WMO 1–3), `OUTDOOR_PERFECT` (WMO 0
and 18–28°C), `MILD` (default safe bucket), or `UNKNOWN` (network/API failure — caught, never
raises). CONCIERGE consumes these labels (with an explicit "treat as educated guess" instruction)
to decide indoor/outdoor/shade framing per day, rather than parsing a prose summary.

## Feedback (`preferences.md`)

Plain markdown the user hand-edits ("Loved: … / Not interested: … / Constraints: …"), read as
text and injected into FIND (to bias discovery) and CONCIERGE (to bias tone/selection). No
reply-parsing. Memory schema also leaves room for future "went/liked" flags.

## Critical invariants (carry the discipline over)

- All LLM calls via `common.llm()`; all email via `common.send_email()`. No direct HTTP/SMTP.
- Scrapers never crash the run — per-source try/except → `[]`.
- SKEPTIC only removes/corrects; it is the hallucination guard (don't send fake events).
- Tiers/scores are internal only; the email is prose, never a scoreboard.
- State files in `state/` are CI-managed real state, committed each run; seed shapes as above.
- Anti-repeat key includes `role` for events and uses a long cooldown for evergreens.
- Email always sends Thursday (weekly ritual), guaranteed non-empty via evergreen fallback.

## Implementation order (for Sonnet)

1. **Bootstrap repo:** new dir/repo; copy `common.py`, `memory.py`, `daily.yml`, `requirements.txt`
   from deal-hunter as starting points; seed `state/` files; add `beautifulsoup4`/`lxml`.
2. **`scrapers.py`:** framework (RawItem, registry, `harvest`, `fetch`, `bg_date`) + raw-fetch for
   all listed sources + 2–3 structured references + FB stub. Verify: `python -c "import scrapers; print([(i['source'], i['title'][:40]) for i in scrapers.harvest('2026-07-02')][:20])"`.
3. **`weather.py`:** open-meteo fetch + soft summary. Verify standalone.
4. **`memory.py` adapt:** evergreen catalog + suggestion ledger + summarize.
5. **`config.py`:** knobs, sources, seed evergreens, prompts, schemas.
6. **`weekend_concierge.py`:** wire HARVEST → FIND → SKEPTIC → anti-repeat → CONCIERGE → memory →
   email, adapting `find_city_anomalies.py`'s main()/anti-spam/state-write scaffolding.
7. **`weekly.yml`:** Thursday cron; env = Gemini/Anthropic + SMTP only.
8. **`preferences.md`** template + **`CLAUDE.md`** for the new repo.

## Verification (end-to-end)

- **Scrapers:** run `harvest()` alone; confirm each source logs a nonzero count or a clean FAILED,
  and no exception escapes.
- **Weather:** call `weekend_weather()`; confirm empty string on mild forecast, phrase on extreme.
- **Offline pipeline test:** stub `common.llm` to return canned JSON for FIND + SKEPTIC and canned
  HTML for CONCIERGE; run `weekend_concierge.py` with SMTP unset (send is caught/printed). Confirm
  `state/weekend_log.md`, `weekend_signals.json`, `signals_seen.json`, `memory.json` are written
  and anti-repeat keys look right; run twice to confirm suppression + evergreen rotation.
- **Live smoke test:** `GEMINI_API_KEY=… LLM_PROVIDER=gemini python weekend_concierge.py` (SMTP
  unset) → inspect the drafted email in `weekend_log.md` for real, in-radius, correctly-dated
  events and a warm soft-itinerary tone.
- **Secrets/vars for the new repo:** `GEMINI_API_KEY`/`ANTHROPIC_API_KEY`, `LLM_PROVIDER`,
  `SMTP_HOST/PORT/USER/PASS`, `EMAIL_TO/FROM`. No RapidAPI/hotel/weather keys needed.

---


