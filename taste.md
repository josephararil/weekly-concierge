# taste.md — adult taste calibration

Hand-edited, like `preferences.md`. Injected verbatim into the adult FIND prompt and into CONCIERGE.
Edit it freely; the exemplars below are what actually calibrate the model, so change a score if it
ever feels wrong and the pipeline will follow.

The household: Joseph and Marti, central Plovdiv, with a 4-year-old (Sophie). Joseph is
culturally anglophone and does not speak Bulgarian. Media diet is FT / The Economist. No TV, no
local news. This file scores things **for the adults**; Sophie's side is scored separately by the
family path and is not this file's business.

Two independent scales live here. Do not mix them:

- **`adult_fit`** (0–100) — how much the adults would want to go. Floor to send: **70**.
- **`civic_value`** (0–100) — how much they need to *know* a local fact, whether or not anyone
  attends it. Floors to send: **75** for a durable fact (`civic_opportunity`), **55** for a
  time-bounded one (`civic_notice`).

A civic item's value has nothing to do with whether it is enjoyable, so it never gets an
`adult_fit`, and a concert never gets a `civic_value`.

---

## 1. Qualitative axes — no weights

Judge holistically against the exemplars in §2. There is deliberately **no formula and no
percentage weighting**: an earlier weighted rubric could not reproduce these same exemplars, and
when arithmetic and judgement disagree, the judgement is the thing that came from a human.

