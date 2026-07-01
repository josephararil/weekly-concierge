# Design Notes & Roadmap

## Current product: the Diamond Finder

The active system is `find_city_anomalies.py` — a daily script that emails
immediately when it finds something genuinely exceptional. It runs on GitHub Actions, costs
nothing beyond API tokens and RapidAPI calls.

### Why LLM-only (no scraping)?

The original design (v1) did cross-sectional outlier detection over scraped hotel data: flag a
hotel far below its same-class peers in the same crawl. That catches a "crazy manager" but is
blind to a *whole city* dropping — when everything drops together the median drops with it, so
the detector goes quiet exactly when the deals are best.

The v2 design added a seasonal baseline (city|class|month medians) and an LLM planner at the
front to steer the expensive crawl. This worked but introduced complexity: scraping costs, a
slow baseline warm-up period, and a weekly digest cadence that missed short-window deals.

The current (v3) design uses LLM + live web search to detect market shocks, post-event
collapses, flight sales, and currency moves. Stage-3 grounding now uses Booking.com live
rates (apidojo RapidAPI) instead of a second LLM call, giving factual price verification
without scraping infrastructure. The LLM fallback fires when the API is unavailable.

### The two-stage gate

The core design decision is: **two separate LLM calls with different postures**.

**Stage 1 — find (want_search=True)**

A generalist prompt that casts wide: hotels, resort closeouts, post-event collapses, cruises
departing from the region, flight error/sale fares from Sofia (SOF) and nearby airports,
package dumps, currency-driven cheapness. Uses web search to ground every claim in recent
findings. Scores each candidate 0–100. Includes lower-scoring candidates too, so `city_signals.md`
is a useful daily log even on silent days.

**Stage 2 — skeptic (want_search=False, only candidates >= 80)**

A deliberately hostile prompt with a default-reject stance. Kill conditions: normal low-season
pricing, vague evidence, modest savings (< 30% off normal), narrow window (< 72h), poor fit
for a 4-year-old, long connection times. A result of zero keepers is correct and expected most
days.

The two-call structure matters: the finder is optimistic and broad, the skeptic is adversarial
and narrow. Combining them into one prompt would muddy both objectives.

### Anti-spam memory

`state/signals_seen.json` keyed by `destination|window`. 30-day TTL. Prevents a genuine
but persistent window (e.g. "cheap Antalya for the next 3 weeks") from generating daily email.
Monthly count also tracked; if it exceeds 3 in a month, a conscience note is added to the
email body so you can judge whether thresholds need tuning.

### Email-now vs weekly digest

The original design accumulated deals into a weekly Sunday digest so weekday finds would
survive to the send window (crawl horizons were 10/17/24 days out). The diamond finder drops
this: any window worth an email is worth an email today. The 30-day TTL handles deduplication.

### Broadened scope

The original scope was hotels only. The diamond finder hunts across:
- Hotels / resorts — any class, with or without all-inclusive
- Seasonal resort closeouts — end-of-season fire sales
- Post-event price collapses — conventions, festivals, sporting events ending
- Cruises — family-friendly itineraries from Istanbul, Athens, Thessaloniki or similar ports
- Flight error/sale fares — published from SOF, OHD, VAR, BOJ, SKP
- Holiday package dumps — operators offloading unsold flight+hotel allocations
- Currency-driven cheapness — EUR buying power spikes

The anchor city list (`config.CITIES`) guides the search but the Stage 1 prompt explicitly
allows nearby or thematically related destinations when a confirmed opportunity exists.

## Scraping pipeline (retired)

The original scraping pipeline (`hunt.py`, `baseline_sampler.py`, `patterns.json`) has been
retired and is no longer in the repository.

Stage-3 grounding now uses Booking.com live rates via the apidojo RapidAPI — a lightweight
API call per Stage-2 survivor rather than a full scrape. See CLAUDE.md "Hotel grounding seam"
section for the implementation details and fallback behaviour.

Restore full scraping only if the current approach demonstrably fails and you are prepared to
take on scraping costs and the operational complexity of a crawling pipeline. That is a
deliberate product decision, not a configuration change.

## What's parked (do not start without an explicit request)

1. **Flights** — surface a hotel only when a cheap outbound flight exists in-window. Natural
   complement to the hotel-crawl pipeline if that is ever restored.

2. **Package operators** — scrape Bulgarian-market charter operators (Comet Tours, Prima
   Holidays, etc.) for unsold near-departure allocations. JS-heavy sites, scattered supply.
   Same two-stage architecture could apply, pointed at operator search results.

3. **Urgent override** — a separate immediate-email path for outrageous short-window finds
   that would be lost under a weekly digest. Moot under the current email-now design.
