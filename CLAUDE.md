# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Build status

**Live.** The full pipeline is built, merged to `main`, and running weekly on GitHub Actions
(first real run succeeded). All modules exist and are wired: `common.py`, `scrapers.py`,
`weather.py`, `memory.py`, `config.py`, `weekend_concierge.py`,
`.github/workflows/weekly.yml`, `preferences.md`, `taste.md`. Ongoing work is **iteration, not
construction**: tuning the prompts, and adding/upgrading scrapers (raw-fetch → structured) as new
sources prove worthwhile.

**`llm_chain` is no longer a file in this repo.** It was extracted to
<https://github.com/josephararil/llm-chain> and is now an installed dependency, pinned in
`requirements.txt` to a **moving `v1` tag**. `llm_chain.py` and `test_llm_chain.py` are gone from
here; do not re-add them, and do not vendor a copy back — copying it into N repos is the exact
problem the extraction solved (`common.py` was documented as "byte-identical" across three repos
and had already drifted into three different files). A change to the LLM layer is made in that
repo, released per its README, and picked up here on the next scheduled run with **no edit to
this repo**. weekly-concierge is llm-chain's designated **proving consumer**: it is the pipeline
that must go green before a moved `v1` reaches anything else.

**Its fallback path is confirmed against the live API** — see the 2026-08-14 verification runs
below. Both halves fired: the forced-fallback drill (a bogus first model returning 404) and,
unprompted, a real 503 exhaustion on the concierge stage. That verification was done here, from
this repo's workflow, *before* the extraction, and it is the reason the move could be made without
re-proving the chain. Re-running the drill is now an upstream job:
`LLM_MODEL_CHAIN=zzz-not-a-model,gemini-flash-latest python -m llm_chain`.

The **dual-audience** split (adult culture + local civic facts alongside the family
recommendations) has now been judged against a real email — see the same section. The floors —
`MIN_ADULT_SCORE = 70` especially — are still deliberate first guesses and expect tuning against
the `[LOW-FIT ]` lines in `state/weekend_log.md` after ~3 weeks of real runs. One good email is
not calibration.

- `plan.md` captures the original design rationale and the intended end-state; consult it when
  a change might conflict with a core design decision.
- `common.py` is deal-hunter's infrastructure reused verbatim by design — don't modify it here.

## What this is

A personal weekly concierge for a household near Plovdiv, Bulgaria: two adults and a 4-year-old.
The household is English-speaking with no TV/newspapers and little local plug-in, and the adult
who reads this email doesn't speak Bulgarian — so it is effectively **illegible to its own city**.
Things happen 400m from the flat and nobody hears about them.

This pipeline runs weekly on free GitHub Actions and emails one warm, curated **soft-itinerary**
every Friday, serving **two audiences in one email**: (0) a short weather-at-a-glance grounding
for the week, then four **time-first** sections — (1) *This weekend*, (2) *During the week*,
(3) *Looking ahead* (2–4 weeks out), and (4) *Good to know*: local civic facts (a new store, a
road closure, a water outage, a derby that will snarl the city) plus the rotating evergreen ideas
(zoo, museums, rowing channel, galleries, viewpoints). Passive by design — the user only reads the
email. No server, no database; JSON state committed back by CI.

**Why time-first and not audience-first.** Family items cluster on the weekend and adult items on
weekdays, so a time split separates them for free. Audience-first headers plus uncapped volume
would render fifteen adult items above three family ones and read as a demotion of the half that
already works.

**The civic half is the highest-value part**, by the owner's own account: a single email warning
him of a closure on a road he drives is "worth a year's worth of everything else combined". It is
also the least reliable — see Known trade-offs.

Same Pareto ethos as its sibling: small, flat, readable scripts over clever abstractions. If a
change adds a framework or a layer of indirection to save a few lines, it's probably wrong here.

## Pipeline (see PLAN.md for the full diagram)

**Still four LLM calls per run**: two FIND, one SKEPTIC, one CONCIERGE.