**Taste alignment — the dominant axis.**
High: iconic or cult cinema re-screenings, English-language stand-up, high-level tech and economics
talks, unique permanent restaurants, synthwave / electronic / post-rock.
Middle: standard blockbuster cinema, general outdoor markets, high-end one-off guest-chef pop-ups.
Low: pop-chalga, generic rock and club nights, alcohol-centric events (wine tastings, cocktail
evenings — see #08, this is a real and counter-intuitive zero-interest area), obscure
non-masterpiece cinema, anything MLM- or crypto-adjacent.

**Hyper-local proximity and civic impact.**
Central Plovdiv is the centre of the world here: street and parking changes, utility outages, new
retail, direct flights from Plovdiv airport. Sofia and the Rhodopes (45–70 minutes) are reachable
but cost an item a little — a Sofia event needs to be better than a Plovdiv one to be worth the
drive (#13). Beyond ~90 minutes, or a generic national administrative announcement, is out.
For `civic_value` specifically, **location matters enormously and language is irrelevant** — an
outage notice is just as useful in Bulgarian.

**Permanence beats one-off.** A restaurant that opens and stays open is worth more than a
single evening that will never recur (#05 vs #06). A high price point for a one-night thing is a
real drag on the score.

**Dual utility is a bonus, not a requirement.** An item Sophie can also attend is worth more —
open-air food-and-music festivals are the model case (#07). But **assume childcare is always
available.** An adults-only evening is never penalised for being adults-only; the household would
much rather hear about something and decide for itself.

**Language is not scored here.** It is the separate structural field `language_barrier`
(`none` | `partial` | `blocking`). `blocking` removes the item entirely regardless of how good it
is — see #02. `partial` should read as a lower score in your judgement, but do not also apply a
numeric penalty; that double-counts. And `language_barrier` must be **established**, not assumed:
an event listing rarely states its language, so say `none` or `partial` only when you actually
have evidence.

---

## 2. Scored exemplars — the load-bearing section

These 16 are Joseph's own judgements and are the real calibration. Generalise from them.
Scores and item descriptions are his verbatim; the justification text has been trimmed where it
referenced scoring machinery that no longer exists (see the note at the end of this file).

### `adult_fit` scale

| ID | Item | `adult_fit` | Why |
|---|---|---|---|
| **01** | **Pair 1A:** Touring English-language stand-up comedian at a Kapana club (€15). | **95** | Fully legible, squarely on-taste, and genuinely scarce in this market. The archetype of what this pipeline exists to catch. |
| **02** | **Pair 1B:** Prominent Bulgarian stand-up comedian performing at Boris Hristov House of Culture. | **0** | *[Isolated variable: language]* Spoken-narrative comedy in Bulgarian is unattendable. Note this is **not** a low score — it is `language_barrier: blocking`, which removes the item before any scoring happens. Do not reach 0 by arithmetic; reach it by the gate. |
| **03** | **Pair 2A:** 4K Anniversary Screening of *Blade Runner* in original English at Lucky Cinema. | **95** | A masterpiece re-screening in the original, locally and immediately actionable. |
| **04** | **Pair 2B:** 1950s obscure French drama screening with Bulgarian subtitles at Lucky Cinema. | **25** | *[Isolated variable: cultural stature / curation]* Obscurity is the problem, not age — this fails the curated-distinction bar that #03 clears easily. |
| **05** | **Pair 3A:** Permanent opening of a unique Bulgarian-Malaysian fusion restaurant in Central Plovdiv. | **90** | A distinctive permanent addition to the city for a non-drinking household that cares about food. |
| **06** | **Pair 3B:** One-night-only guest chef tasting menu pop-up (€70/person) at a local hotel restaurant. | **45** | *[Isolated variable: permanence vs. one-off friction]* Same cuisine interest as #05, but the price and the one-night-only framing halve it. |
| **07** | **Pair 4A:** Weekend Craft Beer & Street Food Festival in Kapana (open-air food stalls, ambient music, space for children). | **85** | Open-air food and atmosphere, and Sophie can come — dual utility raises the whole household's return. Note the beer is incidental; the food and the setting are the draw. |
| **08** | **Pair 4B:** Guided Natural Wine Tasting with Sommelier (€30/person) at a Kapana bar. | **20** | *[Isolated variable: food and atmosphere vs. alcohol focus]* **Counter-intuitive low scorer.** Reads as an obvious adult night out and is not — interest in drinking as the *point* of an event is zero. Generalise this: when alcohol is the subject rather than the accompaniment, score it down hard. |
| **11** | Guest lecture in English on macroeconomic trends in SE Europe hosted at Plovdiv University. | **90** | Straight down the middle of the FT/Economist media diet, in English, in town. |
| **12** | European synthwave / electronic DJ performing a night set at a Central Plovdiv club. | **80** | On-genre and non-verbal, so language never arises. |
| **13** | Top-tier international English stand-up comedian performing in Sofia (70-minute drive). | **85** | Would be a #01-tier 95 in Plovdiv; the drive to Sofia costs it a little but nowhere near enough to matter. Use this as the calibration for how much distance actually costs. |
| **14** | Weekly English open-mic comedy night with amateur expat performers at a local pub. | **70** | Amateur production quality would ordinarily put this far lower, but English-language live comedy is scarce enough here that it still clears the bar. **Classify a recurring weekly fixture like this as `evergreen`, not as an event** — it is definitionally always available, and as a dated event on a 21-day cooldown it would appear roughly 17 times a year and teach the reader to skim. |
| **16** | Opera (*La Traviata*) sung in Italian with Bulgarian-only stage surtitles at the Ancient Theatre. | **30** | **Counter-intuitive low scorer.** Prestigious, visually spectacular, in a landmark venue, and still not wanted: no personal resonance, and the surtitles are illegible. Do not let cultural prestige stand in for taste. |

### `civic_value` scale

Not commensurable with the scores above — a 100 here means "he must not miss this", not "he will
enjoy this". Nobody attends an outage.

| ID | Item | `civic_value` | Category | Why |
|---|---|---|---|---|
| **09** | Scheduled 8-hour municipal water & power outage affecting street *Mitropolit Panaret*. | **100** | `civic_notice` | The maximum. Directly disrupts the home, is announced only in Bulgarian, and reaches him through no other channel. This single case is worth more to him than a year of everything else — treat a missed disruption as the pipeline's worst possible failure. |
| **10** | Ryanair announcing new direct flight route from Plovdiv Airport to Frankfurt Hahn. | **95** | `civic_opportunity` | Durable, practical, and pure civic osmosis — the sort of fact a local absorbs from the radio. Note this has **no venue and no radius**; do not kill it for being unlocatable. |
| **15** | Major retail brand (e.g. Decathlon or IKEA concept store) opening a location in Plovdiv. | **85** | `civic_opportunity` | Real household logistics value, and true for months rather than days — which is exactly why it gets the long 180-day cooldown and only needs telling once. |

Note on the pairs: #01/#02, #03/#04, #05/#06 and #07/#08 are deliberate near-misses that isolate a
single variable each — language, curation, permanence, and alcohol-vs-food. They are the most useful
rows in the table. When an unseen item feels borderline, find which pair it sits between.

---

## 3. Hard exclusions

Never propose these. All are taste judgements, not accessibility ones.

1. **Pop-chalga and generic nightclub party nights.** No alignment with music taste; actively
   unpleasant.
2. **Crypto, MLM and "wealth building" meetups.** Reads as spam and damages trust in the whole
   email.
3. **Bulgarian-dubbed cinema.** Any international release dubbed rather than subtitled.
4. **Religious processions and Orthodox church ceremonies.** Excluded because they are **boring** —
   low-stakes ceremonial content with nothing to do and nothing much to see. Note this is *not* a
   language exclusion: making it one would wrongly admit the next visually-striking but equally
   dull ceremony. The problem is that there is no there there.
5. **Student art showcases and amateur local theatre.** Same reasoning: **amateur work with little
   to look at.** Judge on production quality and whether there is anything worth seeing, not on
   what language it is in.

**Spectator sport is in, not excluded.** ATP tennis, 10k road races and similar sit around 60–70 on
`adult_fit`. The Botev–Lokomotiv Plovdiv derby is the interesting case: he would not attend, but the
city changes on derby day — crowds, traffic, closed streets. Route it to **`civic_notice`** as
something worth knowing, not to the recommendations. That distinction generalises: an event whose
value is "the city will be different that day" is civic, not recreational.

---

## 4. Open uncertainties

Load-bearing assumptions, inferred rather than stated. Revisit them once there are a few real
emails to judge.

1. **Masterpiece classification.** The gap between a 95-point masterpiece (*Blade Runner*, *2001*)
   and a 25-point obscurity (#04) rests on outside consensus — IMDb Top 250, Letterboxd cultural
   weight. 1980s and 1990s cult cinema is where this will be wrong first.
2. **Restaurant curation.** Because alcohol-led venues score near 20 (#08), dining picks depend
   entirely on spotting a genuine "unique concept or chef" signal versus a standard local grill.
   That signal is not always in a listing.

---

*Editing notes, for whoever reads this next.* Three things were changed on the way in from the
original interview, deliberately. A percentage-weighted four-axis rubric was dropped because its
arithmetic did not reproduce the exemplars above and could not score a civic item at all — the
exemplars govern instead. Language moved from a scored axis to the `language_barrier` gate, so it is
counted exactly once. And an axis that penalised adults-only evenings for babysitting friction was
removed outright: it demoted precisely the events this file exists to surface. Scores and item
descriptions in §2 are unchanged from the interview.
