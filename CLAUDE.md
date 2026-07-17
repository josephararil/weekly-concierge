# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Build status

**Live.** The full pipeline is built, merged to `main`, and running weekly on GitHub Actions
(first real run succeeded). All modules exist and are wired: `common.py`, `scrapers.py`,
`weather.py`, `memory.py`, `config.py`, `weekend_concierge.py`, `.github/workflows/weekly.yml`,
`preferences.md`. Ongoing work is **iteration, not construction**: tuning the prompts, and
adding/upgrading scrapers (raw-fetch → structured) as new sources prove worthwhile.

- `plan.md` captures the original design rationale and the intended end-state; consult it when
  a change might conflict with a core design decision.
- `common.py` is deal-hunter's infrastructure reused verbatim by design — don't modify it here.

## What this is

A personal weekend-activities concierge for a family of 3 (2 adults + 4-year-old) based near
Plovdiv, Bulgaria. The household is English-speaking with no TV/newspapers and little local
plug-in, so it misses events happening around it. This pipeline runs weekly on free GitHub
Actions and emails one warm, curated **soft-itinerary** every Friday: (0) a short weather-at-
a-glance grounding for the weekend, (1) events happening this weekend, (2) rotating evergreen
ideas (zoo, museums, rowing channel), and (3) notable events 2–4 weeks out. Passive by design —
the user only reads the email. No server, no database; JSON state committed back by CI.

Same Pareto ethos as its sibling: small, flat, readable scripts over clever abstractions. If a
change adds a framework or a layer of indirection to save a few lines, it's probably wrong here.

## Pipeline (see PLAN.md for the full diagram)

```
weekend_concierge.py
  ├─ Load memory (state/memory.json) + feedback (preferences.md) + weather (weather.py)
  ├─ HARVEST   scrapers.py — run every enabled source, per-source failure → [] (never crashes)
  ├─ Stage 1 · FIND      (Gemini flash + web search) consolidate harvest + Google leads + own
  │             knowledge → structured candidates (title, category, when/date, location,
  │             family_fit score, reason, source_url, confidence)
  ├─ Stage 2 · SKEPTIC   (gemini-pro-latest + search) ONE batch call: verify each event's real
  │             existence + date + family relevance + 90-min radius → keep | correct | kill
  ├─ Anti-repeat filter  state/signals_seen.json — events keyed slug(title)|date|role,
  │             evergreens keyed evergreen|slug(name) with a long cooldown
  ├─ Stage 3 · CONCIERGE (gemini-pro-latest, no search) writes the email from survivors +
  │             scores + weather + feedback + recent-suggestion memory (soft itinerary, prose).
  │             Each candidate carries actionable links: real source_url + a Google Maps link
  │             and a search link built deterministically (build_links) so no URL is invented.
  ├─ Memory write        ledger per candidate; grow evergreen catalog; prune; save
  ├─ Email               ALWAYS sends Friday (weekly ritual; evergreen guarantees content)
  └─ Always writes state/: weekend_signals.json, weekend_log.md, memory.json/.md, signals_seen.json
```

## Files

