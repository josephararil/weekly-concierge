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
    "programata", "plovdiv_bg", "visitplovdiv", "marica",
    "lostinplovdiv",
    # Adult + civic sources. "plovdiv24" was already registered in
    # scrapers.RAW_FETCH_SOURCES but never enabled here; it's local news, which is the
    # only route to road closures and outages (no utility publishes a usable feed).
    "plovdiv24", "programata_adult",
]

# Volume cap applied to the deduped harvest before it's handed to FIND.
MAX_HARVEST_ITEMS = 200

# ── LLM models ──────────────────────────────────────────────────────────────
# Per-stage model roles. Values are canonical Anthropic model names; Gemini
# equivalents are looked up in GEMINI_MODEL_MAP below.
MODEL_FIND      = "claude-haiku-4-5-20251001"  # Stage 1: fast + web-search capable, consolidates harvest+leads
MODEL_SKEPTIC   = "claude-haiku-4-5-20251001"          # Stage 2: stronger reasoning, the hallucination guard
MODEL_CONCIERGE = "claude-haiku-4-5-20251001"          # Stage 3: strong prose writer, no search

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
MAX_TOKENS_SKEPTIC   = 20000  # Stage 2: one verdict per candidate, but a large input batch
                              # was 12000 — the batch now spans both FIND paths
MAX_TOKENS_CONCIERGE = 24000  # Stage 3: full email prose (HTML + text) + thinking
                              # was 12000 — 4 sections, uncapped items, and the body is
                              # emitted twice (HTML + plain text)

# ── Web search ───────────────────────────────────────────────────────────────
# Maximum number of individual web-search tool uses allowed in a single stage
# call (Anthropic provider only; Gemini's google_search has no per-call cap).
WEB_SEARCH_MAX_USES = 6

# ── Coverage knobs ───────────────────────────────────────────────────────────
LOOKAHEAD_WEEKS      = 4    # how far ahead "notable events to plan for" reaches
WEEK_WINDOW_DAYS     = 7    # "happening soon" window: run-day+1 .. run-day+7 inclusive
                            # (Sat..next Fri on the Friday cron) — both audiences

# Score floors, 0-100. Each candidate carries exactly one score, chosen by its category
# (see weekend_concierge.floor_for / score_of). Evergreens are exempt from all of them —
# that exemption is what guarantees the email is never empty.
# An over-tight floor fails into a thin section, which is acceptable; an over-loose floor
# fails into spam, which loses trust permanently. Tune these from the [LOW-FIT ] lines in
# state/weekend_log.md after a few real runs.
MIN_INCLUDE_SCORE           = 50  # family_fit floor for family events
MIN_ADULT_SCORE             = 70  # adult_fit floor for adult events + evergreens
MIN_CIVIC_OPPORTUNITY_SCORE = 75  # civic_value floor for durable civic facts (a high bar)
MIN_CIVIC_NOTICE_SCORE      = 55  # civic_value floor for time-bounded notices (low bar —
                                  # missing a road closure is the worst failure mode)

# ── Anti-repeat knobs ────────────────────────────────────────────────────────
EVENT_TTL_DAYS          = 21  # cooldown before the same event can resurface
EVERGREEN_COOLDOWN_DAYS = 70  # cooldown before the same evergreen idea can resurface
CIVIC_OPPORTUNITY_COOLDOWN_DAYS = 180  # a new mall or flight route is interesting once;
                                       # the fact stays true for months

