# CLAUDE.md

Guidance for Claude Code when working in this repository. Rules and one-line reasons live here;
the evidence behind them (incident write-ups, the source-by-source scraper investigation log,
calibration runs) is archived verbatim in [`docs/history.md`](docs/history.md). Read that file
when a change touches an invariant and the short reason below isn't enough.

## Status

**Live**, running weekly on GitHub Actions since July 2026. Work here is iteration, not
construction: tuning prompts and adding/upgrading scrapers. The owner changes this repo rarely,
so prefer fixes to the live run's correctness over process tooling (there is deliberately no PR
CI; run the offline tests locally before merging).

- `plan.md` holds the original design rationale.
- `llm_chain` is an **external dependency**, not a file here:
  <https://github.com/josephararil/llm-chain>, pinned in `requirements.txt` to a moving `v1` tag.
  Never re-add or vendor `llm_chain.py`; a change to the LLM layer is made in that repo and
  reaches this one on the next run with no edit here. This repo is its **proving consumer**: a
  moved `v1` must go green here before it reaches anything else.

## Git workflow

**Auto-merge is authorized.** Feature branch → PR → once the offline tests pass locally, merge it
yourself (`gh pr merge <n> --merge --delete-branch`). Never commit directly to `main`.

## What this is

A weekly concierge email for a household near Plovdiv, Bulgaria: two adults (Joseph and Marti)
and a 4-year-old (Sophie). English-speaking, no TV or local news, and Joseph doesn't speak
Bulgarian, so the city is effectively illegible to them. Things happen 400m from the flat and
nobody hears about them.

Every Friday it emails one warm **soft itinerary** for two audiences at once: a weather line, then
four **time-first** sections: *This weekend*, *During the week*, *Looking ahead* (2–4 weeks), and
*Good to know* (local civic facts plus rotating evergreen ideas). Passive by design: the owner
only reads the email. No server, no database; JSON state is committed back by CI.

### What counts as valuable

**The most valuable item is one that makes the owner think "huh, I didn't know about that, that's
great", and then act on it.** Two tests: he didn't know about it (or hadn't thought of it), and
he's glad to learn it. Category is irrelevant. A road closure can pass; so can a puppet show. The
2026-10-16 email surfaced a puppet-theatre show, the family had nothing planned, and they went the
next day. That is the benchmark.

The corollary: **a wrong item is worse than a missing one.** The same email sent them to an air
show "on Saturday" that was actually on Friday; only a manual double-check saved a trip to an
empty museum. Trustworthiness is what makes the email worth acting on.

Earlier docs (and `docs/history.md`) describe civic items as the single most valuable output.
That was wrong; judge by the two tests above.

**Why time-first, not audience-first:** family items cluster on weekends and adult items on
weekdays, so time separates them for free. Audience headers with uncapped volume would render
fifteen adult items above three family ones.

Style ethos: small, flat, readable scripts. A framework or indirection layer added to save a few
lines is probably wrong here.

## Pipeline

**Four LLM calls per run:** two FIND, one SKEPTIC, one CONCIERGE.

