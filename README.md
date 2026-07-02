# Weekend Concierge

A passive weekly concierge for a family of 3 (2 adults + a 4-year-old) based near Plovdiv,
Bulgaria. The household is English-speaking with no TV/newspapers, so it misses most of what's
happening locally — parades, concerts, a circus in town — and never hears about the quietly
great standing options (the Stara Zagora zoo, the Natural History Museum, biking the rowing
channel). Every **Thursday** it runs on free GitHub Actions and emails one warm, curated
**soft-itinerary** of what's worth doing this weekend and in the weeks ahead. No app, no server,
no database — just JSON state committed back by CI. You do nothing but read the email.

## How it works

```
weekend_concierge.py   (weekly)
   │
   ├─ HARVEST — scrapers.py (no LLM)
   │     Scrape every enabled Bulgarian source (ticketing, municipal, theatre, news).
   │     Two tiers: raw-fetch (URL → page text) and structured parsers. Each source runs in
   │     its own try/except → [] on failure, so one dead site never breaks the run.
   │
   ├─ Stage 1 — FIND (LLM + web search)
   │     Consolidates the harvest + live search leads + the model's own knowledge into
   │     structured candidates: title, category (this weekend | look-ahead | evergreen),
   │     date/location, a family_fit score, and a source_url when one was found.
   │
   ├─ Stage 2 — SKEPTIC (LLM + web search) — the hallucination guard
   │     One batch call verifies each event's real existence, date, family-relevance, and
   │     90-minute radius. keep | correct (fix date/location) | kill. Evergreens are known-real
   │     and skip the existence check. Never invents — a fake event is dropped here.
   │
   ├─ Anti-repeat filter — state/signals_seen.json
   │     Events keyed slug(title)|date|role (a look-ahead item can resurface as "happening now"),
   │     evergreens on a long cooldown. If no evergreen is off-cooldown, the least-recently-
   │     suggested one is used, so the email is never empty.
   │
   └─ Stage 3 — CONCIERGE (LLM, no search)
         Writes the email — warm prose, a soft itinerary (never a schedule, never a scoreboard),
         grouped This Weekend / Also Worth Knowing / Looking Ahead, with weather woven in softly.
         Every item carries actionable links: the real source_url when present, plus a Google
         Maps link and a search link built deterministically so no URL is ever invented.
         The email always sends (the weekly ritual is the point).
```

State files (`state/`) are committed back by CI after each run — no external database.

## Sources

Scrapers live in `scrapers.py`; the active set is `config.ENABLED_SOURCES`. Adding a source is a
one-line entry in `scrapers.RAW_FETCH_SOURCES` (raw-fetch tier) — it contributes immediately as
page text FIND parses — and can later be upgraded to a structured parser (`scrapers.SCRAPERS`)
when it's worth it. Every structured source keeps its raw-fetch entry too, as an automatic
fallback if the parser ever comes back empty.

**Structured** (dedicated per-event parser): `plovdiv2019.eu`, `bilet.bg`, `ticket.bg`,
`programata.bg` (Kids category), `visitplovdiv.com` (its own AJAX calendar endpoint),
`plovdiv.bg` (events-category news feed), and `lostinplovdiv.com` (English articles feed).

**Raw-fetch only** — `eventim.bg` (its real event data sits behind a JSON API that 403s at
Akamai's edge for every request; the alternative, `pyventim`, drags in a full headless-browser
stack this project avoids) and `ticketstation.bg` (a client-rendered Vue SPA — the fetched HTML
is an empty shell with no event markup to parse). The remaining raw-fetch sources — `dtp.bg`,
`rnhm.org`, `oldplovdiv.bg`, `tourist.stara-zagora.bg`, `marica.bg` — just haven't been evaluated
for a structured upgrade yet.

`facebook` is a documented stub, disabled until auth/anti-bot is worth solving. See `CLAUDE.md`
for the full per-source investigation notes.

## Setup

1. Push this repo to GitHub.
2. Add secrets and variables under *Settings → Secrets and variables → Actions*:

   **Secrets** (encrypted):

   | Secret | What |
   |---|---|
   | `GEMINI_API_KEY` | aistudio.google.com/apikey — required if `LLM_PROVIDER=gemini` |
   | `ANTHROPIC_API_KEY` | console.anthropic.com — required if `LLM_PROVIDER=anthropic` |
   | `SMTP_HOST` / `SMTP_PORT` | e.g. `smtp.gmail.com` / `587` |
   | `SMTP_USER` / `SMTP_PASS` | sending address + app password (Gmail: 2FA → App Password) |
   | `EMAIL_TO` / `EMAIL_FROM` | recipient / sender (both default to `SMTP_USER`) |

   **Variables** (plain text, *Variables* tab):

   | Variable | Default | Effect |
   |---|---|---|
   | `LLM_PROVIDER` | `anthropic` | `anthropic` or `gemini` |

   No hotel/weather/RapidAPI keys — weather uses open-meteo, which needs none.

3. Enable Actions. It runs automatically at **06:00 UTC every Thursday**; test any time via
   *Actions → weekly → Run workflow*.

> **LLM web search:** with `LLM_PROVIDER=anthropic`, FIND and SKEPTIC use the Anthropic
> `web_search` tool. With `LLM_PROVIDER=gemini`, search runs on a lite model (`google_search`)
> and the flagship reasons over the results — see `common.py` for the two-step split.

## Running locally

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...  LLM_PROVIDER=gemini
# or: export ANTHROPIC_API_KEY=...  LLM_PROVIDER=anthropic
python weekend_concierge.py   # writes state/; emails if SMTP vars set, else prints the error
python scrapers.py            # harvest smoke test: prints per-source item counts
python weather.py             # weekend weather summary smoke test
```

Leave SMTP vars unset to test without sending (the send is caught and printed). Offline tests
stub the network and LLM:

```bash
python -m unittest test_concierge test_memory test_scrapers
```

## Tuning (config.py)

| Knob | Default | Effect |
|---|---|---|
| `ENABLED_SOURCES` | 14 sources | Which scrapers run each harvest. |
| `MAX_HARVEST_ITEMS` | 200 | Cap on the deduped harvest handed to FIND. |
| `RADIUS_MINUTES` | 90 | Max one-way travel time from Plovdiv worth suggesting. |
| `LOOKAHEAD_WEEKS` | 4 | How far ahead "notable events to plan for" reaches. |
| `MIN_INCLUDE_SCORE` | 50 | family_fit floor (0–100) below which an event is dropped. |
| `EVENT_TTL_DAYS` | 21 | Cooldown before the same event can resurface. |
| `EVERGREEN_COOLDOWN_DAYS` | 70 | Cooldown before the same evergreen idea can resurface. |
| `MODEL_FIND` / `MODEL_SKEPTIC` / `MODEL_CONCIERGE` | haiku / sonnet / sonnet | Per-stage model roles (Gemini equivalents via `GEMINI_MODEL_MAP`). |
| `SEED_EVERGREEN` | seed list | Initial evergreen catalog; the pipeline grows it over time. |

Family preferences live in `preferences.md` — hand-edit it ("Loved / Not interested /
Constraints") and it's injected into the FIND and CONCIERGE prompts.

## Cost

A handful of LLM calls per week (three stages, on Claude Haiku/Sonnet or Gemini) plus free
open-meteo weather and direct scraping. Effectively free at this cadence.

See `CLAUDE.md` for the full design rationale and pipeline invariants, and `plan.md` for the
original design notes.