# ── Evergreen seed catalog ───────────────────────────────────────────────────
# Merged into state/memory.json's evergreen catalog on first run (see memory.record_evergreen).
# Optional fields per entry: url (official page → real "Details" link in the email) and
# practical (hours/fees/season/safety notes → injected into prompts for smarter prose).
# The research-sourced entries below come from a Gemini Deep Research sweep of family
# attractions within a ~90-min drive of Plovdiv (source="research"); refresh practical
# details periodically since small Bulgarian venues change hours/fees often.
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

    # ── Zone 1 · urban core & immediate periphery (< 30 min) ──────────────────
    {
        "name": "Tara Eco Farm & Horse Base",
        "location": "Yagodovo village, near Plovdiv",
        "area": "~15-20 min drive from Plovdiv",
        "description": "Farm and horse base with ostriches, emus, peacocks, mini-sheep and ponies, plus short guided pony rides in a safe arena.",
        "tags": ["animals", "farm", "outdoor", "paid"],
        "url": "https://konnabazatara.com",
        "practical": "Entry ~8 BGN (free under 2); pony rides usually weekends 11:00 & 13:00; open Tue-Sun 10:00-19:00.",
        "source": "research",
    },
    {
        "name": "Keffa Children's Farm",
        "location": "Stroevo village, near Plovdiv",
        "area": "~20-25 min drive from Plovdiv",
        "description": "A working dairy farm's educational annex — hands-on encounters with sheep, cows, pigs and donkeys plus simple workshops for young children.",
        "tags": ["animals", "farm", "educational", "outdoor", "paid"],
        "practical": "Open year-round; reserve ahead for non-group visits so staff are on hand.",
        "source": "research",
    },
    {
        "name": "Han Krum Horse Base",
        "location": "Voyvodinovo village, near Plovdiv",
        "area": "~15 min drive from Plovdiv",
        "description": "One of the region's largest equestrian centres, 150+ horses; kids can watch the animals and take beginner lessons.",
        "tags": ["animals", "horses", "outdoor", "paid"],
        "url": "https://khankrum.n.nu",
        "practical": "Open daily 09:00-18:00; children's lessons from ~15 BGN per 30 min.",
        "source": "research",
    },
    {
        "name": "Frigopan Horse Base",
        "location": "Tsaratsovo village, near Plovdiv",
        "area": "~15 min drive from Plovdiv",
        "description": "Modern 26-decare equestrian complex with indoor and outdoor arenas — clean, weather-proof pony rides and observation for toddlers.",
        "tags": ["animals", "horses", "indoor", "outdoor", "paid"],
        "url": "https://konnabazafrigopan.com",
        "practical": "Open year-round; book guided children's walks ahead.",
        "source": "research",
    },
    {
        "name": "Tangra Horse Base",
        "location": "Kalekovets village, near Plovdiv",
        "area": "~15-20 min drive from Plovdiv",
        "description": "Scenic riding base by the Stryama river; offers gentle 'vaulting' (gymnastics on horseback) recommended for the youngest children.",
        "tags": ["animals", "horses", "sports", "outdoor", "paid"],
        "url": "https://tangra-plovdiv.com",
        "practical": "Open Wed-Sun 08:00-18:00; call ahead (0897 093 887) to schedule.",
        "source": "research",
    },
    {
        "name": "Children's Railway 'Banner of Peace'",
        "location": "Youth Hill (Mladezhki Halm), Plovdiv",
        "area": "in town",
        "description": "Miniature narrow-gauge train on a 25-minute scenic loop through a tunnel around the hill — reliably fascinating for toddlers.",
        "tags": ["ride", "park", "outdoor", "cheap"],
        "practical": "Tickets ~1 BGN; Wed-Sun (closed Mon/Tue); midday break around 13:00; shorter winter hours.",
        "source": "research",
    },
    {
        "name": "Lauta Rope Park",
        "location": "Park Lauta, Trakiya District, Plovdiv",
        "area": "in town",
        "description": "Forest-shaded adventure park; the 'Small Circle' (ages 5-10) is often managed by an agile 4-year-old with close parental spotting.",
        "tags": ["adventure", "active", "outdoor", "paid"],
        "practical": "Open daily ~10:00/11:00-19:00 (seasonal); small-circle pass ~5-6 BGN.",
        "source": "research",
    },
    {
        "name": "Aviation Museum",
        "location": "Krumovo, next to Plovdiv Airport",
        "area": "~15-20 min drive from Plovdiv",
        "description": "Open-air museum with dozens of aircraft and helicopters; big machines and open grounds let a toddler roam and gawp.",
        "tags": ["museum", "educational", "outdoor", "paid"],
        "url": "https://airmuseum-bg.com",
        "practical": "Wed-Sun; summer 09:00-18:00, winter to 16:30; children under 7 free.",
        "source": "research",
    },
    {
        "name": "Fantasy NeMuseum",
        "location": "Central Plovdiv (Stage Park area)",
        "area": "in town",
        "description": "Hands-on, sensory-rich space of colourful installations and analog games with no 'do not touch' rules — a good rainy-day option.",
        "tags": ["indoor", "interactive", "art", "paid"],
        "practical": "Entry roughly 25 BGN for a parent + child combo.",
        "source": "research",
    },
    {
        "name": "State Puppet Theatre Plovdiv",
        "location": "14 Hristo G. Danov St, Plovdiv",
        "area": "in town",
        "description": "Long-running puppet theatre; shows are paced and lit for preschool attention spans, with standard weekend-morning performances.",
        "tags": ["theater", "indoor", "cultural", "paid"],
        "url": "https://puppet.bg",
        "practical": "Weekend morning shows are typical; check the site for current repertoire and tickets.",
        "source": "research",
    },
    {
        "name": "Bubbu Bear Children's Center",
        "location": "141 Komatevsko Shose Blvd, Plovdiv",
        "area": "in town",
        "description": "Play centre with an enclosed outdoor 'Park Zone' of inflatables and green space where a 4-year-old can run within a secure perimeter.",
        "tags": ["playground", "indoor", "outdoor", "paid"],
        "practical": "Outdoor park zone is seasonal; general hours Sat-Sun 10:00-23:00, weekdays from 12:00.",
        "source": "research",
    },
    {
        "name": "Momina Skala Ecotrail",
        "location": "Izvor village, above Hrabrino",
        "area": "~30 min drive from Plovdiv",
        "description": "Flat, child-friendly forest trail dotted with small chapels, gazebos and fountains — an easy ~40-minute walk.",
        "tags": ["hiking", "nature", "outdoor", "free"],
        "practical": "Park at the Izvor municipality building; red-and-white markings; hold hands near the final viewpoint cliff.",
        "source": "research",
    },
    {
        "name": "Ravnishta Ecotrail",
        "location": "Dedovo village",
        "area": "~30 min drive from Plovdiv",
        "description": "Short ~30-minute trail on a wide dirt path to a panoramic rock overlook — a good first mountain hike for a small child.",
        "tags": ["hiking", "nature", "outdoor", "free"],
        "practical": "Follow the yellow markers; supervise closely at the final cliff edge.",
        "source": "research",
    },

    # ── Zone 2 · near periphery (30-60 min) ───────────────────────────────────
    {
        "name": "Eco Park Stamboliyski",
        "location": "Central Stamboliyski",
        "area": "~30 min drive from Plovdiv (20 km)",
        "description": "Zoned municipal park with a dedicated toddler area (ages 0.5-6): wooden climbing structures, sensory boards and safe ground cover.",
        "tags": ["playground", "park", "outdoor", "free"],
        "practical": "Free access; on-site mini-golf ~2 BGN, Wed-Sun 08:00-20:00 with a midday break.",
        "source": "research",
    },
    {
        "name": "The Fairytale Forest",
        "location": "Rakovski (between Sekirovo and Gen. Nikolaevo)",
        "area": "~35-40 min drive from Plovdiv",
        "description": "Themed walking park of life-sized painted Bulgarian folktale characters, recently expanded with 3D-printed models of the Seven Wonders.",
        "tags": ["park", "statues", "outdoor", "free"],
        "practical": "Free municipal park, accessible year-round.",
        "source": "research",
    },
    {
        "name": "Park-Island 'Freedom'",
        "location": "River island, Pazardzhik",
        "area": "~45 min drive from Plovdiv (36 km)",
        "description": "Huge 300-decare car-free island with a free zoo (tigers, monkeys, deer), a dinosaur corner and fairytale displays.",
        "tags": ["park", "zoo", "playground", "outdoor", "free"],
        "practical": "Open 06:00-00:30 daily; dogs prohibited; free parking at the entrance.",
        "source": "research",
    },
    {
        "name": "Krichim Palace and Park",
        "location": "Krichim",
        "area": "~35-40 min drive from Plovdiv",
        "description": "Former royal hunting lodge in a botanical park; flat, stroller-friendly paths, free-roaming peacocks and greenhouses.",
        "tags": ["botanical", "nature", "outdoor", "paid"],
        "practical": "Guided tours (~1.5 hr) at fixed times, e.g. 08:15/10:15/13:00/15:00; ticket ~14 BGN.",
        "source": "research",
    },
    {
        "name": "Paleontological Museum Asenovgrad",
        "location": "Badelema district, Asenovgrad",
        "area": "~30 min drive from Plovdiv",
        "description": "Home to a life-sized Deinotherium skeleton whose sheer scale captivates young children without a long visit.",
        "tags": ["museum", "fossils", "indoor", "paid"],
        "url": "https://www.nmnhs.com/palaeontological-museum-in-asenovgrad-bg.html",
        "practical": "Mon-Fri 08:30-17:00 (weekend hours vary); under 3 free, adults ~4 BGN.",
        "source": "research",
    },
    {
        "name": "Peristera Fortress",
        "location": "Sveta Petka Hill, Peshtera",
        "area": "~50-60 min drive from Plovdiv (40 km)",
        "description": "Restored ancient fortress with paved walkways, safety railings and rest gazebos — a rare ruin a 4-year-old can safely explore.",
        "tags": ["history", "fortress", "outdoor", "paid"],
        "practical": "Wed-Sun 09:00-17:00 (later in summer), closed Mon/Tue; adult ~4 BGN; free parking at the base.",
        "source": "research",
    },
    {
        "name": "Cars of Socialism Museum",
        "location": "56 Mihail Takev St, Peshtera",
        "area": "~50-60 min drive from Plovdiv",
        "description": "Quirky indoor collection of brightly coloured retro cars and mid-century artifacts — a fast, colourful ~10-minute visit.",
        "tags": ["museum", "retro", "indoor", "paid"],
        "practical": "Open daily 10:00-18:00 (to 17:00 in winter); entry ~6 BGN.",
        "source": "research",
    },
    {
        "name": "Wild City Ranch",
        "location": "Near Dolnoslav, south of Asenovgrad",
        "area": "~45 min drive from Plovdiv",
        "description": "Rustic horse base in a valley with a small zoo corner, grazing areas and outdoor play structures — a quiet nature afternoon.",
        "tags": ["animals", "farm", "outdoor", "food"],
        "practical": "Final approach is a rough dirt track (~15 min careful driving); on-site restaurant.",
        "source": "research",
    },
    {
        "name": "Trakiets Equestrian Complex",
        "location": "Zhitnitsa",
        "area": "~35 min drive from Plovdiv",
        "description": "Boutique equestrian complex; day visitors can watch the horses, use outdoor child play areas and dine in a polished, stroller-friendly setting.",
        "tags": ["animals", "horses", "premium", "food", "outdoor"],
        "practical": "Has hotel facilities, but the grounds are open to day-trippers.",
        "source": "research",
    },
    {
        "name": "The Parks of Hisarya",
        "location": "Central Hisarya",
        "area": "~50-60 min drive from Plovdiv",
        "description": "Interconnecting landscaped parks over Roman ruins with flat paved walkways, streams and tame squirrels — highly sensory and safe for toddlers.",
        "tags": ["park", "nature", "ruins", "outdoor", "free"],
        "practical": "Parks are free; entering the excavated Roman Baths/Tomb is a small combined fee (~7 BGN).",
        "source": "research",
    },
    {
        "name": "Batak Reservoir & Tsigov Chark",
        "location": "Tsigov Chark resort area",
        "area": "~60 min drive from Plovdiv (73 km)",
        "description": "Open lakeside meadows for running and picnics, with slow pedal boats to rent and free-roaming horses to spot.",
        "tags": ["water", "nature", "outdoor", "free"],
        "practical": "High altitude — bring layered clothing; pedal boats operate seasonally.",
        "source": "research",
    },

    # ── Zone 3 · wider region (60-90 min) ─────────────────────────────────────
    {
        "name": "Pliocene Park Dorkovo",
        "location": "Dorkovo, near Tsigov Chark",
        "area": "~80-90 min drive from Plovdiv (83 km)",
        "description": "Modern museum over a fossil dig with a spectacular life-sized mastodon model; a single enclosed room scaled to a 4-year-old's attention span.",
        "tags": ["museum", "fossils", "indoor", "paid"],
        "practical": "Usually open daily 10:00-17:00/18:00; visit includes short ~10-minute educational talks.",
        "source": "research",
    },
    {
        "name": "Planetarium Giordano Bruno",
        "location": "Park Nikola Vaptsarov, Dimitrovgrad",
        "area": "~70 min drive from Plovdiv",
        "description": "Bulgaria's first planetarium, set in a leafy park; immersive dome star projections make a mesmerising, low-stamina indoor activity.",
        "tags": ["science", "educational", "indoor", "paid"],
        "url": "https://www.naopjbruno.bg",
        "practical": "Summer (Jul-Aug) Wed-Sun 10:00-16:00; projections at fixed times, e.g. 11:00 & 14:00.",
        "source": "research",
    },
    {
        "name": "Maritsa Park Dimitrovgrad",
        "location": "Dimitrovgrad",
        "area": "~70 min drive from Plovdiv",
        "description": "Recently renovated park with big new wooden adventure playgrounds, sandpits and wide paths — one of the region's best free play spaces.",
        "tags": ["park", "playground", "outdoor", "free"],
        "practical": "Free; pristine pathways suit scooters and strollers.",
        "source": "research",
    },
    {
        "name": "Penyo Penev Park",
        "location": "Dimitrovgrad",
        "area": "~70 min drive from Plovdiv",
        "description": "Memorial park built around a water cascade of 16 interconnected lakes and streams with lily pads and fish — a lively walking tour.",
        "tags": ["park", "lakes", "outdoor", "free"],
        "practical": "Free; the lakes can suffer summer algae blooms.",
        "source": "research",
    },
    {
        "name": "Kenana Rope Park & Zoo",
        "location": "Kenana Park, Haskovo",
        "area": "~75-85 min drive from Plovdiv",
        "description": "Big forested park with a free shaded zoo plus a newer rope/adventure park of low-level obstacles suited to active youngsters, and wide cycling alleys.",
        "tags": ["zoo", "rope park", "active", "outdoor", "free", "paid"],
        "practical": "Zoo is free; rope park ~6 BGN; open daily 10:00-21:00 in summer.",
        "source": "research",
    },
    {
        "name": "Zagorka Lake & Bedechka Park",
        "location": "Stara Zagora",
        "area": "~75 min drive from Plovdiv",
        "description": "Picturesque lake in a green park — feed ducks and swans, rent pedal boats, use the climbing frames, see a 650-year-old plane tree.",
        "tags": ["water", "park", "outdoor", "free"],
        "practical": "Free; lake edges are unfenced, so keep toddlers close.",
        "source": "research",
    },
    {
        "name": "Ayazmoto Park & Hall of Laughter",
        "location": "Stara Zagora",
        "area": "~75 min drive from Plovdiv",
        "description": "Sprawling park with a 'Hall of Laughter' (curved mirrors) and a free municipal children's rope park with harnesses and instructors.",
        "tags": ["park", "active", "indoor", "outdoor", "free"],
        "practical": "Rope park provides harnesses/instructors free; Hall of Laughter is a cheap, repeatable sensory stop.",
        "source": "research",
    },
    {
        "name": "Damascena Biopark",
        "location": "Skobelevo, near Pavel Banya",
        "area": "~80-90 min drive from Plovdiv",
        "description": "A rose distillery grown into an ecological park with free-roaming deer, swans and ostriches, bronze statues and rose gardens.",
        "tags": ["animals", "botanical", "outdoor", "paid"],
        "url": "https://www.damascena.net",
        "practical": "Open daily 09:00-17:00; adults ~24 BGN, children under 7 free.",
        "source": "research",
    },
    {
        "name": "Four Seasons Ostrich Farm",
        "location": "Skobelevo, near Pavel Banya",
        "area": "~80-90 min drive from Plovdiv",
        "description": "Quirky farm where children safely watch ostriches, with memorable treats like ostrich-egg crème caramel.",
        "tags": ["animals", "farm", "outdoor", "paid"],
        "url": "https://www.shtraus.com",
        "practical": "Open Wed-Sun 10:00-17:00; pairs well with the nearby Damascena Biopark.",
        "source": "research",
    },
    {
        "name": "Sopot Chairlift",
        "location": "Sopot",
        "area": "~60-70 min drive from Plovdiv",
        "description": "Bulgaria's longest passenger chairlift; the open-air ride is a big, thrilling experience, with paragliders to watch and gelato at the base.",
        "tags": ["ride", "mountain", "outdoor", "paid"],
        "url": "https://www.lift-sopot.com",
        "practical": "~22 min to the first station; runs ~09:00-17:30 (seasonal); highly wind-dependent — check before going.",
        "source": "research",
    },
    {
        "name": "Rhodope Narrow-Gauge Railway",
        "location": "Septemvri station",
        "area": "~45 min drive to the station from Plovdiv",
        "description": "Bulgaria's only narrow-gauge line; the slow, rhythmic ~1.5-hr ride through the Chepino Gorge toward Velingrad is a big hit with toddlers.",
        "tags": ["train", "ride", "outdoor", "cheap"],
        "practical": "Park at Septemvri and board; check the BDZ (Bulgarian State Railways) timetable for departures.",
        "source": "research",
    },
    {
        "name": "Byala Reka Ecotrail",
        "location": "Near Kalofer",
        "area": "~80-90 min drive from Plovdiv (70 km)",
        "description": "Child-friendly canyon trail of ~1.8 km over a series of sturdy wooden bridges — the constant bridge-crossing feels like a playground.",
        "tags": ["hiking", "nature", "outdoor", "free"],
        "practical": "Reached via a dirt road past the Kalofer Monastery; very crowded on summer weekends.",
        "source": "research",
    },
    {
        "name": "Kostenets Waterfall",
        "location": "Kostenets",
        "area": "~80-90 min drive from Plovdiv (95 km)",
        "description": "One of the country's most accessible waterfalls — a flat 2-3 minute stroll from parking (with restaurants and amenities) to a 10-metre fall.",
        "tags": ["water", "nature", "outdoor", "free"],
        "practical": "Extremely easy access; a zero-stress nature stop with a toddler.",
        "source": "research",
    },
]