```
weekend_concierge.py
  ├─ Load memory (state/memory.json) + feedback (preferences.md) + taste (taste.md)
  │             + weather (weather.py, 7 raw days) ; window = today+1 .. today+WEEK_WINDOW_DAYS
  ├─ HARVEST   scrapers.py — run every enabled source, per-source failure → [] (never crashes)
  │             A structured parser returning 0 items is named in weekend_log.md.
  │
  │             Every stage below calls llm_chain.call_llm() → LLMResult; each result is
  │             recorded in stage_results so the run can report its own degradation.
  ├─ Stage 1a · FIND FAMILY (search)  FIND_FAMILY_PROMPT + STAGE1_FAMILY_SCHEMA → family_fit.
  │             Categories: event_this_weekend | event_thisweek | event_lookahead | evergreen
  ├─ Stage 1b · FIND ADULT  (search)  FIND_ADULT_PROMPT + STAGE1_ADULT_SCHEMA, calibrated by
  │             taste.md → adult_fit (culture) OR civic_value (local facts), never both, plus a
  │             language_barrier field. Categories: the four above + civic_opportunity |
  │             civic_notice. candidate_id is assigned AFTER the merge.
  ├─ Stage 2 · SKEPTIC   (search) ONE batch call over the merged pool: verify existence + date
  │             + 90-min radius → keep | correct | kill, plus a `verified` bool (not a gate).
  │             Its search's grounding domains (LLMResult.sources) audit each note's cited
  │             domains → `SRC:` tags + one summary line in weekend_log.md (log only).
  ├─ Language gate       language_barrier == "blocking" → dropped, before any floor check
  ├─ Routine-maintenance drop  recurring municipal rounds (street washing etc.), title match
  ├─ Past-event drop     event whose end_date_iso (else date_iso) precedes the window start,
  │             re-checked AFTER SKEPTIC corrections (event_already_past)
  ├─ Expired-notice drop civic_notice whose end_date_iso precedes the window start
  ├─ Score floors        family 50 · adult 70 · civic_opportunity 75 · civic_notice 55 ·
  │             evergreens EXEMPT. A missing score is 0 and fails every floor.
  ├─ Five pools          family_events · family_evergreens · adult_events · adult_evergreens ·
  │             civic_items — every downstream consumer is driven from exactly these
  ├─ Anti-repeat filter  state/signals_seen.json — three cooldowns by key prefix (table below)
  ├─ Venue cap           at most MAX_EVENTS_PER_VENUE (2) events per venue per audience
  ├─ Stage 3 · CONCIERGE (no search) writes ONE email in four time-first sections. Each
  │             candidate carries `audience`, real-or-built links (build_links) and, if dated,
  │             a calendar_url (build_calendar_url).
  ├─ Degraded banner     degraded_summary(): a stage that FAILED or was TRUNCATED prefixes the
  │             subject "[degraded] " and opens the email with a banner. A stage that merely
  │             FELL BACK to a later model gets a log line only.
  ├─ Subject prefix      "[concierge] " prepended in Python, ahead of any "[degraded] "
  ├─ Email               ALWAYS sends Friday. Cooldown state (signals_seen.json, memory.json)
  │             is saved only AFTER a successful send; a failed send exits 1.
  └─ Writes state/: weekend_signals.json, weekend_log.md, memory.json/.md, signals_seen.json
```

### Category → score field → floor → anti-repeat key

| category | audience | score field | floor | key | cooldown |
|---|---|---|---|---|---|
| `event_this_weekend` | family | `family_fit` | 50 | `slug(title)\|date\|thisweekend` | 21d |
| `event_thisweek` | family | `family_fit` | 50 | `slug(title)\|date\|thisweek` | 21d |
| `event_lookahead` | family | `family_fit` | 50 | `slug(title)\|date\|lookahead` | 21d |
| `evergreen` | family | `family_fit` | **exempt** | `evergreen\|slug(name)` | 70d |
| `event_this_weekend` | adult | `adult_fit` | 70 | `slug(title)\|date\|thisweekend` | 21d |
| `event_thisweek` | adult | `adult_fit` | 70 | `slug(title)\|date\|thisweek` | 21d |
| `event_lookahead` | adult | `adult_fit` | 70 | `slug(title)\|date\|lookahead` | 21d |
| `evergreen` | adult | `adult_fit` | **exempt** | `evergreen\|adult\|slug(name)` | 70d |
| `civic_opportunity` | adult | `civic_value` | 75 | `civic\|slug(title)` | 180d |
| `civic_notice` | adult | `civic_value` | 55 | `slug(title)\|date\|civicnotice` | 21d |

`prune_seen()` picks the cutoff by prefix: `evergreen|` → 70d, `civic|` → 180d, else 21d. The two
civic categories split on **durability**, not date-presence: a new mall stays true for months, a
road closure does not.

## Files

