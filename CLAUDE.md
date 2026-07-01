Guidance for Claude Code when working in this repository.

## ⚠️ Build status — READ THIS FIRST

**This repo is NOT finished and NOT yet functional.** It was cloned from a sibling project
(`deal-hunter`, a travel-deal finder) and is being transformed, task by task, into the
**Weekend Concierge** described in `PLAN.md`. Much of what you see is LEFTOVER deal-hunter code
(hotel/price/deal wording, `find_city_anomalies.py`, etc.) that has not been rewritten yet.

- **`PLAN.md` is the source of truth for the target design. This file describes the end-state.**
- Where the current code disagrees with `PLAN.md` or your task, the plan/task wins.
- Reuse deal-hunter's infrastructure verbatim where the plan says so (`common.py` especially).
- Update this file as pieces land; delete the leftover deal-hunter references as you replace them.

## What this is

A personal weekend-activities concierge for a family of 3 (2 adults + 4-year-old) based near
Plovdiv, Bulgaria. The household is English-speaking with no TV/newspapers and little local
plug-in, so it misses events happening around it. This pipeline runs weekly on free GitHub
Actions and emails one warm, curated **soft-itinerary** every Thursday: (1) events happening
this weekend, (2) 1–2 rotating evergreen ideas (zoo, museums, rowing channel), and (3) notable
events 2–4 weeks out. Passive by design — the user only reads the email. No server, no database;
JSON state committed back by CI.

Same Pareto ethos as its sibling: small, flat, readable scripts over clever abstractions. If a
change adds a framework or a layer of indirection to save a few lines, it's probably wrong here.

## Pipeline (target — see PLAN.md for the full diagram)

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
  │             scores + weather + feedback + recent-suggestion memory (soft itinerary, prose)
  ├─ Memory write        ledger per candidate; grow evergreen catalog; prune; save
  ├─ Email               ALWAYS sends Thursday (weekly ritual; evergreen guarantees content)
  └─ Always writes state/: weekend_signals.json, weekend_log.md, memory.json/.md, signals_seen.json
```

## Files (target)

| File | Role |
|---|---|
| `common.py` | `llm()`, `send_email()`, `parse_json_block()`, state IO, Gemini two-step search. **Copied verbatim from deal-hunter — do not modify.** |
| `scrapers.py` | **Centerpiece.** Two-tier per-source registry (raw-fetch default + structured upgrade), `harvest()`, `fetch()`, `bg_date()`. Adding a source is a one-line entry. |
| `weather.py` | open-meteo (no key) → soft weekend summary; strong signals only. |
| `memory.py` | `load/save/prune/summarize_for_prompt`; evergreen catalog + suggestion ledger. |
| `config.py` | Knobs, source registry, seed evergreens, per-stage model roles, prompts, schemas. |
| `weekend_concierge.py` | The pipeline (HARVEST→FIND→SKEPTIC→anti-repeat→CONCIERGE→email). |
| `preferences.md` | Hand-edited feedback ("Loved / Not interested / Constraints"), injected into prompts. |
| `.github/workflows/weekly.yml` | Thursday cron; commits `state/`. |
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
- **State in `state/` is CI-managed real state**, committed every run. Seed shapes as above.
- **Anti-repeat keys:** events `slug(title)|date|role` (role = lookahead|thisweekend, so a
  look-ahead item can re-surface as "happening now"); evergreens `evergreen|slug(name)` with
  `EVERGREEN_COOLDOWN_DAYS` (~70). If no evergreen is off-cooldown, fall back to least-recently-
  suggested so the email is never empty.
- **The email always sends on Thursday** (the weekly ritual is the point). Evergreen fallback
  guarantees non-empty content even on a dead-event weekend.
- **Weather is a soft educated guess.** Only surface strong signals (very hot → water/shade,
  near-certain rain → indoor). A 10–30% chance is a non-signal; return nothing.
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
python weather.py             # weekend weather summary smoke test
```

Leave SMTP vars unset to test without sending (the send is caught and printed).

## Known trade-offs (accepted — don't "fix" without asking)

- **Scraping is brittle.** Sites change; some sources are raw-fetch page-text blobs that FIND must
  parse. Facebook is a documented stub (auth/anti-bot). FIND's web search partially compensates.
- **No ticketing/price data.** This finds things to do, not the cheapest way to do them.
- **Weather is unreliable in Plovdiv** and treated as a soft nudge only.
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
```