# ── Adult evergreen seed catalog ─────────────────────────────────────────────
# Seeded with audience="adult" so they can never fill a family evergreen slot (see
# weekend_concierge's seed loop and select_evergreens).
#
# These 10 are UNVALIDATED PRIORS, not researched entries like the family ones above.
# They exist so the adult section is never bare in the first few weeks — which is exactly
# when the owner is judging whether this feature works at all. Edit, delete or replace
# them freely as real taste data arrives; nothing downstream depends on any single entry.
#
# Note "The Parks of Hisarya" already exists in SEED_EVERGREEN as a family entry. It is
# semantically adjacent to "Hisarya thermal spa town" below but a different name, so there
# is no key collision — left as two entries deliberately, since the family draw (parks) and
# the adult draw (thermal baths) are genuinely different visits.
SEED_EVERGREEN_ADULT = [
    {
        "name": "Kapana Creative District",
        "location": "Kapana, Plovdiv",
        "area": "central, walkable",
        "description": "The city's small-bar, gallery and street-art quarter — the default answer to 'where is something on tonight'.",
        "tags": ["nightlife", "food", "art", "central"],
        "practical": "Busiest Thu-Sat evenings; most venues are food-led rather than drink-led.",
        "source": "seed-adult",
    },
    {
        "name": "Plovdiv City Art Gallery",
        "location": "Plovdiv",
        "area": "in town",
        "description": "Permanent Bulgarian painting collection across several central buildings — visual, so no language barrier.",
        "tags": ["art", "indoor", "central"],
        "source": "seed-adult",
    },
    {
        "name": "Bunardzhik Hill sunset viewpoint",
        "location": "Bunardzhik (Hill of the Liberators), Plovdiv",
        "area": "central, walkable",
        "description": "A short climb to the best free view over the city — a genuinely good hour around sunset.",
        "tags": ["outdoor", "free", "view", "central"],
        "practical": "Paved path most of the way; nothing to book, nothing to pay.",
        "source": "seed-adult",
    },
    {
        "name": "Roman Stadium of Philippopolis",
        "location": "Dzhumaya Square, Plovdiv",
        "area": "central, walkable",
        "description": "The excavated end of a 240-metre Roman stadium sunk beneath the main pedestrian street.",
        "tags": ["history", "central", "free"],
        "practical": "The street-level section is free; the small multimedia exhibition is ticketed.",
        "source": "seed-adult",
    },
    {
        "name": "Trakart Cultural Centre",
        "location": "Under the Archaeological Underpass, Plovdiv",
        "area": "in town",
        "description": "Roman mosaics from a late-antique house, shown in situ under the road, plus a glass and antiquities collection.",
        "tags": ["history", "art", "indoor"],
        "source": "seed-adult",
    },
    {
        "name": "Regional Ethnographic Museum Plovdiv",
        "location": "Old Town, Plovdiv",
        "area": "in town",
        "description": "A National Revival merchant's house in the Old Town, worth it as much for the building as the collection.",
        "tags": ["history", "architecture", "indoor"],
        "source": "seed-adult",
    },
    {
        "name": "Dom na Kinoto Plovdiv",
        "location": "Plovdiv",
        "area": "in town",
        "description": "The arthouse cinema — the one local venue likely to screen something subtitled rather than dubbed.",
        "tags": ["cinema", "indoor", "central"],
        "practical": "Check whether a given screening is subtitled or dubbed before committing.",
        "source": "seed-adult",
    },
    {
        "name": "Tsar Simeon's Garden & Singing Fountains",
        "location": "Plovdiv",
        "area": "central, walkable",
        "description": "The big central park, with an evening light-and-music fountain show in the warmer months.",
        "tags": ["outdoor", "free", "central", "evening"],
        "practical": "Fountain shows run seasonally after dark; the garden itself is open year-round.",
        "source": "seed-adult",
    },
    {
        "name": "Hisarya thermal spa town",
        "location": "Hisarya",
        "area": "~40 min drive from Plovdiv",
        "description": "Mineral baths and Roman walls — the nearest thing to a spa day without a real drive.",
        "tags": ["spa", "daytrip", "history"],
        "source": "seed-adult",
    },
    {
        "name": "Shiroka Laka & the Rhodope villages drive",
        "location": "Shiroka Laka, Rhodope Mountains",
        "area": "~90 min drive from Plovdiv",
        "description": "Architectural-reserve village of stone and timber houses, at the far edge of the radius — a full day, not an outing.",
        "tags": ["daytrip", "architecture", "nature", "scenic-drive"],
        "practical": "At the 90-minute limit; mountain roads, so poor weather makes it a bad idea.",
        "source": "seed-adult",
    },
]