| File | Role |
|---|---|
| `weekend_concierge.py` | The pipeline. `role_for`/`score_field`/`score_of`/`floor_for`/`civic_key`/`evergreen_key` own the routing table; `filter_seen()` applies cooldowns; `select_events()` ranks and applies the venue cap; `is_recurring_maintenance()`, `event_already_past()`, `civic_notice_expired()` are the structural drops; `clean_source_url()`, `build_links()`, `build_calendar_url()`, `parse_when_times()` build links; `save_json()`/`load_json()` are the UTF-8-safe state IO; `degraded_summary()` builds the banner; `note_domains()`/`source_domains()`/`audit_source()`/`source_audit_summary()` own the source audit. |
| `scrapers.py` | Two-tier per-source registry: `RAW_FETCH_SOURCES` (page-text blob for FIND) plus `SCRAPERS` (structured upgrade, falls back to raw on empty/failure). `harvest()` runs everything, dedupes on title+date, interleaves round-robin, caps at `MAX_HARVEST_ITEMS`. Most news sources are RSS and share `_parse_news_rss()`. Every source's quirks and every rejected source are in `docs/history.md` and in the comment above each parser. |
| `config.py` | Knobs, `ENABLED_SOURCES`, seed evergreens (42 family + 10 adult priors), per-audience prompts and schemas. **Names no model.** |
| `memory.py` | Evergreen catalog + suggestion ledger. `summarize_for_prompt(mem, audience)` filters the catalog per audience. Entries default to `audience="family"`, which is how pre-audience entries read correctly without migration. |
| `weather.py` | open-meteo, no key. `week_weather()` returns raw per-day dicts; `DAY_FIELDS` names the keys `format_weather()` reads (a one-sided rename prints `?` into the prompt and the model invents weather). |
| `common.py` | `send_email()` and state helpers, copied from deal-hunter. Leave it alone (see COMMON-PY-UNTOUCHED). |
| `preferences.md` | Hand-edited family feedback and factual exclusions (closed or unsafe places), injected into prompts. |
| `taste.md` | Hand-edited adult calibration: 16 scored exemplars, no percentage weights. Exemplars govern. Editing it is safe and expected. |
| `.github/workflows/weekly.yml` | Friday 06:00 UTC, fixed (no DST logic). Commits `state/` only if the run succeeded. |
| `test_*.py`, `tests/fixtures/` | Fully offline. `test_concierge.py` runs `main()` end to end with stubbed LLM/harvest/weather/email. |

## Critical invariants — do not break

Long-form evidence for each is in `docs/history.md`.

**Architecture**
- **All LLM calls go through `llm_chain.call_llm()`; all email through `common.send_email()`.** Never call `common.llm()`: it is the un-chained path that produced the blank email of 2026-08-14 (and now raises `AttributeError` here, which is intended).
- **Two FIND paths, never one widened brief.** The family path's quality comes from being narrow. Each path fails independently to `[]`; `candidate_id` is assigned after the merge.
- **Scrapers never crash the run.** Every source in try/except → `[]`.
- **HARVEST-CAP-FAIRNESS:** the cap interleaves per source (`_round_robin()`), never concatenate-then-slice. A naive slice once zeroed every newly added source.
- **Model selection lives in llm-chain's defaults, overridden by `LLM_*` repo variables.** No model name in this repo. **NO-SILENT-MODEL-DEFAULT**, **FAIL-FAST-ON-CLIENT-ERROR** and **BUDGET-VALVE** are llm-chain's invariants; its README is the authority.
- **COMMON-PY-UNTOUCHED.** Nothing here needs `common.py` changed; its dead LLM half must not be deleted (other repos call their own copies). It is *not* byte-identical across repos; don't cite that, and don't "resync" the copies.

**SKEPTIC**
- **SKEPTIC only removes or corrects.** It must not invent items or judge suitability; desirability belongs to FIND/CONCIERGE.
- **CIVIC-RADIUS:** never kill a `civic_opportunity` on radius (a flight route has no venue). Radius still applies to `civic_notice`.
- **VERIFIED-NOT-A-GATE:** `verified` is a visibility flag (`UNVERIFIED` in the log), never a filter. Gating would drop thinly reported real items.
- **SKEPTIC-INFRA-IS-NOT-REJECTION:** no verdicts + `ok=False` (never ran) keeps candidates flagged unverified; no verdicts + `ok=True` kills them. Conflating these either empties the email on an outage or silently disables the hallucination guard.
- **SOURCE-AUDIT-NOT-A-GATE:** `source_audit` (`ok`/`unsourced`/`no-domain`) is log-only, like `verified`. No sources means *unauditable* (log says `skipped`), never *unsourced*. Read `sources` with `getattr` inside SKEPTIC's `try`: an AttributeError there kills every candidate. Making the audit a gate is the owner's call.