```
weekend_concierge.py
  ├─ Load memory (state/memory.json) + feedback (preferences.md) + taste (taste.md)
  │             + weather (weather.py, 7 raw days) ; window = today+1 .. today+WEEK_WINDOW_DAYS
  ├─ HARVEST   scrapers.py — run every enabled source, per-source failure → [] (never crashes)
  │
  │             Every stage below calls llm_chain.call_llm() → LLMResult; each result is
  │             recorded in stage_results so the run can report its own degradation.
  ├─ Stage 1a · FIND FAMILY (llm_chain, search)  FIND_FAMILY_PROMPT + STAGE1_FAMILY_SCHEMA
  │             → candidates scored family_fit. Categories: event_this_weekend |
  │               event_thisweek | event_lookahead | evergreen
  ├─ Stage 1b · FIND ADULT  (llm_chain, search)  FIND_ADULT_PROMPT + STAGE1_ADULT_SCHEMA,
  │             calibrated by taste.md → candidates scored adult_fit (culture) OR civic_value
  │             (local facts), never both, plus a language_barrier gate field. Categories: the
  │               four above + civic_opportunity | civic_notice
  │             ── TWO paths, not one widened brief: the family path's quality comes from being
  │                narrow. Each path fails independently to [] so one dead path can't cost the
  │                other. candidate_id is assigned AFTER the merge.
  │
  ├─ Stage 2 · SKEPTIC   (llm_chain, search) ONE batch call over the merged pool:
  │             verify real existence + date + 90-min radius → keep | correct | kill.
  │             Does NOT judge suitability for anyone, and never applies the radius test to
  │             civic_opportunity (a new flight route has no venue). Also returns `verified`
  │             (bool) per candidate — separate from the verdict, NOT a gate; see below.
  │             ── If it returns NO verdicts, the cause matters: res2.ok is False (never
  │                ran) keeps the candidates, flagged verified=False; res2.ok is True with an
  │                empty parse kills them as before. See SKEPTIC-INFRA-IS-NOT-REJECTION.
  ├─ Language gate       language_barrier == "blocking" → dropped, before any floor check
  ├─ Routine-maintenance drop  recurring municipal maintenance rounds (street washing etc.)
  ├─ Expired-notice drop       civic_notice whose end_date_iso precedes the window start
  ├─ Score floors        family 50 · adult 70 · civic_opportunity 75 · civic_notice 55 ·
  │             evergreens EXEMPT. A missing score is 0 and fails every floor.
  ├─ Five pools          family_events · family_evergreens · adult_events · adult_evergreens ·
  │             civic_items — every downstream consumer is driven from exactly these
  ├─ Anti-repeat filter  state/signals_seen.json — three cooldowns by key prefix (see below)
  ├─ Stage 3 · CONCIERGE (llm_chain, no search) writes ONE email in four time-first
  │             sections from survivors + scores + weather + feedback + taste + memory.
  │             Each candidate carries `audience` and actionable links: real source_url + a
  │             Google Maps link and a search link built deterministically (build_links) so no
  │             URL is invented. A bare-domain source_url is stripped to "" first
  │             (clean_source_url), which promotes the search link.
  ├─ Memory write        ledger per candidate; grow evergreen catalog (audience-scoped); prune
  ├─ Degraded banner     degraded_summary(stage_results) — if any stage FAILED, prefix the
  │             subject "[degraded] " and open the email with a "this email is incomplete"
  │             banner. A stage that merely FELL BACK to a later model is the chain working:
  │             it gets a weekend_log.md line only, never a banner.
  ├─ Email               ALWAYS sends Friday (weekly ritual; evergreen guarantees content)
  └─ Always writes state/: weekend_signals.json, weekend_log.md, memory.json/.md, signals_seen.json
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

`prune_seen()` picks the cutoff by key prefix: `evergreen|` → 70d, `civic|` → 180d, otherwise 21d.

The two civic categories split on **durability, not date-presence** — a new mall stays true for
months; a road closure and a derby do not. Disruptions and awareness items behave identically, so
they share `civic_notice`.

## Files

| File | Role |
|---|---|
| `llm_chain` (**external**) | **The LLM layer — an installed dependency, not a file here.** Lives at <https://github.com/josephararil/llm-chain>; `requirements.txt` pins `llm-chain @ git+…@v1`. Imported unchanged as `import llm_chain as L`, which is why the package deliberately ships **one top-level module** (`py-modules = ["llm_chain"]`) rather than a package directory — a `src/` layout or an `llm_chain/` package would rename the import for every consumer. `call_llm()` → `LLMResult`, plus `parse_json_block()`, `resolved_provider()`, `available_models()`, `resolved_config()`. A two-level loop: retry the same model on a transient status, then advance to the next model in the chain. **Imports only `os/json/time/dataclasses/typing/requests` — never `config`, never `common`, never any project-specific name.** That constraint is what made it liftable, and it still binds: anything project-specific belongs in the caller, since the module is now shared by unrelated pipelines. It is the **only place a model name appears anywhere in the fleet**. All 11 knobs are `LLM_*` env vars read at *call* time (not import), defaulting in that module. Smoke test: `python -m llm_chain` (was `python llm_chain.py` before extraction). Its 17 offline tests now run in **its own** CI, not here. |
| `common.py` | `send_email()`, state IO (`load_json`/`save_json`/`today_iso`), and `parse_json_block()`. Its LLM half — `llm()`, `_gemini()`, `_gemini_search()`, `_anthropic()`, `_post_with_retry()`, `resolved_provider()` — is **dead in this project** since `llm_chain` landed, and **must not be deleted**: deal-hunter and shopping-assistant still call those functions from their own copies. `parse_json_block` is duplicated between `common.py` and `llm_chain` for the same reason. **Copied from deal-hunter — do not modify beyond the deliberate exception below.** `send_email()` raises `SMTPRecipientsRefused` if `smtplib.send_message()` returns any refused recipients — `send_message()` only raises on *total* failure, so a multi-address `EMAIL_TO` (e.g. `"a@x.com,b@x.com"`) could otherwise have one address silently dropped with no error. |
| `scrapers.py` | **Landed.** Two-tier per-source registry (raw-fetch default + structured upgrade), `harvest()`, `fetch()`, `text_of()`, `bg_date()`. Structured parsers: `plovdiv2019.eu` (its own JS calendar just navigates to a server-rendered `?f_time=all&page=N` — see the docstring), `bilet.bg`, `ticket.bg` (homepage `div.productItem` cards; no year in the date string, so it assumes the next upcoming occurrence like `bg_date`; pre-filters to the Plovdiv-radius towns plus Sofia, and Sofia only when the event is ≥14 days out), `programata.bg` (`scrape_programata` — Kids category page — and `scrape_programata_adult`, which reuses the identical `_parse_programata` **unmodified** over four adult category pages listed in `PROGRAMATA_ADULT_URLS`: `/kino/`, `/muzika/kontserti-partita/`, `/izlozhbi/`, `/stsena/postanovki/`, each fetched in its own try/except so one dead category doesn't lose the others. `div.post-list-entry` yields 17/12/12/12 cards respectively — so the adult upgrade was a URL-list addition, not a new parser. **The pages are national with a strong Sofia skew** — Plovdiv-vs-Sofia mentions were 2/6 on concerts, 3/14 on exhibitions, 2/3 on theatre — and **no Plovdiv filter is applied here on purpose**: FIND and SKEPTIC own the radius judgement, as they already do for the Sofia-carrying `ticketbg` source. Low yield is expected and accepted at a marginal cost of one URL list. `div.post-list-entry` cards; the site is an editorial/magazine, not a calendar — listing cards have no date/venue field, only free-form prose inside each article, so `date_iso`/`location` are left unset for FIND/SKEPTIC to resolve), `visitplovdiv.com` (its "culture calendar" listing page itself renders empty — its own JS fills it in from an XML AJAX endpoint after load, so the parser calls that endpoint directly and parses XML, not HTML; `location` is left unset since it's only present as free-form prose inside `content`), `plovdiv.bg` (upgraded from HTML-scraping `/category/events/` to the site-wide WordPress RSS feed at `/feed/` — `wp-json` is disabled site-wide, confirmed 404 with a WordPress-branded 404 page rather than a CDN block, both for `/wp-json/` and `/wp-json/wp/v2/categories`, but `/feed/` and `/category/events/feed/` both 200 with real RSS; `/feed/` (the broader one) was chosen because the events-only category never carries the Транспорт/Актуално posts — bus reroutes, road closures, water-main work — that are exactly the highest-value civic_notice content this product wants, and the feed gives a real per-item permalink for free instead of an HTML card scrape. Shares `_parse_news_rss` — see below), `trafficnews.bg` (a national outlet, but its own `/plovdiv/` category — found via a manual href scan of its homepage, not a documented API — publishes a dedicated per-category RSS feed with no `pubDate` field at all, so `date_iso` relies entirely on `bg_date()` finding a date in the short teaser, which is rare; one fetch returns 50 items, no pagination needed), `podtepeto.com` (a dedicated Plovdiv news site — "podtepeto", lit. "under the hills", is itself a Plovdiv nickname — confirmed by the densest signal of every candidate evaluated: 95 Plovdiv mentions across just 10 items in its own default WordPress feed), `dcnews.bg` (its own title tag reads "Новини от Пловдив, Асеновград и региона" — genuinely regional, not just branded; its default feed already carried a water-main reconstruction, toll-camera lane restrictions on the Тракия/Хемус motorways, and a wildfire near Hisar, all inside the ~90-min radius), and `plovdivnews.bg` (its top-level `/feed/` is a general Bulgarian news portal that only happens to be Plovdiv-branded — world news, horoscopes — so the parser instead targets its `/category/plovdiv/feed/`, which is real local news; this overlaps some with `plovdiv_bg`/`dcnews` on the same underlying stories independently written up, which is harmless — `harvest()`'s title+date dedupe absorbs it). All five of the above share one helper, `_parse_news_rss(xml, source, today)`, since they all emit the same RSS 2.0 shape (title/link/description, optionally a richer `content:encoded`) regardless of platform; it prefers `content:encoded` over `description` when present (podtepeto's feed carries the *full article body* there for free — e.g. a kids' LEGO workshop's actual time window and age range — while `description` alone is a one-sentence teaser too thin to extract that from, with no extra per-item fetch needed, unlike `lostinplovdiv`'s detail-fetch enrichment) and strips the "Материалът ... е публикуван за пръв път на ..." ("this post was first published on...") footer several of these sites' RSS plugins append to every item, which is pure noise since the real URL is already in `<link>`. And `lostinplovdiv.com` (`/en/` front-page feed, `post-item` cards; a hand-curated bilingual city guide, not a calendar. The site retired its old `/en/articles` archive listing — now a 404 — after a theme change to Jannah/WordPress, so the parser targets the front-page feed instead: no pagination param needed since one fetch already returns ~50+ cards newest-first, sliced to the newest 30. Most articles are evergreen roundups or local trivia with no event date, left `date_iso=None` for FIND/SKEPTIC, except the recurring "What to do in Plovdiv (DD.MM - DD.MM)" weekly digest whose title embeds its own date range — that date is taken at face value in today's year rather than rolled forward, since it describes the current/just-finished week, not a future one; the listing's own one-sentence blurb is too thin for FIND to extract anything from an actual event/activity guide — e.g. a "which events in June" roundup collapses to a teaser with none of the dozen dates it lists — so `_lostinplovdiv_is_actionable()` heuristically flags titles that read as an activity guide (a numbered listicle, a "where is/are/to" question, or an event/activity keyword) versus pure local-history trivia, and only those get one extra fetch of the full article body (now selected via the `entry-content` class, also renamed by the theme change) via `_fetch_lostinplovdiv_detail()`, capped at `LOSTINPLOVDIV_MAX_DETAIL_FETCHES` extra requests per harvest). Two sources were investigated and deliberately kept raw-fetch: `eventim.bg` — its real event data comes from a JSON API (public-api.eventim.com/websearch/search/api/exploration/v1/productGroups) that 403s at Akamai's edge for every request regardless of correct params (reverse-engineered from the site's own JS), and the suggested `pyventim` fallback pulls in playwright/patchright/curl_cffi/scrapling — the exact heavy headless-browser stack this project avoids — so neither route was adopted (see the comment above `RAW_FETCH_SOURCES` for the full investigation); and `ticketstation.bg` — a client-rendered Vue SPA whose fetched HTML carries only nav/config JSON (no `<urbo-*>` event-listing component renders server-side), leaving no event markup a structured parser could select or be verified against. `ticketstation.bg` also intermittently 403s specifically from GitHub Actions' IP ranges (Cloudflare-fronted) while fetching fine from a residential/corporate network — most likely Cloudflare's bot defense flagging datacenter IPs rather than a code or markup problem; nothing to fix here short of the same headless-browser tradeoff already ruled out for `eventim.bg`. `plovdiv.bg`'s HTML route (`/category/events/`, no longer used) had the same CI-only 403; its RSS route (`/feed/`) does not — confirmed live from GitHub Actions itself via a temporary `workflow_dispatch` probe workflow (added, run, and deleted the same session), which also confirmed `trafficnews.bg`, `podtepeto.com`, `dcnews.bg`, `plovdivnews.bg`, and the three raw-fetch additions below all return real items from CI, not just locally. `eventim.bg`'s HTML route was separately retried with a longer timeout (40s, up from 15s) on the theory its earlier `ReadTimeout` might be transient network slowness rather than the documented Akamai API block — it is not: a `curl -v` trace shows Akamai's edge repeatedly forcing TLS renegotiation before stalling to a hard timeout with zero bytes received, the same edge-protection behavior as the documented API 403, just extended to the HTML route. Not solvable without the headless-browser stack this project has ruled out three times; left raw-fetch, unchanged. `tourist.stara-zagora.bg` was removed from the source list entirely — the domain no longer resolves (confirmed NXDOMAIN via public DNS), and its apparent replacement, `visitstarazagora.bg`, is a client-rendered SPA with no text in its raw HTML, so it wasn't worth adding as a substitute. Three more Plovdiv-adjacent aggregators were evaluated and rejected: `calendarbg.com` is not an independent source at all — its homepage text reads "POWERED BY: BILET.BG" and it has no visible event listing of its own (rendered via a JS widget), it's a promotional funnel for organizers built on top of `bilet.bg`, which this project already scrapes directly; `kulturni-novini.info` is a national culture-news aggregator with zero Plovdiv mentions on its homepage AND zero in its own August "Calendar" section (`/sections/12/news/list/August.2026`) — Sofia-centric with no city filter, same trade-off already documented for `programata.bg`'s non-Kids categories, just with no Plovdiv signal at all rather than a skew; and `novinata.bg` has no working `/category/plovdiv` (its `/category/plovdiv` redirects to `/plovdiv/`, which 404s) and zero Plovdiv mentions anywhere reachable from its homepage. None of the three are worth revisiting absent a structural change on their end (e.g. calendarbg.com's widget becoming server-rendered).

Three more sources were added raw-fetch-only (a plain page-text blob for FIND to parse, no structured parser) since their signal didn't justify a dedicated parser: `plovdiv-online.com` (a Plovdiv-branded outlet, "Новини от Пловдив", but its feed skews crime/tabloid/national-opinion, with only occasional real local content — e.g. a duplicate of the same Тракия/Хемус lane-restriction story `dcnews` also carries — so the signal-to-noise ratio didn't clear the bar for a dedicated parser); `plovdivtime.bg` (no RSS feed — `/feed/` 404s — but its „Ела и виж“ ("Come and See") section runs a genuinely useful recurring feature, a dated "Къде да отидем в <day>, Пловдив" ("Where to go on <day> in Plovdiv") digest visible directly on the homepage, but no feed and no per-item date/link markup to key a parser off); and `sphotel.net/blog` (a hotel's blog, low prestige, but its RSS feed matches exactly what the research lead predicted — recurring seasonal roundups like "17 free things to do in Plovdiv" and "Concerts in Plovdiv 2026" — the blurb in the feed is as thin as `lostinplovdiv`'s was before its detail-fetch enrichment, but the volume here (10 posts, mostly evergreen) didn't justify building the same machinery a second time). The rest of `RAW_FETCH_SOURCES` (`dtp.bg`, `rnhm.org`, `oldplovdiv.bg`, `marica.bg`, `plovdiv24.bg`) — `plovdiv24.bg` was registered but *disabled* until the civic feature needed it, and is now in `ENABLED_SOURCES` (verified 200, 111k chars) — simply haven't been evaluated for a structured upgrade yet — no investigation, just page-text blobs FIND parses; upgrade only if one proves worth the maintenance cost. `scrape_facebook` is a documented stub (raises `NotImplementedError`, caught by `harvest()`) — no auth/anti-bot handling yet. `config.ENABLED_SOURCES`/`MAX_HARVEST_ITEMS` turn sources on/off and cap volume. Adding a raw-fetch source is a one-line entry in `RAW_FETCH_SOURCES` + `ENABLED_SOURCES`. `harvest()`'s volume cap interleaves per-source results round-robin before truncating to `MAX_HARVEST_ITEMS` (`_round_robin()`) rather than concatenating and slicing — a plain-concatenation cap let whichever sources are listed first in `ENABLED_SOURCES` eat the entire budget, which silently zeroed out every source added after it the moment total harvest volume first crossed 200 (found while adding the four RSS sources above: pre-cap volume went from ~180 to 320, and none of the new sources survived the cap at all). Tests: `test_scrapers.py` (offline, mocks network; fixture-backed parse tests in `tests/fixtures/` cover twelve structured sources; a dedicated regression test confirms a high-volume source can't starve a low-volume one under the cap). |
| `weather.py` | open-meteo (no key) → `week_weather(latlon, today, days=7)` returns a **list** of raw per-day forecast dicts for `today+1 .. today+days`, ordered by date (max/min temp, feels-like, humidity, cloud cover, chance of rain, condition, 3-letter `label`), passed to CONCIERGE as-is so the model reasons over the actual numbers rather than a pre-classified label. `[]` on any failure; never raises. `DAY_FIELDS` names the emitted keys in one place because `weekend_concierge.format_weather()` reads them by name with `.get(k, '?')` — a one-sided rename would print `?` into a live prompt and the model would invent plausible weather, so `test_concierge` builds its stub from `DAY_FIELDS` to bind the two sides. (Replaced `weekend_weather`, which returned a `{"Sat":…, "Sun":…}` dict.) |
| `memory.py` | `load/save/prune/summarize_for_prompt`; evergreen catalog + suggestion ledger. Evergreen entries carry optional `url` (official page → real "Details" link when emailed), `practical` (hours/fees/season/safety note → injected into prompts), and `audience` (`"family"` or `"adult"` — which FIND path owns the entry); all preserve-on-missing across upserts. `summarize_for_prompt(memory, audience="family")` filters the catalog by audience, so `MAX_PROMPT_EVERGREENS = 10` is now 10 *per audience* and neither starves the other. **`audience` defaults to `"family"` everywhere**, which is what let the 42 pre-existing catalog entries read correctly with no migration — and because `"family"` is a truthy default it acts as the preserve-on-missing sentinel (the `x or existing.get(...)` idiom used for `url`/`practical` cannot work here; see the comment in `record_evergreen`). |
| `config.py` | Knobs, source registry, seed evergreens, prompts, schemas. **Names no model** — the `# ── LLM models ──` block is deliberately empty and points at `llm_chain.py`. It keeps `PROVIDER_FIND/SKEPTIC/CONCIERGE` (all `None`, passed through as `provider=`), `MAX_TOKENS_*`, `WEB_SEARCH_MAX_USES` and `SEARCH_RESULTS_PREAMBLE`. `SEED_EVERGREEN` holds 42 family entries (5 original + ~37 `source="research"` from a Gemini Deep Research sweep of family attractions within a ~90-min drive of Plovdiv), each with optional `url`/`practical`. `SEED_EVERGREEN_ADULT` holds **10 unvalidated priors** (`source="seed-adult"`) so the adult section is never bare in the first weeks — edit or replace them freely, nothing depends on any single entry. Both are seeded into `state/memory.json` on the first run where the name is absent (52 total), the adult ones with `audience="adult"`. Prompts are per-audience: `SEARCH_FAMILY_PROMPT`/`SEARCH_ADULT_PROMPT`, `FIND_FAMILY_PROMPT`/`FIND_ADULT_PROMPT`, `STAGE1_FAMILY_SCHEMA`/`STAGE1_ADULT_SCHEMA`. |
| `weekend_concierge.py` | The pipeline (HARVEST→FIND×2→SKEPTIC→language gate→routine-maintenance drop→expired-notice drop→floors→anti-repeat→CONCIERGE→email). `role_for`/`score_field`/`score_of`/`floor_for`/`civic_key`/`evergreen_key(name, audience)` own the routing table above; `filter_seen()` applies the per-category cooldowns; `is_recurring_maintenance()` and `civic_notice_expired()` are the two structural civic drops; `clean_source_url()` strips bare-domain citations; `build_links()` builds each candidate's `(source_url, maps_url, search_url)` before the concierge call. `save_json()`/`load_json()` are this project's UTF-8-safe replacements for the `common` pair (see the UTF-8-STATE-IO invariant); `load_json` falls back to the platform encoding on a `UnicodeDecodeError` so a legacy cp1252 state file is read rather than silently discarded, and the next save rewrites it as UTF-8. `degraded_summary(stage_results)` returns the `(subject_prefix, banner_html, banner_text, log_line)` for a run where a stage failed outright — `stage_results` is a list of `(stage_name, LLMResult)` accumulated at each of the four call sites. Tests: `test_concierge.py` (offline, stubs `llm_chain.call_llm`/`scrapers.harvest`/`weather.week_weather`, runs `main()` twice to verify state files + suppression + per-audience evergreen rotation, plus unit tests for the `civic\|` prefix, schema↔`role_for` coverage, weather field names, audience isolation, the score sort key, bare-domain stripping, the maintenance and expiry drops, that an unverified item is sent *and* flagged, the SKEPTIC infra-vs-empty split, and UTF-8 state IO). |
| `preferences.md` | Hand-edited feedback ("Loved / Not interested / Constraints"), injected into prompts. Constraints also carry factual exclusions (Aqualand closed; Asen's Fortress / Kuklen Waterfall / Belintash too dangerous for a 4-year-old) so FIND/CONCIERGE never propose them. |
| `taste.md` | The adult equivalent of `preferences.md`: hand-edited, read by `load_taste()`, injected verbatim into `FIND_ADULT_PROMPT` (to score) and `CONCIERGE_PROMPT` (for tone only). **The 16 scored exemplars are the calibration** — the model generalises from them, and four are near-miss pairs isolating one variable each (language, curation, permanence, alcohol-vs-food). Two counter-intuitive low scorers are load-bearing: an alcohol-led event scores ~20 however sophisticated it looks, and cultural prestige alone (grand opera, landmark venue) doesn't earn a high score. Deliberately carries **no percentage weights** — the interview's original weighted rubric could not reproduce its own exemplars (#02 computes to 49.5 against a stated 0) and could not score a civic item at all (zeroing the two inapplicable axes caps any civic item at 60, against stated 100/95/85), so exemplars govern and the axes survive as qualitative prose. Language is not scored here at all; it is the `language_barrier` gate. Editing it is safe and expected. |
| `.github/workflows/weekly.yml` | Friday 6am UTC, fixed — no DST logic. (A prior two-cron-plus-skip-guard scheme meant to land on 9am Sofia time year-round instead fired both crons every week and skipped both, since GitHub Actions scheduling jitter meant the actual run hour rarely matched the guard's exact expected hour.) Commits `state/`. |
| `state/*.json` | CI-managed state. Seeds: `memory.json={"evergreen":{},"ledger":[]}`, `signals_seen.json={"seen":{},"monthly_count":{}}`. |

## Critical invariants — do not break

- **All LLM calls go through `llm_chain.call_llm()`; all email through `common.send_email()`.**
  `call_llm` abstracts Anthropic vs Gemini, the retry policy and the model fallback chain; email
  keeps its single SMTP path in `common.py`. Never call provider/SMTP endpoints directly, and never
  call `common.llm()` from this project again — it is the un-chained version and it is what produced
  the blank email of 2026-08-14.
- **Scrapers never crash the run.** Every source runs inside try/except → `[]` on any failure.
  Log per-source counts; one dead source must not lose the others.
- **Two FIND paths, never one widened brief.** The family path's quality comes from being *narrow*
  ("a family of 3… genuinely suitable for a 4-year-old"). A single brief asked to find family
  things *and* adult things *and* civic news degrades both. If you're tempted to merge them to
  save an LLM call: the call count is already 4 either way. Each path must keep its own
  try/except degrading to `[]` — one dead path must not cost the other — and `candidate_id` must
  be assigned **after** the merge so ids are unique across the batch SKEPTIC sees.
- **SKEPTIC only removes or corrects — it is the hallucination guard.** It exists so the email is
  trustworthy enough to act on. It must not invent items; it kills fake/past/too-far items and
  corrects wrong dates. Desirability is FIND/CONCIERGE's job, not a SKEPTIC kill. It must **not**
  judge who an item is suitable for — the old kid-relevance check was deleted, because
  suitability is now a scoring question answered upstream.
- **CIVIC-RADIUS: SKEPTIC must never kill a `civic_opportunity` on the radius check.** A new
  flight route or an airline announcement has no venue within 90 minutes of anywhere, because it
  has no venue at all. `SKEPTIC_PROMPT` carries an explicit carve-out sentence. The radius still
  applies normally to `civic_notice`, where the street is the whole point.
- **LANG-GATE: `language_barrier == "blocking"` drops the candidate *before* the floor check, and
  `"partial"` carries no numeric penalty in Python.** Language accessibility is structural, not a
  matter of degree — Bulgarian stand-up is simply unattendable, and a model asked to express that
  as a number scores it 45 and lets a busy week float it back. The FIND prompt already scores
  `partial` items lower; subtracting points as well is the double-counting that broke the original
  weighted rubric. The prompt also insists the barrier be *established*, not assumed, since a
  listing rarely states its language.
- **VERIFIED-NOT-A-GATE: SKEPTIC's `verified` boolean is a visibility signal, never a filter.**
  An uncorroborated item still goes to the reader — gating on it would lose exactly the thinly
  reported hyperlocal civic notices the civic half exists for. It is surfaced as `UNVERIFIED`
  per row in `weekend_log.md` plus a summary line naming every uncorroborated item that was
  sent. **That count is the tuning signal**: if it stays high week after week, tighten
  `SKEPTIC_PROMPT` or give the stage more search budget. Why it exists: in the 2026-08-11 run
  SKEPTIC kept 13/14 with zero kills and zero corrections, justifying items with "plausible for
  the season" and "a standard format for Plovdiv summer culture" — genre reasoning that an
  invented event in a real venue satisfies just as well. The prompt had literally licensed it
  (`"keep" (verified or plausible)`). `SKEPTIC_PROMPT` now enumerates the banned justifications
  and requires a **named source** in `note` for `verified: true`.
- **EXPIRY-NEEDS-END-DATE: `civic_notice_expired()` must read `end_date_iso` and must NOT fall
  back to `date_iso`.** `date_iso` is the START date, so using it to decide something has ended
  is a category error. The 2026-08-11 Peshtersko Shose closure ran 10–24 Aug against a window
  opening on the 12th — a `date_iso` fallback would have dropped a two-week closure of a road
  the owner drives, the highest-value item the pipeline has produced. A missing `end_date_iso`
  therefore **keeps** the item and simply disables the check for it.
- **ROUTINE-MAINTENANCE: recurring municipal maintenance rounds are dropped in Python, not just
  by prompt.** The municipality republishes a street-washing schedule every week with a fresh
  title and a fresh date, so it keys differently each time and the 21-day cooldown can *never*
  suppress it — left alone it appears in every email forever and trains the reader to skim past
  the outages that matter. `FIND_ADULT_PROMPT` tells the model to drop these and
  `is_recurring_maintenance()` guarantees it. Matched on **title only** (`reason` prose
  mentioning cleaning must not cost a real closure) and only within `CIVIC_CATEGORIES`.
- **NO-BARE-DOMAIN-LINKS: `clean_source_url()` strips a `source_url` with no path to `""`.**
  FIND habitually emits the domain of a site it never read — in the 2026-08-11 run *all 14*
  candidates came back as bare homepages (`https://plovdiv.bg` for a road closure, on a site
  that had 403'd during harvest). That is a guess dressed as a citation: unusable to a reader
  and unauditable as provenance. `""` is strictly better because `build_links()` then supplies
  a search link that actually resolves.
- **EVERGREEN-EXEMPT: evergreens are exempt from every score floor** (`floor_for()` returns
  `None`). That exemption is what guarantees the email is never empty. Applying a floor to them
  would *look* correct, since most evergreens score well above every threshold.
- **SORT-KEY: rank by `score_of(c, audience)`, never by `c.get("family_fit", 0)`.** Every adult
  and civic candidate carries `adult_fit` or `civic_value` and **no** `family_fit` at all, so the
  old sort scores all of them 0 and orders them arbitrarily. This fails **silently**: the email
  still contains the right items, with the concierge's prioritisation signal destroyed.
- **PREFIX: the civic cooldown test in `prune_seen()` must use `startswith("civic|")` — with the
  pipe.** An event titled "Civic Center Opening" slugs to `civic-center-opening` and its key
  begins `civic-center-opening|…`, so a pipe-less test would quietly give that event a 180-day
  cooldown instead of 21.
- **`role_for()` raises on an unknown category — do not "fix" it to `.get()`.** Returning `None`
  yields the key `slug|date|None`, which matches no stored key, so the item is never suppressed
  and re-sends every week forever. Add the category to `_ROLE_BY_CATEGORY` instead.
- **SCORES-INTERNAL: `family_fit`, `adult_fit` and `civic_value` never appear in email copy.**
  They rank items and drive the anti-repeat rotation. The email is warm prose / a soft itinerary —
  NEVER a scoreboard or a strict hour-by-hour plan.
- **Each candidate carries exactly ONE score.** Family → `family_fit`; adult culture →
  `adult_fit`; civic → `civic_value`. Never two. `family_fit` is never renamed and never assigned
  by the adult path. A missing score reads as `0`, which fails every floor — deliberate, so an
  unscored item is not sent. The three fields are optional in `STAGE1_ADULT_SCHEMA` because
  Gemini's `response_schema` cannot express "required only when category is X".
- **One email, not two newsletters.** Four **time-first** sections, never audience-first headers.
  CONCIERGE is told to move between family and adult items in the same section naturally.
- **Links are real or built, never invented.** The email should be actionable, but the CONCIERGE
  is given exact link strings and told not to fabricate URLs. `build_links()` passes through the
  real `source_url` (from FIND/scrapers, or an evergreen's catalog `url`; may be "") and
  deterministically constructs a Google Maps link (from `location`) and a search link (from
  `title`+`location`). Add links where they help someone act, not on every line.
- **UTF-8-STATE-IO: read and write `state/*.json` through `weekend_concierge.save_json()` /
  `load_json()`, never `common.save_json()` / `common.load_json()`.** The `common` pair passes
  `ensure_ascii=False` through an `open()` with no `encoding=`, so on a non-UTF-8 locale (Windows)
  it writes cp1252 and CI then cannot read back the state it just committed — the pipeline dies on
  its own output. `memory.py` has always done its own UTF-8 IO; these two bring the rest of
  `state/` in line. *Why the wrong thing looks correct:* `X.save_json` reads naturally beside the
  other `X.` helpers, CI is Linux so the round-trip never breaks there, and the failure only
  appears once a Windows-written state file reaches Actions. Guarded by
  `test_state_files_are_written_as_utf8`, which also asserts at least one state file actually
  contains a non-ASCII byte — otherwise the UTF-8 check would pass vacuously.
- **State in `state/` is CI-managed real state**, committed every run. Seed shapes as above.
- **Anti-repeat keys:** see the routing table above for the exact string form and cooldown per
  category. `role_for()` supplies the role suffix, so a look-ahead item can re-surface as
  "happening now" once its date arrives. **Family evergreen keys are byte-identical to the
  pre-audience form (`evergreen|slug(name)`) on purpose** — the live `state/signals_seen.json`
  holds entries in that shape, and changing it would reset the running family rotation. Adult
  evergreens use `evergreen|adult|slug(name)`. If no evergreen is off-cooldown, fall back to the
  least-recently-suggested catalog entry **of that audience** so the email is never empty and an
  adult evergreen can never fill the family slot.
- **The email always sends on Friday** (the weekly ritual is the point). Evergreen fallback
  guarantees non-empty content even on a dead-event weekend, in both audiences.
- **Weather is fed to the LLM as raw data, not a pre-classified label.** `weather.py` returns
  actual forecast numbers (max/min temp, feels-like, humidity, cloud cover, chance of rain,
  condition) for 7 days; CONCIERGE is trusted to interpret them and open the email with a
  short weather-at-a-glance line before any recommendations. Still a best-effort estimate,
  never a certainty — the prompt says so explicitly. **No trend computation in Python** — the
  model reasons over the numbers.
- **Weather gates family items but never adult ones.** For family picks it stays the selector it
  always was (rain → indoors, heat → shade/early). For adult and civic items it is trend texture
  only: forecasts here beyond ~12h are close to coin-flips, something worth going to is worth
  going to in a downpour, and a road closure happens whatever the weather.
- **Everything Bulgarian in, English out.** Search/scrape Bulgarian sources; write the email in
  English.
- **Model selection lives in `llm_chain.py`'s defaults, overridden by `LLM_*` env vars** — never in
  this repo. Every `llm_chain.py` reference in the invariants below means the file **in the
  external `llm-chain` repo**; it is not checked out here. Changing any of it is an upstream
  change, released per llm-chain's README, and it lands in every consumer at once.
  `config.py` and never as literals in pipeline code. `config.py` no longer names a model at all;
  `MODEL_FIND/SKEPTIC/CONCIERGE`, `GEMINI_MODEL_MAP` and `GEMINI_SEARCH_MODEL` are gone. Gemini still
  splits search (lite model, carries the `google_search` tool) from reasoning (flagship, tools-free):
  attaching `google_search` to a flagship model times out on Google's grounding gateway, and the
  split also keeps `responseSchema` off the search call, where it conflicts. The two chains are
  `LLM_SEARCH_MODEL_CHAIN` and `LLM_MODEL_CHAIN`. Model names are sent to the API verbatim — see
  Invariant NO-SILENT-MODEL-DEFAULT below, which now forbids the resolve-to-a-default behaviour
  `common.py` still has.

- **NO-SILENT-MODEL-DEFAULT.** A model name from `LLM_MODEL_CHAIN` reaches the API verbatim. There is
  no name-mapping dict and no `.get(name, fallback)` anywhere in `llm_chain.py`. *Why the wrong thing
  looks correct:* `common.py:114` does `C.GEMINI_MODEL_MAP.get(model, "gemini-flash-latest")`, so a
  typo'd model silently resolves to flash — the run succeeds, the log says flash, and the operator
  believes the chain is configured when it is not.
- **FAIL-FAST-ON-CLIENT-ERROR.** An HTTP status in neither `LLM_RETRY_STATUSES` nor
  `LLM_ADVANCE_STATUSES` (401, 403) returns immediately without trying another model. *Why the wrong
  thing looks correct:* retrying a 401 across 4 attempts × N models produces a run that takes four
  minutes and reports "all models unavailable" — indistinguishable from a real outage, so the
  operator waits for Google instead of fixing the key. `400` is the mirror case: it *advances* but is
  never *retried*, because a malformed `response_schema` returns 400 deterministically on every model.
- **BUDGET-VALVE.** `LLM_TOTAL_BUDGET_SECONDS` is checked before every request and before every
  sleep; once exceeded, `call_llm` returns `ok=False` without sleeping. It is the only thing that
  makes `timeout-minutes: 35` a proof rather than an estimate. The clock starts at the first
  `call_llm`, not at import, so a slow harvest does not consume the LLM budget.
- **SKEPTIC-INFRA-IS-NOT-REJECTION.** The no-verdicts branch distinguishes *SKEPTIC never ran*
  (`LLMResult.ok is False` → keep the candidates, flag `verified=False`) from *SKEPTIC ran and
  returned nothing usable* (`ok is True` → kill them, as before). *Why the wrong thing looks correct:*
  conflate them in the permissive direction and a genuinely malfunctioning SKEPTIC silently stops
  guarding hallucinations while every email still looks normal. `verified` is a **reporting flag
  only** — nothing filters, sorts or gates on it.
- **COMMON-PY-UNTOUCHED.** Leave `common.py` alone here. **Correction (2026-08-14):** the reason
  previously given for this rule — "byte-identical to deal-hunter's copy" — was **factually false**,
  and had been for some time. The three copies are three different files:
  weekly-concierge `17310bf6`, deal-hunter `a7d1c256`, shopping-assistant `07d55152` (git blob
  SHAs). The advice survives; its premise does not. The real reason to leave it alone is narrower
  and still sufficient: **nothing in this project needs it changed**. Its LLM half is dead code
  here, this repo is not where a `common.py` fix would be verified, and editing a file that has
  already drifted three ways invites a fourth divergence for no gain. Do not cite byte-identity to
  justify anything, and do not "resync" the three copies — the drift is the *evidence* for
  extraction, and `llm_chain` is the pattern that replaces it. If the shared half of `common.py`
  (`send_email`, state IO) ever needs a real fix, extract it the same way rather than editing three
  files.
  Its `llm`, `_gemini`, `_gemini_search`, `_anthropic`, `_post_with_retry` and `resolved_provider` are
  dead in this project and **must not be deleted** — deal-hunter and shopping-assistant call them
  from their own copies, and deleting them here would widen the drift rather than reduce it.
  *Why the wrong thing looks correct:* deleting dead code is normally right,
  and every test here would still pass. Note the consequence: `common._gemini` and
  `common._gemini_search` still read `C.GEMINI_MODEL_MAP` and `C.GEMINI_SEARCH_MODEL`, which
  `config.py` no longer defines, so calling `common.llm()` **in this repo** now raises
  `AttributeError`. That is a feature — the un-chained path fails loudly instead of quietly working —
  and it is not a reason to either restore the config names or edit `common.py`.
- **SUSPECT-THE-PROMPT-FIRST. When a stage looks under-powered, the prompt is the first
  suspect, not the model tier.** Pro was tried across all three reasoning stages and
  dropped: multiples of the cost, far more timeouts, no better output on this workload. The
  Aug 2026 review initially blamed the tier for a toothless SKEPTIC and was wrong -- the real
  causes were a prompt that accepted "plausible" as a verdict and a single batched search call
  spanning 14 candidates (~200 chars of grounding each). No model verifies 14 distinct claims
  from that.
- **HARVEST-CAP-FAIRNESS: `harvest()`'s volume cap must interleave per-source results
  round-robin (`_round_robin()`), never concatenate-then-slice.** A plain-concatenation cap
  lets whichever sources are listed first in `config.ENABLED_SOURCES` eat the entire
  `MAX_HARVEST_ITEMS` budget. This is not hypothetical: adding four civic/news RSS sources
  pushed pre-cap volume from ~180 to 320 items, and with a naive slice **every one of the new
  sources — the entire point of adding them — was silently truncated to zero**, since they're
  listed after the older, higher-volume event sources. Round-robin guarantees every enabled
  source gets a fair share of the cap before any single high-volume source (`bilet`,
  `plovdiv2019`, `trafficnews`) can exhaust it alone.

### Coverage knobs (`config.py`)

| Knob | Value | Note |
|---|---|---|
| `WEEK_WINDOW_DAYS` | 7 | "happening soon" = run-day+1 .. run-day+7 inclusive. On the Friday cron that is Sat..next Fri, so a Thursday festival six days out is caught with **no cron change**. |
| `MIN_INCLUDE_SCORE` | 50 | `family_fit` floor. Unchanged. |
| `MIN_ADULT_SCORE` | 70 | `adult_fit` floor. Deliberately high; **expects tuning** from logged `[LOW-FIT ]` drops. |
| `MIN_CIVIC_OPPORTUNITY_SCORE` | 75 | High bar — a durable fact should be genuinely worth telling. |
| `MIN_CIVIC_NOTICE_SCORE` | 55 | Low bar — near-zero tolerance for missing a disruption. |
| `CIVIC_OPPORTUNITY_COOLDOWN_DAYS` | 180 | A new mall is interesting once. |
| `MAX_TOKENS_SKEPTIC` | 20000 | was 12000; the batch now spans both paths. |
| `MAX_TOKENS_CONCIERGE` | 24000 | was 12000; four sections, uncapped items, body emitted twice (HTML + text). |

The token budgets are **load-bearing, not cosmetic**: `config.py`'s own comment warns that
exceeding `maxOutputTokens` truncates the JSON and "looks like a quiet weekend" — i.e. a silent
failure that reads as the feature finding nothing.

**On tuning the floors:** an over-tight floor fails into a thin section, which the owner has said
is acceptable. An over-loose floor fails into spam, which loses trust permanently. Bias tight.
Volume is otherwise **uncapped** — floors are the only control.

## Required secrets / variables

| Name | Type | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | secret | Gemini LLM calls (default provider) |
| `ANTHROPIC_API_KEY` | secret | Anthropic LLM calls (if `LLM_PROVIDER=anthropic`) |
| `LLM_PROVIDER` | repo variable | `"gemini"` (default) or `"anthropic"` |
| `LLM_MODEL_CHAIN` | repo variable, optional | Reasoning models, comma-separated, tried left to right. Default `gemini-flash-latest,gemini-3.1-flash-lite`. **Set in production to explicit numbered models** rather than `-latest`, which resolves to the newest and most load-shed flash — see the 2026-08-14 verification runs. |
| `LLM_SEARCH_MODEL_CHAIN` | repo variable, optional | Search-step models. Default `gemini-3.1-flash-lite,gemini-flash-latest` |
| `LLM_ATTEMPTS_PER_MODEL` | repo variable, optional | HTTP attempts per model before advancing. Default `4`. **Lower this before raising `LLM_BACKOFF_SECONDS`** — advancing beats waiting on a shedding model. |
| `LLM_BACKOFF_SECONDS` | repo variable, optional | Sleep between attempts; the last value repeats. Default `5,15,45` |
| `LLM_TIMEOUT_SECONDS` | repo variable, optional | Per-request HTTP timeout. Default `180` |
| `LLM_RETRY_STATUSES` | repo variable, optional | Retry the **same** model on these. Default `429,500,502,503,504` |
| `LLM_ADVANCE_STATUSES` | repo variable, optional | Advance to the **next** model on these. Default `400,404,429,500,502,503,504` |
| `LLM_TOTAL_BUDGET_SECONDS` | repo variable, optional | Wall-clock ceiling across all LLM calls in a run. Default `1200` |
| `LLM_RETRY_AFTER_CAP` | repo variable, optional | Upper bound on a `Retry-After` header. Default `120` |
| `LLM_ANTHROPIC_MODEL` | repo variable, optional | Model used when provider is `anthropic`. Default `claude-haiku-4-5-20251001` |

Every `LLM_*` row above is **optional** — unset means the default, and all ten are already passed
through in `weekly.yml`, so any of them can be set from the Actions UI with no code change. An
empty or whitespace-only value resolves to the default, never to an empty list. Run
`python -m llm_chain` to see which models your key can actually reach before setting a chain.
These knobs are defined by the external `llm-chain` package, not by anything in this repo — its
README is the authority on their semantics.
| `SMTP_HOST/PORT/USER/PASS` | secrets | Email delivery |
| `EMAIL_TO` / `EMAIL_FROM` | secrets | Recipient / sender (default to SMTP_USER) |

No RapidAPI/hotel/weather keys (open-meteo needs none).

## Running locally

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...  LLM_PROVIDER=gemini
python weekend_concierge.py   # writes state/; emails if SMTP vars set, else prints the error
python scrapers.py            # harvest smoke test: prints per-source item counts
python weather.py             # 7-day forecast smoke test
python -m llm_chain           # resolved config + available models + one live ping (the
                              # installed package, NOT a file here -- was `python llm_chain.py`)
python test_concierge.py      # full pipeline, two runs, fully offline
python test_scrapers.py       # fixture-backed parse tests, fully offline
python test_memory.py
```

`pip install -r requirements.txt` is now **required**, not merely recommended: `llm_chain` is
installed from git rather than checked out, so without it `weekend_concierge.py` fails at import.
The LLM layer's own 17 tests live in the llm-chain repo and run in its CI; there is no
`test_llm_chain.py` here.

Leave SMTP vars unset to test without sending (the send is caught and printed).

**`.env` works for `weekend_concierge.py` only.** It calls `load_dotenv()` behind a try/except,
so a `.env` at the repo root supplies `GEMINI_API_KEY`/SMTP for the pipeline. `llm_chain`
deliberately imports nothing but stdlib + `requests` (that portability constraint is the whole
point of the module, and it now binds harder — the module is shared by unrelated pipelines, so a
dependency added there is imposed on all of them), so running it standalone reads the **process**
environment only — a `.env` is invisible to it and the run reports
`HTTP 403 ... unregistered callers`, which looks like a dead key rather than an unset one. Export
the variable in the shell before `python -m llm_chain`. This is documented in llm-chain's own
README too, since the trap now belongs to every consumer rather than to this repo alone.

## Known-bad calibration point: the 2026-08-14 scheduled run

(Not to be confused with the 2026-08-14 *manual* verification runs below, which succeeded.)

The run produced a **blank email**. Do not re-diagnose this as a model-quality or prompt problem —
it was infrastructure, and the evidence is specific:

- `gemini-flash-latest` returned **503 on 13 of 14** reasoning calls inside a three-minute window.
- `gemini-3.1-flash-lite` served **3 of 3** search calls on the **same API key, same window**. So
  this was not the key, not the quota and not the account — it was one model's capacity.
- **Billing was enabled** (€20 credit), which rules out free-tier load-shedding.

Three separate weaknesses turned a provider capacity dip into no email at all, and it took all
three: `common._post_with_retry` allowed 4 attempts with sleeps of 2/4/8 — **14 seconds of total
patience** against an event lasting minutes; there was **no cross-model fallback** despite a
demonstrably healthy model on the same key; and the SKEPTIC no-verdicts branch **killed every
non-evergreen candidate**, so one dead verification call emptied an email whose FIND-adult stage had
already succeeded and found a real water-main failure. That item was discarded.

The first two are fixed in `llm_chain.py`, the third in the `skeptic_available` split. The reason the
degraded banner exists at all is the shape of the complaint: the run failing was not the problem —
the failure **looking like a quiet week** was.

## Known trade-offs (accepted — don't "fix" without asking)

- **Scraping is brittle.** Sites change; some sources are raw-fetch page-text blobs that FIND must
  parse. Facebook is a documented stub (auth/anti-bot). FIND's web search partially compensates.
- **No ticketing/price data.** This finds things to do, not the cheapest way to do them.
- **Weather is unreliable in Plovdiv** and treated as a best-effort estimate, not gospel.
- **90-min radius**, with `civic_opportunity` the one deliberate exception (a flight route has no
  venue). Destinations needing arduous travel are excluded by the prompts. Intentional.
- **The family path now spans 7 days, not just the weekend.** The owner's call, overriding a
  recommendation to keep it weekend-only. Consequence accepted: weekday kids' events are often
  unattendable at a 20:00 start, so expect some added noise in the family half. The prompt tells
  FIND to be honest about that in `family_fit` rather than filtering it out.
- **No server-rendered Plovdiv cinema source exists.** Probed: `cinemacity.bg` returns 200 with
  **zero** hrefs containing "plovdiv"; `kinoarena.com` 200/127k chars with **zero** Plovdiv
  mentions; `luckyplovdiv.com`, `kinolucky.bg`, `cinemax.bg` all fail DNS/connection; programata's
  own `/kino/filmi/odiseya/` page has 0 Plovdiv mentions and 2 Sofia. All listings are
  client-rendered — the headless-browser tradeoff this repo has ruled out three times. **Cinema
  therefore reaches the email only via FIND's web search**, which `SEARCH_ADULT_PROMPT` is
  explicitly instructed to attempt by name. If cinema never shows up, that prompt line is the
  thing to fix, not a new scraper.
- **Civic geography is inferred from "central Plovdiv", not configured.** No hardcoded streets or
  routes — the owner doesn't know the names of most roads he drives on. Consequence accepted:
  relevance degrades to proximity-to-centre, so expect some noise and some route-specific misses.
- **Disruption sourcing is the weakest link, in the highest-value category.** No utility publishes
  a usable feed: `vikplovdiv.com` returns 200/192k chars with 198 Plovdiv mentions and *looks*
  ideal, but its only announcement-shaped nav links are commercial plumbing services
  (`/remonti/…`); `evn.bg` has 7 Plovdiv mentions, `electrohold.bg` has 1 outage-word. Disruptions
  therefore rest entirely on local news (`marica`, `plovdiv24`, `plovdiv_bg` — the last 403-prone
  from CI) plus web search. **First real run (2026-08-11): it worked anyway.** All three civic
  items came through FIND's `google_search` grounding, not the harvest — `plovdiv_bg` 403'd
  twice that run, `marica` and `plovdiv24` yielded one raw blob each — and two of the three
  verified exactly. So web search is currently carrying this category alone. That is fragile
  but not broken; the fix is better sources, not more filtering.
- **`programata.bg/plovdiv/`** is not usable as an event source: 200/50k chars but **0**
  `div.post-list-entry` cards and only 41 bare `<li><a>` links — a venue directory, matching the
  existing note about `/sofia`.
- **`common.py`'s retry policy stays broken here.** 4 attempts, sleeps of 2/4/8, no chain. It is
  not fixed here because nothing in this project calls it any more — the fix lives in the external
  `llm-chain` package, and deal-hunter and shopping-assistant get it by depending on `@v1`, not by
  anyone patching their `common.py`. (The old reason given, "`common.py` stays byte-identical
  across three projects", was not true — see the COMMON-PY-UNTOUCHED invariant.)
- **The `llm-chain` fallback chain is Gemini-only.** `_anthropic()` passes a **single-element**
  list to `_run_chain`, so the Anthropic path gets retry-with-backoff, `Retry-After` handling and
  the budget valve, but **no cross-model fallback**. Accepted: production runs Gemini 100% and
  Anthropic is a hedge. Documented here and in llm-chain's README so nobody assumes otherwise;
  widening it means a new env var and a changed failure profile for every consumer.
- **A moved `v1` tag reaches production with no per-repo review.** That is the deal the extraction
  makes: one edit propagates everywhere, and there is no lockfile or staged rollout between
  promoting the tag and a Friday cron emailing a real person. The mitigation is procedural, in
  llm-chain's release flow — CI green, then verified in this repo as the proving consumer, only
  then `git tag -f v1`. A breaking change to `call_llm`/`LLMResult` gets a `v2` tag instead.
- **`common.save_json()`/`load_json()` carry an encoding defect** — `ensure_ascii=False` with no
  `encoding="utf-8"` on `open()`, so a non-ASCII string written on a non-UTF-8 locale (Windows)
  lands as cp1252 and a later UTF-8 read fails. **This project no longer routes through them**
  (see the UTF-8-STATE-IO invariant); the defect remains in `common.py` for deal-hunter and
  shopping-assistant to fix upstream, since `common.py` is not ours to change here.
- **`memory.py` duplicates `EVERGREEN_COOLDOWN_DAYS = 70`**, which `config.py` also defines. A
  latent drift risk found by reading, not an observed bug; de-duplicating means introducing a
  `memory.py → config.py` import that doesn't exist today. Flagged, not fixed.
- **A recurring weekly fixture is an `evergreen`, not an event.** A weekly English open-mic on a
  21-day event cooldown would appear ~17×/year and teach the reader to skim.
- **Childcare is not modelled at all.** Assume it is always available; never downrate an
  adults-only evening for it. The owner would rather know and decide himself.
- **No global anglophone media calendar** (TV, games, book releases). Deliberately out: the owner
  is already ahead of anglophone culture, and it has no Bulgarian source, no radius, no venue and
  nothing SKEPTIC could verify.
- **No dynamic seasonal score floor.** Rejected: it solves email density, which the evergreen
  catalog already solves, and a self-moving threshold makes `weekend_log.md` untunable.

## Out of scope (do not start without an explicit request)

- Booking/ticket purchase integration.
- A web/PWA front-end — the product is deliberately a passive weekly email.
- Reply-to-email feedback parsing (feedback is the hand-edited `preferences.md` / `taste.md`).

## Resolved: `state/` persistence

**Closed 2026-08-11.** `state/` had not been committed since 2026-07-17 (24 days against a
weekly cron), which would have meant none of the cooldowns above worked in production. The
2026-08-11 run committed and pushed cleanly (`8740b09..17b2b06`, 5 files, 720 insertions), so
the workflow's commit step is healthy. Cause of the gap was never established — the runs in
between were not inspected before the log rotated.

## Known-good calibration point: the 2026-08-11 run

The first real dual-audience email. Eight of its fourteen candidates were independently
web-verified afterwards; **seven were accurate**, several impressively so (the Peshtersko Shose
closure matched the municipal notice on dates, lane, both cross streets and the five rerouted
bus lines). The one that did not corroborate — a water disruption on ul. Brezovsko Shose — was
the *highest*-scored civic item at 85 and carried a SKEPTIC note reading "Verified as a current
municipal utility maintenance notice". Nothing in the pipeline could distinguish it from the
seven real ones, which is what motivated `verified` and the `UNVERIFIED` log line. Treat this
run as the reference point when tuning: the adult path does find real things in Plovdiv, and
the failure mode to watch is confident-sounding verification, not empty output.

## Known-good calibration point: the 2026-08-14 manual verification runs

Two `workflow_dispatch` runs after PR #17 merged, which together closed the two things the test
suite structurally cannot reach.

**The fallback chain works live, on both trigger paths.** With
`LLM_MODEL_CHAIN=zzz-not-a-real-model,gemini-flash-latest` the log read
`zzz-not-a-real-model exhausted (HTTP 404); advancing to gemini-flash-latest` — the 404
advance-without-retry path. Then in the clean run the chain fired *unprompted* on a real outage:
`gemini-3.7-flash` 503'd four times on the concierge stage and the run completed on
`gemini-3.6-flash`. The email arrived complete, and — correctly — with **no** `[degraded]` prefix:
`weekend_log.md` recorded `0 stage(s) failed (none); 1 fell back to a later model (concierge)`.
That is the fall-back-is-not-failure distinction behaving exactly as designed. **This is the run
to point at if anyone proposes simplifying the two-level retry loop.**

**`gemini-flash-latest` is the model that sheds, and its numbered equivalent sheds identically.**
Across the clean run `gemini-3.7-flash` returned 503 on **9 of 12** reasoning attempts while
`gemini-3.6-flash` served 1/1 and `gemini-3.1-flash-lite` served 3/3 search calls on the same key
in the same window — the same signature as the blank-email run's 13-of-14, which suggests
`-latest` simply resolves to the newest flash and that the newest flash is the congested one.
**Consequence for tuning: prefer a lower `LLM_ATTEMPTS_PER_MODEL` over a longer backoff.** Four
attempts against the congested model spent ~140s sleeping to reach an answer the next model in
the chain would have given immediately; the chain is the resilience mechanism, the retry loop is
only there for a genuine blip. Do not "fix" a 503-heavy log by lengthening
`LLM_BACKOFF_SECONDS` — that spends `LLM_TOTAL_BUDGET_SECONDS` to wait on a model that is
shedding load on purpose.

**The email reads as one artifact.** The clearest evidence is that the two FIND paths
independently surfaced the same festival (Shake That Hill, `family_fit=80` and `adult_fit=85`)
and CONCIERGE wrote it as a **single paragraph** that hands off from daytime craft corners to the
evening electronic stages — the dual-audience intent working in one sentence rather than two
sections. Note the cost, accepted: there is no cross-path dedupe, so one event consumed 2 of the
10 sent slots. Keys differed only because the two titles slugged differently; identical
title+date across paths would collide on one key, and whichever path was written first would
claim it.

**SKEPTIC has teeth now.** 2 kills of 14 with specific reasons ("Could not confirm the existence
of a 'Kids Expo Plovdiv' for these dates"), against 2026-08-11's 13-of-14 keep with zero kills.
Both `UNVERIFIED` items were among the killed, so **zero uncorroborated items were sent** — the
tuning signal reading clean for the first time. One caveat worth keeping in view: the surviving
notes cite named sources (`galleryplovdiv.com`, Eventim.bg, an EVN announcement), which is
exactly the shape of the one 2026-08-11 item that was confidently sourced and still wrong.

**Watch item, not yet a defect:** the Peshtersko Shose closure re-sent a week later under a
different title (framed as heating-pipe works rather than the original closure notice), so it
keyed differently and the 21-day cooldown did not suppress it. Re-announcing a still-active
closure is defensible. A *third* appearance under a third title would make it the street-washing
failure mode in a new coat, and `is_recurring_maintenance()`'s title-only matching would not
catch it.

## Style

Flat functions, plain stdlib + `requests` + BeautifulSoup, clear names, short modules. Match the
existing tone. Prefer editing in place over adding files. Comment only the non-obvious. No emoji
in code; `weekend_log.md` and the email HTML may use them.