# ── LLM prompts ─────────────────────────────────────────────────────────────
# There are TWO independent FIND paths, family and adult, because the family path's quality
# comes from a NARROW brief. One widened brief ("find family things and adult things and civic
# news") degrades both. They share one SKEPTIC and one CONCIERGE.
#
# Placeholders filled at runtime by weekend_concierge.py / common.py:
#   SEARCH_FAMILY_PROMPT    → {today}, {home_area}, {radius_minutes}, {lookahead_weeks}
#   SEARCH_ADULT_PROMPT     → {today}, {home_area}, {radius_minutes}, {lookahead_weeks}
#   SEARCH_RESULTS_PREAMBLE → {leads}             (Gemini reasoning step — injected ahead of FIND)
#   FIND_FAMILY_PROMPT      → {today}, {home_area}, {radius_minutes}, {lookahead_weeks},
#                              {window_start}, {window_end}, {harvest}, {memory}, {feedback},
#                              {search_directive}
#   FIND_ADULT_PROMPT       → the same, plus {taste}
#   SKEPTIC_PROMPT          → {today}, {home_area}, {radius_minutes}, {candidates}, {memory}
#   CONCIERGE_PROMPT        → {today}, {home_area}, {candidates}, {weather}, {feedback},
#                              {memory}, {taste}
# Use {{...}} for literal braces in the JSON schema examples (Python .format() escaping).