**Filtering**
- **LANG-GATE:** `language_barrier == "blocking"` drops before the floor check; `"partial"` gets no numeric penalty in Python (the prompt already scores it lower).
- **PAST-EVENT:** `event_already_past()` runs after SKEPTIC corrections and uses `end_date_iso`, else `date_iso`. Removing it re-opens the "air show on the wrong day" failure.
- **EXPIRY-NEEDS-END-DATE:** `civic_notice_expired()` reads `end_date_iso` only, never `date_iso` (a start date can't say something ended). Missing end date keeps the item.
- **ROUTINE-MAINTENANCE:** recurring municipal rounds are dropped in Python on title only, within civic categories. They re-key weekly, so cooldowns can never suppress them.
- **EVERGREEN-EXEMPT:** `floor_for()` returns `None` for evergreens. That is what guarantees a non-empty email.
- **Each candidate carries exactly ONE score** (`family_fit` / `adult_fit` / `civic_value`). A missing score reads as 0 and fails every floor.
- **SORT-KEY:** rank by `score_of(c, audience)`, never `c.get("family_fit", 0)`, which silently zeroes every adult/civic item.
- **`role_for()` raises on an unknown category.** Don't soften it to `.get()`: the resulting key never matches, so the item re-sends forever.
- **PREFIX:** `prune_seen()` tests `startswith("civic|")` with the pipe, or "Civic Center Opening" gets a 180-day cooldown.
- **Family evergreen keys stay `evergreen|slug(name)`** (pre-audience form) so the live rotation isn't reset.

**Email**
- **EMAIL-BEFORE-STATE:** cooldown state is saved only after `send_email` succeeds, and a failed send exits 1. Saving first put a whole week on cooldown unseen, on a green job.
- **The email always sends on Friday.** Evergreen fallback guarantees content in both audiences.
- **One email, four time-first sections**, never audience headers.
- **SCORES-INTERNAL:** scores never appear in email copy. Warm prose, not a scoreboard or an hour-by-hour plan.
- **Links are real or built, never invented.** **NO-BARE-DOMAIN-LINKS:** `clean_source_url()` strips a path-less URL to `""`, promoting the built search link.
- **CALENDAR-IS-THE-CTA:** every dated item gets an Add-to-calendar link. Undated items get `""` (never fabricate one). Timed only when `when_text` yields a start time and the item is single-day. All-day end dates are **exclusive** in Google's format (a 26–27 event sends `20260926/20260928`). Local times go with `ctz=Europe/Sofia`; no DST arithmetic here.
- **Weather is raw data, not a label,** and gates family items only. Adult and civic items ignore it.
- **Bulgarian in, English out.**
- **UTF-8-STATE-IO:** state goes through `weekend_concierge.save_json()`/`load_json()`, never `common`'s pair (no `encoding=`, writes cp1252 on Windows, CI can't read it back).
- **SUSPECT-THE-PROMPT-FIRST:** when a stage looks weak, fix the prompt before reaching for a bigger model. Pro was tried and dropped: more cost, more timeouts, no better output.

## Knobs (`config.py`)

| Knob | Value | Note |
|---|---|---|
| `WEEK_WINDOW_DAYS` | 7 | Run-day+1 .. +7. On Friday that is Sat..next Fri. |
| `MIN_INCLUDE_SCORE` | 50 | `family_fit` floor. |
| `MIN_ADULT_SCORE` | 70 | `adult_fit` floor; first guess, tune from `[LOW-FIT ]` log lines. |
| `MIN_CIVIC_OPPORTUNITY_SCORE` | 75 | High bar for durable facts. |
| `MIN_CIVIC_NOTICE_SCORE` | 55 | Low bar for time-bound notices. |
| `CIVIC_OPPORTUNITY_COOLDOWN_DAYS` | 180 | A new mall is interesting once. |
| `MAX_EVENTS_PER_VENUE` | 2 | Per audience. Over-cap items aren't marked seen, so they return later. |
| `MAX_HARVEST_ITEMS` | 350 | Was 200, which cut 282 items. A guard, not a filter. |
| `MAX_TOKENS_SKEPTIC` / `_CONCIERGE` | 20000 / 24000 | Load-bearing: overflow truncates JSON. Truncation now raises the degraded banner. |

