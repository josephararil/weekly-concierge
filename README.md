# Deal Hunter

Finds genuinely good-to-exceptional hotel travel windows for a family of 3 (2 adults + child
aged 4) based near Plovdiv, Bulgaria. One script, three stages, live Booking.com rate
verification, and a deterministic scoring model. Runs daily on free GitHub Actions and emails
an honest daily digest of every scored candidate (💎 diamond / 👍 good / skip) whenever there's
something new to see; silent otherwise.

## How it works

```
find_city_anomalies.py   (daily, three-stage gate)
   │
   ├─ Stage 1 — FIND (web search)
   │     Scores hotel/resort/flight/cruise candidates 0–100 with an est_price_eur.
   │     Gate: FIND score ≥ 80 → grounding. No price filter (price is scored later).
   │
   ├─ Stage 2 — GROUND (Booking.com live rates → LLM fallback)
   │     providers.ground_api() fetches live nightly rates (apidojo RapidAPI); fuzzy-matches
   │     the named hotel; falls back to LLM concierge + web search on failure.
   │     kill → dropped. confirm/correct → real price merged and forwarded to scoring
   │     (unless a data-quality guard trips: low confidence / dates out of window).
   │
   └─ Stage 3 — SCORE (LLM desirability score + deterministic modifiers)
         The LLM returns a 0–100 desirability score (price held neutral). The pipeline then
         computes: final = llm_score + price_adj (vs regional par) + transit_adj (drive/fly).
         final ≥ 85 → 💎 diamond · ≥ 68 → 👍 good · below → skip.
         The email is a daily digest of EVERY scored candidate (diamond/good/skip) with its
         score breakdown, so you see what the pipeline weighed and why. Anti-spam TTL (keyed
         destination|window|tier) fires on any new/tier-changed item and stays quiet on
         repeats. Grounding kills/blocks ride along in a "seen & dropped" footer. Every score
         is recorded — no veto throws information away.
```

State files (`state/`) are committed back by CI after each run — no external database.

## Setup

1. Push this repo to GitHub.
2. Add secrets and variables under *Settings → Secrets and variables → Actions*:

   **Secrets** (encrypted):

   | Secret | What |
   |---|---|
   | `ANTHROPIC_API_KEY` | console.anthropic.com — required if `LLM_PROVIDER=anthropic` |
   | `GEMINI_API_KEY` | aistudio.google.com/apikey — required if `LLM_PROVIDER=gemini` |
   | `RAPIDAPI_KEY` | RapidAPI key for Booking.com (apidojo) hotel grounding |
   | `SMTP_HOST` / `SMTP_PORT` | e.g. `smtp.gmail.com` / `587` |
   | `SMTP_USER` / `SMTP_PASS` | sending address + app password (Gmail: 2FA → App Password) |
   | `EMAIL_TO` / `EMAIL_FROM` | recipient / sender (both default to `SMTP_USER`) |

   **Variables** (plain text, *Variables* tab in the same page):

   | Variable | Default | Effect |
   |---|---|---|
   | `LLM_PROVIDER` | `anthropic` | `anthropic` or `gemini` |
   | `HOTEL_PROVIDER` | `apidojo` | Set to `""` to force LLM-only grounding (no Booking.com calls) |
   | `BOOKING_RAPIDAPI_HOST` | `apidojo-booking-v1.p.rapidapi.com` | Override the RapidAPI host |

3. Enable Actions. Test via *Actions → daily → Run workflow*.

> **LLM web search:** with `LLM_PROVIDER=anthropic`, Stage 1 and the LLM fallback use the
> Anthropic `web_search` tool — enable it in the console if needed. With `LLM_PROVIDER=gemini`
> it uses `google_search`; if Gemini rejects the tool, the call retries without search.

## Running locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...  LLM_PROVIDER=anthropic  RAPIDAPI_KEY=...
# or: export GEMINI_API_KEY=...  LLM_PROVIDER=gemini  RAPIDAPI_KEY=...
python find_city_anomalies.py   # writes state/; emails if diamonds found + SMTP vars set
```

To skip Booking.com calls (LLM-only grounding):
```bash
HOTEL_PROVIDER="" python find_city_anomalies.py
```

To run offline unit tests for the grounding provider:
```bash
python test_providers.py           # apidojo: monkey-patched, no network
HOTEL_PROVIDER="" python test_stub.py  # full pipeline: LLM-only, stub llm()
```

## Tuning (config.py)

| Knob | Default | Effect |
|---|---|---|
| `STAGE1_MIN_SCORE` | 80 | Minimum FIND score to forward a candidate to grounding (triage only; no price filter). |
| `MAX_EMAILS_PER_RUN` | 3 | Cap on the actionable diamond/good picks shown (diamonds first); skips are context and always shown in full. |
| `SIGNAL_TTL_DAYS` | 14 | Anti-spam TTL per destination+window+tier; a tier change re-notifies. |
| `DIAMOND_PAR_EUR` | BG €80, TR €85, rest €110 | Per-night reference price. Below par → score bonus, above → penalty. Not a wall. |
| `PRICE_SCORE_WEIGHT` / `PRICE_BONUS_CAP` | 50 / 15 | Strength of the price modifier; bonus capped, penalty uncapped. |
| `TRANSIT_TIER1_BONUS` / `TIER2` | +3 / −3 | Deterministic drive-vs-fly score nudge. |
| `DIAMOND_SCORE_THRESHOLD` / `GOOD_SCORE_THRESHOLD` | 85 / 68 | Final-score cutoffs for 💎 / 👍. |

## Cost

LLM calls are cheap at this volume (a few per day, Claude Haiku/Sonnet or Gemini).
Booking.com rate lookups via RapidAPI are one call per gate survivor (typically 3–5 per day).
The LLM fallback fires on any API failure.

See `CLAUDE.md` for full design rationale, pipeline invariants, and grounding seam details.