# PROMPT SPECS (apply throughout): all inputs may be in Bulgarian (scraped pages, search
# results) — read them, but ALWAYS write output in English. Everything must be within a
# ~{radius_minutes}-minute travel radius of {home_area}, and nothing requiring arduous travel.
# The family path additionally requires everything to suit a 4-year-old; the adult path does
# not, and must never apply that filter.

# ── Gemini search/reasoning split (see common._gemini) ───────────────────────
# On Gemini, want_search calls run in two steps. SEARCH_FAMILY_PROMPT / SEARCH_ADULT_PROMPT
# drive step 1 (lead generation on the lite model with google_search); SEARCH_RESULTS_PREAMBLE
# frames step 1's output for step 2 (the flagship reasoner, which has no live search tool).
# These are Gemini-only. On Anthropic the flagship searches inline via the FIND prompts.

SEARCH_FAMILY_PROMPT = """Today is {today}. You are a local scout running live web searches to find weekend activities for a family of 3 (2 adults + a 4-year-old) based in {home_area}. The household is English-speaking and misses most local happenings because they live in Bulgarian on municipal sites, ticketing platforms, and Facebook.

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

SEARCH_ADULT_PROMPT = """Today is {today}. You are a local scout running live web searches for two adults based in {home_area}. One of them does not speak Bulgarian, reads the FT and The Economist, has no TV and no local news — so he is effectively invisible to his own city. Things happen 400 metres from his flat and he never hears about them.

Your ONLY job in this step is to surface FRESH, SPECIFIC LEADS from the live web — raw material for an analyst who works downstream. You are NOT deciding what's worth going to, and you are NOT writing the final answer.

Search in BOTH Bulgarian and English. Most of what matters here is published only in Bulgarian; search Bulgarian first and translate what you find. Report every lead in English.

### YOU ARE HUNTING TWO DIFFERENT THINGS

**1. Adult culture worth an evening.** Live comedy (especially anything in English), cinema, concerts and DJ sets, exhibitions, theatre, talks and lectures, notable restaurant openings, sport.

- **Cinema needs explicit effort.** Search for what is actually screening in Plovdiv cinemas this week, by name. There is no scrapeable Plovdiv cinema listing anywhere — every site is either client-rendered or Sofia-only — so live search is the ONLY route by which a film reaches this household. Try the cinema names and "Пловдив кино програма" / "какво се играе" style queries, and report individual film titles with dates where you can find them. Say whether a screening is subtitled or dubbed if the listing states it; do not guess.
- Note the performance language whenever the source states it. Do not infer it — a listing usually doesn't say.

**2. Local civic facts a resident would absorb from radio and TV.** This is the half he values most, and it splits in two:

- **Durable facts** — a new shopping centre, supermarket or store opening; a new direct flight route from Plovdiv airport; a new park, bridge, pool or public building; a permanent change to how the city works.
- **Time-bounded notices** — scheduled water or power outages; road closures, roadworks and parking changes; public transport changes; protests or marches; anything that will make central Plovdiv different on a given day, including a big football match at Botev or Lokomotiv (the crowds and traffic are the point, not the football).