**Tuning floors:** too tight fails into a thin section (acceptable); too loose fails into spam
(loses trust permanently). Bias tight. Floors are the only volume control.

## Secrets and variables

| Name | Type | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | secret | Gemini calls (default provider) |
| `ANTHROPIC_API_KEY` | secret | If `LLM_PROVIDER=anthropic` |
| `SMTP_HOST/PORT/USER/PASS` | secrets | Email delivery |
| `EMAIL_TO` / `EMAIL_FROM` | secrets | Recipient / sender (default SMTP_USER) |
| `LLM_PROVIDER` | variable | `gemini` (default) or `anthropic` |
| `LLM_MODEL_CHAIN` | variable | Reasoning models, left to right. **Production: `gemini-flash-latest,gemini-3.1-flash-lite`** |
| `LLM_SEARCH_MODEL_CHAIN` | variable | Search models. **Production: same as above** |
| `LLM_ATTEMPTS_PER_MODEL` | variable | **Production: 3.** Lower this before raising backoff: advancing beats waiting on a shedding model. |
| other `LLM_*` | variables | Backoff, timeouts, statuses, total budget (1200s). All passed through `weekly.yml`; semantics in llm-chain's README. |

The production chain uses `-latest` so no numbered model needs bumping by hand (owner's call,
2026-10-18). Known cost: `-latest` resolves to the newest flash, which was the one shedding load
in August. If 503 fallbacks become routine in `weekend_log.md`, put a numbered flash between the
two.

## Running locally

```bash
pip install -r requirements.txt   # required: llm_chain installs from git
python weekend_concierge.py       # needs GEMINI_API_KEY; without SMTP it prints the send error,
                                  # writes the log, saves NO cooldown state and exits 1
python scrapers.py                # harvest smoke test, per-source counts
python weather.py                 # 7-day forecast
python -m llm_chain               # resolved config + reachable models + live ping
python test_concierge.py && python test_scrapers.py && python test_memory.py   # offline
```

`.env` is read by `weekend_concierge.py` only. `python -m llm_chain` reads the process
environment, so export the key first; otherwise it reports `HTTP 403 ... unregistered callers`,
which looks like a dead key.

## Known trade-offs (accepted; don't "fix" without asking)

- **Scraping is brittle**; Facebook is a stub. FIND's web search compensates partly.
- **No headless browser, ever.** It has been ruled out three times; `eventim.bg`, `ticketstation.bg` and every Plovdiv cinema site need one, so cinema arrives only via FIND's web search.
- **SKEPTIC's source audit is domain-level.** One batch search serves every candidate, so `ok` means the cited domain was among what the search returned, not that it confirmed this item; `unsourced` is the strong signal. SKEPTIC is deliberately not shown the source list (it would just cite from it). The Anthropic path returns no sources, so the audit skips.
- **No cross-path dedupe:** one festival found by both FIND paths uses two slots.
- **Civic geography is inferred from "central Plovdiv"**, not configured streets.
- **The family path spans 7 days** (owner's call); weekday kids' events add some noise.
- **90-min radius**, with `civic_opportunity` the one exception.
- **Childcare isn't modelled**; never downrate an adults-only evening for it.
- **A recurring weekly fixture is an `evergreen`**, not an event (a 21-day event cooldown would send it ~17×/year).
- **A moved `v1` tag reaches production with no per-repo review**; the mitigation is llm-chain's release flow.
- **llm-chain's fallback chain is Gemini-only**; Anthropic gets retries but no cross-model fallback.
- **`memory.py` duplicates `EVERGREEN_COOLDOWN_DAYS = 70`** from `config.py`. Latent drift risk, not fixed.
- **No anglophone media calendar, no dynamic score floor, no ticket/price data.** Deliberately out.

## Out of scope (only on explicit request)

Booking/ticket purchase, a web/PWA front-end, reply-to-email feedback parsing.

## Style

Flat functions, stdlib + `requests` + BeautifulSoup, short modules, comment only the non-obvious.
Prefer editing in place over new files. No emoji in code; `weekend_log.md` and email HTML may use
them.