| File | Role |
|---|---|
| `common.py` | `llm()`, `send_email()`, `parse_json_block()`, state IO, Gemini two-step search. **Copied from deal-hunter — do not modify beyond the deliberate exception below.** `send_email()` raises `SMTPRecipientsRefused` if `smtplib.send_message()` returns any refused recipients — `send_message()` only raises on *total* failure, so a multi-address `EMAIL_TO` (e.g. `"a@x.com,b@x.com"`) could otherwise have one address silently dropped with no error. |
| `scrapers.py` | **Landed.** Two-tier per-source registry (raw-fetch default + structured upgrade), `harvest()`, `fetch()`, `text_of()`, `bg_date()`. Structured parsers: `plovdiv2019.eu` (its own JS calendar just navigates to a server-rendered `?f_time=all&page=N` — see the docstring), `bilet.bg`, `ticket.bg` (homepage `div.productItem` cards; no year in the date string, so it assumes the next upcoming occurrence like `bg_date`; pre-filters to the Plovdiv-radius towns plus Sofia, and Sofia only when the event is ≥14 days out), `programata.bg` (Kids category page, `div.post-list-entry` cards; the site is an editorial/magazine, not a calendar — listing cards have no date/venue field, only free-form prose inside each article, so `date_iso`/`location` are left unset for FIND/SKEPTIC to resolve), `visitplovdiv.com` (its "culture calendar" listing page itself renders empty — its own JS fills it in from an XML AJAX endpoint after load, so the parser calls that endpoint directly and parses XML, not HTML; `location` is left unset since it's only present as free-form prose inside `content`), `plovdiv.bg` (events-category news feed, `article.post` cards; it's a municipal announcements blog, not a calendar — the listing's own `.post-date` is the article's publish date, not the event date, so it's ignored in favor of best-effort `bg_date()` extraction from the free-form Bulgarian prose in each card's body text; many cards are general municipal news with no event date at all, which simply yields `date_iso=None` for FIND/SKEPTIC to judge), and `lostinplovdiv.com` (`/en/articles` feed, `article.article` cards; a hand-curated bilingual city guide, not a calendar — the listing has no pagination param and returns its entire ~10-year archive newest-first, so the parser slices to the newest 30 cards; most articles are evergreen roundups or local trivia with no event date, left `date_iso=None` for FIND/SKEPTIC, except the recurring "What to do in Plovdiv (DD.MM - DD.MM)" weekly digest whose title embeds its own date range — that date is taken at face value in today's year rather than rolled forward, since it describes the current/just-finished week, not a future one; the listing's own one-sentence blurb is too thin for FIND to extract anything from an actual event/activity guide — e.g. a "which events in June" roundup collapses to a teaser with none of the dozen dates it lists — so `_lostinplovdiv_is_actionable()` heuristically flags titles that read as an activity guide (a numbered listicle, a "where is/are/to" question, or an event/activity keyword) versus pure local-history trivia, and only those get one extra fetch of the full article body via `_fetch_lostinplovdiv_detail()`, capped at `LOSTINPLOVDIV_MAX_DETAIL_FETCHES` extra requests per harvest). Two sources were investigated and deliberately kept raw-fetch: `eventim.bg` — its real event data comes from a JSON API (public-api.eventim.com/websearch/search/api/exploration/v1/productGroups) that 403s at Akamai's edge for every request regardless of correct params (reverse-engineered from the site's own JS), and the suggested `pyventim` fallback pulls in playwright/patchright/curl_cffi/scrapling — the exact heavy headless-browser stack this project avoids — so neither route was adopted (see the comment above `RAW_FETCH_SOURCES` for the full investigation); and `ticketstation.bg` — a client-rendered Vue SPA whose fetched HTML is only an empty `<div id="app">` shell plus a JS bundle that fetches events after load, leaving no event markup a structured parser could select or be verified against. The rest of `RAW_FETCH_SOURCES` (`dtp.bg`, `rnhm.org`, `oldplovdiv.bg`, `tourist.stara-zagora.bg`, `marica.bg`, `plovdiv24.bg`) simply haven't been evaluated for a structured upgrade yet — no investigation, just page-text blobs FIND parses; upgrade only if one proves worth the maintenance cost. `scrape_facebook` is a documented stub (raises `NotImplementedError`, caught by `harvest()`) — no auth/anti-bot handling yet. `config.ENABLED_SOURCES`/`MAX_HARVEST_ITEMS` turn sources on/off and cap volume. Adding a raw-fetch source is a one-line entry in `RAW_FETCH_SOURCES` + `ENABLED_SOURCES`. Tests: `test_scrapers.py` (offline, mocks network; fixture-backed parse tests in `tests/fixtures/` cover all seven structured sources). |
| `weather.py` | open-meteo (no key) → raw Sat/Sun forecast data (max/min temp, feels-like, humidity, cloud cover, chance of rain, condition), passed to CONCIERGE as-is so the model reasons over the actual numbers rather than a pre-classified label. |
| `memory.py` | `load/save/prune/summarize_for_prompt`; evergreen catalog + suggestion ledger. Evergreen entries carry optional `url` (official page → real "Details" link when emailed) and `practical` (hours/fees/season/safety note → injected into prompts); both preserve-on-missing across upserts. |
| `config.py` | Knobs, source registry, seed evergreens, per-stage model roles, prompts, schemas. `SEED_EVERGREEN` holds the original 5 seeds plus ~37 `source="research"` places (from a Gemini Deep Research sweep of family attractions within a ~90-min drive of Plovdiv), each with optional `url`/`practical` fields; seeded into `state/memory.json` on the first run where the name is absent. |
| `weekend_concierge.py` | The pipeline (HARVEST→FIND→SKEPTIC→anti-repeat→CONCIERGE→email). `build_links()` builds each candidate's `(source_url, maps_url, search_url)` before the concierge call. Tests: `test_concierge.py` (offline, stubs `common.llm`/`scrapers.harvest`/`weather.weekend_weather`, runs `main()` twice to verify state files + event suppression + evergreen rotation, plus a `build_links` unit test). |
| `preferences.md` | Hand-edited feedback ("Loved / Not interested / Constraints"), injected into prompts. Constraints also carry factual exclusions (Aqualand closed; Asen's Fortress / Kuklen Waterfall / Belintash too dangerous for a 4-year-old) so FIND/CONCIERGE never propose them. |
| `.github/workflows/weekly.yml` | Friday 6am UTC, fixed — no DST logic. (A prior two-cron-plus-skip-guard scheme meant to land on 9am Sofia time year-round instead fired both crons every week and skipped both, since GitHub Actions scheduling jitter meant the actual run hour rarely matched the guard's exact expected hour.) Commits `state/`. |
| `state/*.json` | CI-managed state. Seeds: `memory.json={"evergreen":{},"ledger":[]}`, `signals_seen.json={"seen":{},"monthly_count":{}}`. |

## Critical invariants — do not break

- **All LLM calls go through `common.llm()`; all email through `common.send_email()`.** Abstracts
  Anthropic vs Gemini and the single SMTP path. Never call provider/SMTP endpoints directly.
- **Scrapers never crash the run.** Every source runs inside try/except → `[]` on any failure.
  Log per-source counts; one dead source must not lose the others.
- **SKEPTIC only removes or corrects — it is the hallucination guard.** It exists so the email is
  trustworthy enough to act on. It must not invent items; it kills fake/past/irrelevant/too-far
  events and corrects wrong dates. Desirability is FIND/CONCIERGE's job, not a SKEPTIC kill.
- **Scores are internal only.** family_fit scores rank items and drive the anti-repeat rotation.
  The email is warm prose / a soft itinerary — NEVER a scoreboard or a strict hour-by-hour plan.
- **Links are real or built, never invented.** The email should be actionable, but the CONCIERGE
  is given exact link strings and told not to fabricate URLs. `build_links()` passes through the
  real `source_url` (from FIND/scrapers, or an evergreen's catalog `url`; may be "") and
  deterministically constructs a Google Maps link (from `location`) and a search link (from
  `title`+`location`). Add links where they help someone act, not on every line.
- **State in `state/` is CI-managed real state**, committed every run. Seed shapes as above.
- **Anti-repeat keys:** events `slug(title)|date|role` (role = lookahead|thisweekend, so a
  look-ahead item can re-surface as "happening now"); evergreens `evergreen|slug(name)` with
  `EVERGREEN_COOLDOWN_DAYS` (~70). If no evergreen is off-cooldown, fall back to least-recently-
  suggested so the email is never empty.
- **The email always sends on Friday** (the weekly ritual is the point). Evergreen fallback
  guarantees non-empty content even on a dead-event weekend.
- **Weather is fed to the LLM as raw data, not a pre-classified label.** `weather.py` returns
  actual forecast numbers (max/min temp, feels-like, humidity, cloud cover, chance of rain,
  condition) for Sat/Sun; CONCIERGE is trusted to interpret them and open the email with a
  short weather-at-a-glance line before any recommendations. Still a best-effort estimate,
  never a certainty — the prompt says so explicitly.
- **Everything Bulgarian in, English out.** Search/scrape Bulgarian sources; write the email in
  English.
- **Per-stage model roles live in `config.py`** (`MODEL_FIND/SKEPTIC/CONCIERGE`, `GEMINI_MODEL_MAP`,
  `GEMINI_SEARCH_MODEL`), never as literals in pipeline code. Gemini splits search (lite model)
  and reasoning (flagship, no tools) — see deal-hunter's `common.py` docs.

## Required secrets / variables

| Name | Type | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | secret | Gemini LLM calls (default provider) |
| `ANTHROPIC_API_KEY` | secret | Anthropic LLM calls (if `LLM_PROVIDER=anthropic`) |
| `LLM_PROVIDER` | repo variable | `"gemini"` (default) or `"anthropic"` |
| `SMTP_HOST/PORT/USER/PASS` | secrets | Email delivery |
| `EMAIL_TO` / `EMAIL_FROM` | secrets | Recipient / sender (default to SMTP_USER) |

No RapidAPI/hotel/weather keys (open-meteo needs none).

## Running locally

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...  LLM_PROVIDER=gemini
python weekend_concierge.py   # writes state/; emails if SMTP vars set, else prints the error
python scrapers.py            # harvest smoke test: prints per-source item counts
python weather.py             # weekend weather forecast smoke test
```

Leave SMTP vars unset to test without sending (the send is caught and printed).

## Known trade-offs (accepted — don't "fix" without asking)

- **Scraping is brittle.** Sites change; some sources are raw-fetch page-text blobs that FIND must
  parse. Facebook is a documented stub (auth/anti-bot). FIND's web search partially compensates.
- **No ticketing/price data.** This finds things to do, not the cheapest way to do them.
- **Weather is unreliable in Plovdiv** and treated as a best-effort estimate, not gospel.
- **Family-only scope, 90-min radius.** Destinations needing arduous travel or poor for a
  4-year-old are excluded by the prompts. Intentional.

## Out of scope (do not start without an explicit request)

- Booking/ticket purchase integration.
- A web/PWA front-end — the product is deliberately a passive weekly email.
- Reply-to-email feedback parsing (feedback is the hand-edited `preferences.md`).

## Style

Flat functions, plain stdlib + `requests` + BeautifulSoup, clear names, short modules. Match the
existing tone. Prefer editing in place over adding files. Comment only the non-obvious. No emoji
in code; `weekend_log.md` and the email HTML may use them.