For civic leads, **proximity to central Plovdiv matters enormously and language matters not at all.** A Bulgarian-only outage notice for a central street is a top-value lead. Prefer local news (marica.bg, plovdiv24.bg, plovdiv.bg) and the municipality's own announcements.

### WHAT MAKES A GOOD LEAD
- Specific: who/what, when, where. Concrete enough for someone else to verify.
- Happening between {today} and roughly the next {lookahead_weeks} weeks — with the next 7 days being the most valuable window by far.
- Within roughly a {radius_minutes}-minute drive of {home_area}. Sofia counts only for something genuinely worth the drive. A civic fact about central Plovdiv beats a cultural event an hour away.
- Hard to know WITHOUT searching today. Surface what is actually posted, not "Plovdiv has a lively cultural scene" filler.

### DOs AND DON'Ts
- DO surface 8-15 distinct leads, and DO mix culture and civic rather than returning only one kind.
- DO include a lead even if you're unsure it's a great fit; the analyst will filter.
- DON'T invent an event, an outage or a road closure you have no search signal for. A fabricated disruption is worse than a missed one.
- DON'T include: pop-chalga or generic club nights; crypto, MLM or "wealth building" meetups; Bulgarian-dubbed international films; religious processions; student or amateur art showcases.
- DON'T add introduction or closing remarks. Start directly with the first lead.
- DON'T score, rank, or output JSON. Just a clean bulleted list, one lead per block in exactly this shape:

* **Item:** <name, or a one-line statement of the civic fact>
* **Kind:** culture | civic
* **Date:** <specific date(s), or "ongoing" for a durable civic fact>
* **Location:** <venue, street, town — as specific as the source allows>
* **Language:** <performance/announcement language if the source states it, else "not stated">
* **Description:** <one line, what it is and why these two adults would want to know>
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

FIND_FAMILY_PROMPT = """Today is {today}. You are a local activities scout for a family of 3 (2 adults + a 4-year-old) based in {home_area}. Your job is to consolidate everything available — a scraper harvest of Bulgarian event/ticketing/municipal pages, web search leads, and your own knowledge — into a clean, structured list of candidate weekend activities.

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
The coming week runs from {window_start} to {window_end} inclusive. Classify each candidate into exactly one of:
- `event_this_weekend`: happens this coming Saturday or Sunday.
- `event_thisweek`: happens between {window_start} and {window_end} but NOT on the Saturday or Sunday — a weekday event in the coming week.
- `event_lookahead`: happens within the next {lookahead_weeks} weeks but after {window_end}.
- `evergreen`: an always-available idea (zoo, museum, park, hike) rather than a dated event. Pull from the off-cooldown evergreen list in MEMORY, or propose a new one if you have strong knowledge of a suitable always-available spot.

A weekday event is worth proposing, but be honest in `family_fit` about whether a 4-year-old can realistically attend a Tuesday evening: a weekday morning or late-afternoon thing is fine, a 20:00 start is not.

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


FIND_ADULT_PROMPT = """Today is {today}. You are a local scout for two adults based in {home_area}. Your job is to consolidate everything available — a scraper harvest of Bulgarian event/ticketing/municipal/news pages, web search leads, and your own knowledge — into a clean, structured list of candidates.

One of them does not speak Bulgarian, reads the FT and The Economist, and has no TV and no local news. The result is that he is illegible to his own city: things happen 400 metres from his flat and he never hears about them. You exist to fix that.

All source material may be in Bulgarian. Read it, but write every field in ENGLISH.

**This is the adult path. Do NOT filter for child-suitability.** A separate scout handles the family side. Assume childcare is always available — never skip or downrate an adults-only evening for being adults-only.

Only propose things within roughly a {radius_minutes}-minute drive of {home_area}, with one deliberate exception noted under `civic_opportunity` below.

---

### HARVESTED MATERIAL (scraped from Bulgarian sources — raw text blobs or structured listings)
{harvest}

---

### TASTE CALIBRATION (the owner's own scored examples — this is how you score, not a suggestion)
{taste}

---

### HOUSEHOLD FEEDBACK (hand-edited notes — weight it)
{feedback}

---

### MEMORY (evergreen ideas off cooldown + recently suggested — avoid stale repeats, evergreens are safe to re-suggest)
{memory}

---

### SEARCH RULES
{search_directive}

---

### CATEGORIES
The coming week runs from {window_start} to {window_end} inclusive. Classify each candidate into exactly one of six:

- `event_this_weekend`: a dated thing happening this coming Saturday or Sunday.
- `event_thisweek`: a dated thing between {window_start} and {window_end} but not on the Saturday or Sunday. Weekday evenings are GOOD here — this is when most adult culture actually happens.
- `event_lookahead`: a dated thing within the next {lookahead_weeks} weeks but after {window_end}.
- `evergreen`: an always-available place or standing option rather than a dated event. **A recurring weekly fixture belongs here, not in the event categories** — a weekly English open-mic night is definitionally always available, and treating it as a dated event would resurface it roughly 17 times a year and teach the reader to skim past everything.
- `civic_opportunity`: a DURABLE local fact worth knowing once, which stays true for months. A new shopping centre, supermarket or store; a new direct flight route from Plovdiv airport; a new park, pool, bridge or public building; a permanent change to how the city works.
- `civic_notice`: a TIME-BOUNDED local fact, inside {window_start}..{window_end}. A scheduled water or power outage; a road closure, roadworks or parking change; a public transport change; a protest or march; a big football match at Botev or Lokomotiv Plovdiv (the crowds, traffic and closed streets are the point — route it here as something worth knowing, never as a recommendation).

The axis between the two civic categories is **durability, not whether it has a date**. A new mall stays interesting for months; a road closure and a derby do not.

---

### SCORING

Each candidate gets **exactly one** score. Never both.

**`adult_fit` (0-100)** — for `event_*` and `evergreen`. Score it against the exemplars in TASTE CALIBRATION above. Those exemplars are the calibration; do not invent your own scale and do not compute a weighted average. Find the exemplar an item most resembles and score near it. Note especially the counter-intuitive ones: an alcohol-led event (wine tasting, cocktail evening) scores near 20 no matter how sophisticated it looks, and cultural prestige alone (a grand opera in a landmark venue) does not earn a high score.

**`civic_value` (0-100)** — for `civic_opportunity` and `civic_notice` only. A completely separate scale: it measures how badly he needs to KNOW this, not whether anyone would enjoy it. Nobody attends a water outage. On this scale:
- **Location matters enormously.** A disruption to a central Plovdiv street is close to 100. The same disruption in a town 60 minutes away is near zero.
- **Language is irrelevant.** A Bulgarian-only municipal outage notice is a top-value item.
- Missing a disruption is the worst thing this pipeline can do, so err toward including a plausible one — but never toward inventing one.

### `language_barrier` — for `event_*` and `evergreen` only

`none` | `partial` | `blocking`.

- `blocking` — the thing is unattendable without Bulgarian. Spoken-narrative comedy, Bulgarian-language theatre, a local panel discussion, Bulgarian-dubbed cinema. This REMOVES the item downstream, so use it decisively when it's true.
- `partial` — mostly accessible with some loss. Instrumental music with Bulgarian-only programme notes; a film with Bulgarian subtitles rather than English; an exhibition with Bulgarian-only captions.
- `none` — original English audio, or non-verbal (instrumental, electronic, dance, visual art, sport, food).

**It must be ESTABLISHED, not assumed.** An event listing very rarely states its language. If the source doesn't say and you cannot infer it from the performer or the form, choose `partial` and say so in `reason` — do not guess `blocking` and silently delete something, and do not guess `none` and set him up for an unattendable evening.

### HARD EXCLUSIONS — never propose these at all
Pop-chalga and generic nightclub party nights. Crypto, MLM and "wealth building" meetups. Bulgarian-dubbed international cinema. Religious processions and Orthodox church ceremonies. Student art showcases and amateur local theatre. The last two are excluded because they are boring — low-stakes ceremonial or amateur content with nothing to see — not because of language.

Spectator sport is NOT excluded: tennis, road races and similar are welcome as events, and a local football derby belongs in `civic_notice`.

---

### OUTPUT FORMAT
Return JSON only. Do not include markdown formatting or wrappers like ```json.

Field notes:
- when_text: human-readable date/time as found in the source (e.g. "Thursday, 20:00" or "August 15-17"). For a durable civic fact, describe the timing plainly ("opening in September", "announced this week").
- date_iso: best-guess ISO date (YYYY-MM-DD) if determinable, else null. For a multi-day event, use the start date. A `civic_opportunity` often has no meaningful date — null is correct there.
- location: specific venue, street or area. For a `civic_notice` this is the most important field you write — name the street or district if the source does.
- adult_fit: include ONLY for `event_*` and `evergreen`. Omit for civic items.
- civic_value: include ONLY for `civic_opportunity` and `civic_notice`. Omit for everything else.
- language_barrier: include ONLY for `event_*` and `evergreen`. Omit for civic items.
- source_url: the URL you found this from, or "" if from general knowledge only.
- confidence: "high" | "medium" | "low" — how sure you are this is real and correctly dated.

JSON Schema:
{{
  "candidates": [
    {{
      "title": "Event, place, or a one-line statement of the civic fact",
      "category": "event_thisweek",
      "when_text": "Thursday, 20:00",
      "date_iso": "2026-08-13",
      "location": "Kapana, Plovdiv",
      "adult_fit": 85,
      "language_barrier": "none",
      "reason": "One line on why these two adults would want this, and what you established about the language.",
      "source_url": "https://...",
      "confidence": "high"
    }},
    {{
      "title": "Scheduled 8-hour water outage on Mitropolit Panaret and surrounding streets",
      "category": "civic_notice",
      "when_text": "Wednesday, 08:00-16:00",
      "date_iso": "2026-08-12",
      "location": "Mitropolit Panaret street, central Plovdiv",
      "civic_value": 95,
      "reason": "Directly affects the home and is announced only in Bulgarian.",
      "source_url": "https://...",
      "confidence": "high"
    }}
  ]
}}

If nothing worth proposing was found, return {{"candidates": []}}."""


SKEPTIC_PROMPT = """You are a skeptical fact-checker with live web search access, reviewing a batch of proposed items for a household in {home_area}: two adults and a 4-year-old. The batch mixes three kinds of thing — activities for the family, culture for the adults, and local civic facts — and each candidate says which it is via its `category` and `audience` fields. The desirability of these items has already been scored — that is NOT your job.

Today is {today}.

### YOUR ONLY JOB: VERIFY, DON'T CURATE
For each candidate, verify:
1. **Real existence** — does this event/place/fact actually exist? Search for it.
2. **Correct date** — is the stated date right? If you find a different real date, CORRECT it; don't kill it for having a wrong date.
3. **Within radius** — is it within roughly a {radius_minutes}-minute drive of {home_area}? (See the civic carve-out below.)

You do NOT judge desirability, excitement, quality, or who an item is suitable for — all of that has already been scored upstream and is not your concern. In particular: do NOT kill an adult-audience item for being unsuitable for a 4-year-old, and do NOT kill a family item for being childish. Suitability is a scoring question that has already been answered. You ONLY remove or correct candidates that fail the checks above. Evergreen-category candidates are known-real by construction (they come from a maintained catalog) — verify only the radius for those, not existence.

### CIVIC ITEMS (`civic_opportunity`, `civic_notice`)
For these, verify the fact is **real and current** — a rumoured or merely proposed mall is not an opening, and an outage that has already happened is no longer a notice. Search for confirmation the same way you would for an event.

**Never kill a `civic_opportunity` on the radius check.** A new flight route, an airline announcement, or a change in national rules has no venue within 90 minutes of anywhere, because it has no venue at all. The radius test simply does not apply to this category — skip it. Apply the radius normally to `civic_notice`, where a street or district is the whole point.

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

verdict: "keep" (verified or plausible, no changes needed) | "correct" (real, but date/location was wrong — fill corrected_date_iso and/or corrected_location) | "kill" (not real, already past, or clearly outside the travel radius — explain in note). Remember that `civic_opportunity` is exempt from the radius test, and that suitability for any particular person is never a reason to kill."""


CONCIERGE_PROMPT = """Today is {today}. You are a warm, knowledgeable personal concierge writing a short weekly email for a household in {home_area}: two adults called Joseph and Marti, and their 4-year-old, Sophie. They have no TV, don't read local news, and Joseph doesn't speak Bulgarian — so they rely entirely on this email to know what's happening in their own city, both for the three of them together and for the two adults alone.

Write in a warm, conversational tone — like a friend who keeps track of the city for you. This is a SOFT ITINERARY, not a schedule and never a scoreboard.

**This must read as ONE email from one person, not two newsletters stapled together.** Family things and adult things sit side by side in the same sections, ordered by when they happen, and the prose moves between them naturally — "and once she's in bed, there's…" rather than a hard partition. Never label a section by audience, and never imply the adult items are an afterthought or the family items a chore.

---

### POTENTIAL CANDIDATES (already fact-checked; scores are for your prioritization only, never show them)
{candidates}

Each candidate carries an `audience` field, `"family"` or `"adult"`, telling you who it's for. Use it to pitch the item correctly — a family item is something all three do together, an adult item is an evening or a daytime thing for Joseph and Marti. Never print the field itself.

**Scores are internal.** Each candidate carries exactly one of `family_fit`, `adult_fit` or `civic_value`. All three are ranking signals for you alone: never show a number, never rank items visibly, never write anything like "family_fit: 82", "scores 90 on taste" or "our top pick rated highest". Higher-scoring items simply come first and get a little more warmth.

---

### THE WEEK'S WEATHER (real forecast data from open-meteo — a best-effort estimate, not a certainty)
{weather}

This is actual forecast data for the next several days (temperature, feels-like, humidity, cloud cover, chance of rain), not a pre-digested label — use your own judgment on what it implies. Weave specific numbers into natural prose where they help (e.g. "low 30s and dry" or "a real chance of showers in the afternoon") — never invent a number that isn't in the data above, never claim certainty about the weather, and don't mention weather for a day marked "forecast unavailable".

Use it two different ways:

- **For family items, weather is a selector**, as it has always been: hot and dry favors water/shade and going early or late in the day; a real chance of rain favors indoor picks (museums, the Natural History Museum) and a soft caveat on outdoor events that day; cold favors indoor or bundle-up-friendly options; a mild, dry, low-cloud day is a good excuse to lean outdoor (parks, the Rowing Channel, the Ancient Theatre).
- **For adult items, weather is trend texture only — never a gate.** Mention it if it genuinely helps ("it'll be warm enough to sit outside afterwards"), but never downplay, caveat away, or omit an adult item because of the forecast. Something worth going to is worth going to in a downpour, and a forecast this far out is closer to a coin-flip than a fact. The same goes for anything civic: a road closure happens whatever the weather.

Beyond the first day or two, treat the forecast as a *trend* — "warm all week, cooling toward the weekend" — rather than a per-day promise. Don't write a day-by-day weather report.

---

### HOUSEHOLD FEEDBACK (hand-edited preferences — bias tone and selection toward this)
{feedback}

---

### ADULT TASTE (the owner's own calibration — for TONE and framing, not for re-scoring)
{taste}

Use this to pitch adult items in language that lands: lead with what he'd actually care about. Do not re-score anything, do not quote this file, and do not mention that a taste profile exists.

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

Some candidates also carry a `practical` field (opening hours, entry fees, seasonality, reservation or safety notes). When present, weave the useful bits into your prose naturally — a quick "open Wed–Sun, kids under 7 free" or "book the pony ride ahead" saves the reader a click. Never dump it verbatim; fold it in as a friendly aside, and pair it with the weather where it helps (e.g. a shaded zoo on a hot day).

### STRUCTURE
Open with a short 1-2 sentence weather-at-a-glance line grounding the reader in what the next few days actually look like, using the specific numbers from THE WEEK'S WEATHER above (e.g. "Hot and dry through midweek, low 30s with barely a cloud, then it eases off with a real chance of showers by Saturday afternoon."). This comes before any recommendations, so the reader has grounding before reading the suggestions below — no need to open another weather app.

Then organize the rest into four loose sections, **ordered by time, not by audience** (use these or similar natural headers):

1. **This weekend** — anything happening this coming Saturday or Sunday, family and adult together. Add a short weather note where it's relevant to a family item. Include links for each item.
2. **During the week** — anything on a weekday in the coming week. In practice this is where most adult culture lands, since that's when it happens; a weekday family item belongs here too.
3. **Looking ahead** — notable things further out, 2-4 weeks, worth putting in the diary now.
4. **Good to know** — the civic half, and the standing ideas. Two kinds of thing live here: local facts (a new store or flight route, a road closure, an outage, a big match that will snarl the city) and the rotating always-available ideas (zoo, museum, rowing channel, a gallery, a viewpoint). Keep the civic items **short and factual** — a line each, no salesmanship, because their value is purely that he knows. Keep the standing ideas short and warm (e.g. "It's going to rain Sunday, so maybe the museum?").

Include **every** candidate provided — they've already been fact-checked and filtered upstream, so don't drop any for length. If a section has nothing in it, skip it gracefully rather than leaving an awkward empty header. There should almost always be something in "Good to know", since the standing ideas are the guaranteed fallback.

A note on the civic items: a road closure or an outage is the single most valuable thing in this email even though it is the least fun. Never bury it, never dress it up, and never skip it because it doesn't fit the warm tone. State it plainly and move on.

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

STAGE1_FAMILY_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title":       {"type": "string"},
                    "category":    {"type": "string", "enum": ["event_this_weekend", "event_thisweek", "event_lookahead", "evergreen"]},
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

# The adult path needs its own schema OBJECT, not just its own enum: test_concierge.py's stub
# dispatches on response_schema identity, so a shared schema would silently feed family
# candidates to both paths and the tests would still pass.
#
# adult_fit, civic_value and language_barrier are deliberately NOT in `required`. Gemini's
# response_schema cannot express "required only when category is X", and each of the three
# applies to only some categories. The pipeline reads whichever field matches the category and
# defaults a missing score to 0 — which fails every floor, so an unscored item is not sent.
STAGE1_ADULT_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title":            {"type": "string"},
                    "category":         {"type": "string", "enum": ["event_this_weekend", "event_thisweek", "event_lookahead", "evergreen", "civic_opportunity", "civic_notice"]},
                    "when_text":        {"type": "string"},
                    "date_iso":         {"type": "string"},
                    "location":         {"type": "string"},
                    "adult_fit":        {"type": "integer"},
                    "civic_value":      {"type": "integer"},
                    "language_barrier": {"type": "string", "enum": ["none", "partial", "blocking"]},
                    "reason":           {"type": "string"},
                    "source_url":       {"type": "string"},
                    "confidence":       {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["title", "category", "when_text", "location", "reason", "source_url", "confidence"],
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
