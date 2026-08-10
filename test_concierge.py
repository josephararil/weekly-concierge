"""
Offline verification for weekend_concierge.py.

Runs the real pipeline in a throwaway temp directory (non-destructive) with:
  - common.llm stubbed to return canned FIND (Stage 1, family + adult) + SKEPTIC (Stage 2)
    JSON and a canned CONCIERGE (Stage 3) HTML/text payload, dispatched by response_schema
    identity -- STAGE1_FAMILY_SCHEMA and STAGE1_ADULT_SCHEMA are distinct objects, so a
    shared schema would silently cross-feed the two FIND paths.
  - scrapers.harvest / weather.week_weather stubbed to avoid any network access.
  - SMTP env left unset, so common.send_email raises and weekend_concierge.main() catches
    and prints it (email "sends" every run regardless).

Runs main() twice on the same day to verify:
  - state/weekend_signals.json, state/weekend_log.md, state/memory.json/.md,
    state/signals_seen.json are all written.
  - Run 1: family + adult events, both civic items and both evergreens are selected and
    marked seen.
  - Run 2: events and the civic_notice are suppressed (21d TTL); the civic_opportunity is
    still suppressed too (180d cooldown); both evergreen slots rotate to a different
    off-cooldown catalog entry per audience, since the ones sent in run 1 are now within
    their own cooldown window.

Also covers, without running the full pipeline where a smaller unit suffices:
  - LANG-GATE: a language_barrier: "blocking" candidate is never sent regardless of score.
  - PREFIX: prune_seen()'s "civic|" cutoff (180d) vs. the default event cutoff (21d) --
    an event merely titled "Civic ..." must NOT get the civic cooldown.
  - every category enum value in both Stage-1 schemas maps through role_for() or is
    evergreen/civic_opportunity (the categories keyed another way).
  - format_weather() renders a fully-populated day with no '?' placeholder leaking through.
  - select_evergreens() never lets an adult catalog entry fill the family fallback slot,
    or vice versa.
  - select_events() ranks by score_of(candidate, audience), not by a hardcoded family_fit.

Run: python test_concierge.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import config as C
import common as X
import memory as M
import scrapers
import weather
import weekend_concierge as WC

EVENT_TITLE = "Test Circus in Plovdiv"
EVENT_DATE = "2099-01-03"  # far future -- stable across test runs regardless of "today"
EVERGREEN_TITLE = "Rowing Channel bike ride"

ADULT_HIGH_TITLE = "Contemporary Art Opening"
ADULT_HIGH_DATE = "2099-02-10"
ADULT_LOW_TITLE = "Jazz Bar Night"
ADULT_LOW_DATE = "2099-02-11"
BLOCKING_TITLE = "Bulgarian-Only Theatre Play"
BLOCKING_DATE = "2099-01-05"
CIVIC_OPPORTUNITY_TITLE = "New Shopping Mall Opens"
CIVIC_NOTICE_TITLE = "Water Supply Interruption Notice"
CIVIC_NOTICE_DATE = "2099-01-10"
ADULT_EVERGREEN_TITLE = "Kapana Creative District"  # matches a SEED_EVERGREEN_ADULT entry,
# so seeding it does not add a 53rd catalog entry beyond the 42+10 seeded count.

_STAGE1_FAMILY = {"candidates": [
    {"title": EVENT_TITLE, "category": "event_this_weekend", "when_text": "Saturday, 11:00",
     "date_iso": EVENT_DATE, "location": "Ancient Theatre, Plovdiv",
     "family_fit": 80, "reason": "Lots for a 4-year-old to enjoy.",
     "source_url": "https://example.bg/circus", "confidence": "high"},
    {"title": EVERGREEN_TITLE, "category": "evergreen", "when_text": "",
     "date_iso": None, "location": "Kanala, Plovdiv", "family_fit": 70,
     "reason": "Easy free outdoor outing.", "source_url": "", "confidence": "high"},
]}

_STAGE1_ADULT = {"candidates": [
    {"title": ADULT_HIGH_TITLE, "category": "event_thisweek", "when_text": "Thursday evening",
     "date_iso": ADULT_HIGH_DATE, "location": "Plovdiv City Art Gallery",
     "adult_fit": 90, "language_barrier": "none", "reason": "New Bulgarian painters, no text to read.",
     "source_url": "https://example.bg/art-opening", "confidence": "high"},
    {"title": ADULT_LOW_TITLE, "category": "event_thisweek", "when_text": "Friday night",
     "date_iso": ADULT_LOW_DATE, "location": "Kapana, Plovdiv",
     "adult_fit": 72, "language_barrier": "none", "reason": "Live jazz set, low key.",
     "source_url": "https://example.bg/jazz-bar", "confidence": "medium"},
    {"title": BLOCKING_TITLE, "category": "event_this_weekend", "when_text": "Saturday, 19:00",
     "date_iso": BLOCKING_DATE, "location": "Drama Theatre, Plovdiv",
     "adult_fit": 95, "language_barrier": "blocking", "reason": "Acclaimed play, Bulgarian only, no subtitles.",
     "source_url": "https://example.bg/play", "confidence": "high"},
    {"title": CIVIC_OPPORTUNITY_TITLE, "category": "civic_opportunity", "when_text": "",
     "date_iso": None, "location": "Trakia District, Plovdiv",
     "civic_value": 90, "reason": "A large new mall just opened nearby.",
     "source_url": "https://example.bg/mall", "confidence": "high"},
    {"title": CIVIC_NOTICE_TITLE, "category": "civic_notice", "when_text": "",
     "date_iso": CIVIC_NOTICE_DATE, "location": "citywide, Plovdiv",
     "civic_value": 60, "reason": "Planned maintenance affecting several districts.",
     "source_url": "https://example.bg/water-notice", "confidence": "high"},
    {"title": ADULT_EVERGREEN_TITLE, "category": "evergreen", "when_text": "",
     "date_iso": None, "location": "Kapana, Plovdiv", "adult_fit": 80, "language_barrier": "none",
     "reason": "The city's small-bar, gallery and street-art quarter.",
     "source_url": "", "confidence": "high"},
]}

# candidate_id is assigned after the merge -- family candidates first (in _STAGE1_FAMILY
# order), then adult ones (in _STAGE1_ADULT order): 1=circus, 2=rowing evergreen,
# 3=adult high, 4=adult low, 5=blocking, 6=civic opportunity, 7=civic notice,
# 8=adult evergreen.
_STAGE2 = [
    {"candidate_id": i, "verdict": "keep", "corrected_date_iso": None,
     "corrected_location": None, "note": "verified via search"}
    for i in range(1, 9)
]

_STAGE3 = {"subject": "Your weekend, sorted",
           "html": "<p>Test email body.</p>",
           "text": "Test email body."}


def _stub_day(label, date_iso):
    return {"label": label, "date": date_iso, "condition": "partly cloudy",
            "max_temp_c": 24, "min_temp_c": 14, "feels_like_max_c": 25, "feels_like_min_c": 13,
            "humidity_pct": 55, "cloud_cover_pct": 40, "rain_chance_pct": 10}


_WEEK_DAYS = [
    _stub_day(label, f"2099-02-{i:02d}")
    for i, label in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), start=3)
]


def _stub_llm(messages, model, max_tokens=2000, want_search=False, response_schema=None,
              provider=None, search_prompt=None):
    if response_schema is C.STAGE1_FAMILY_SCHEMA:
        return json.dumps(_STAGE1_FAMILY)
    if response_schema is C.STAGE1_ADULT_SCHEMA:
        return json.dumps(_STAGE1_ADULT)
    if response_schema is C.STAGE2_RESPONSE_SCHEMA:
        return json.dumps(_STAGE2)
    if response_schema is C.CONCIERGE_RESPONSE_SCHEMA:
        return json.dumps(_STAGE3)
    raise AssertionError(f"unexpected llm() call with response_schema={response_schema!r}")


def _sent_titles(log_text):
    """Titles listed under '## Sent this run' in weekend_log.md."""
    section = log_text.split("## Sent this run", 1)[1].split("## All candidates", 1)[0]
    return [line for line in section.splitlines() if line.startswith("- **")]


def _pool_lines(log_text, heading):
    """Lines listed under one '### <heading>' subheading inside '## Sent this run'."""
    marker = f"### {heading}"
    if marker not in log_text:
        return []
    section = log_text.split(marker, 1)[1]
    section = section.split("## All candidates", 1)[0]
    section = section.split("\n### ", 1)[0]
    return [line for line in section.splitlines() if line.startswith("- **")]


class WeekendConciergeTest(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self.sandbox = tempfile.mkdtemp(prefix="wc_test_")
        os.makedirs(os.path.join(self.sandbox, "state"))
        os.chdir(self.sandbox)
        with open("state/memory.json", "w", encoding="utf-8") as f:
            json.dump({"evergreen": {}, "ledger": []}, f)
        with open("state/signals_seen.json", "w", encoding="utf-8") as f:
            json.dump({"seen": {}, "monthly_count": {}}, f)

        self._real_llm = X.llm
        self._real_harvest = scrapers.harvest
        self._real_week_weather = weather.week_weather
        X.llm = _stub_llm
        scrapers.harvest = lambda today=None: []
        weather.week_weather = lambda latlon, today, days=7: list(_WEEK_DAYS)
        os.environ.pop("SMTP_HOST", None)

    def tearDown(self):
        X.llm = self._real_llm
        scrapers.harvest = self._real_harvest
        weather.week_weather = self._real_week_weather
        os.chdir(self._cwd)
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def test_two_runs_write_state_and_rotate(self):
        WC.main()

        for name in ("weekend_signals.json", "weekend_log.md", "memory.json", "memory.md",
                     "signals_seen.json"):
            self.assertTrue(os.path.exists(f"state/{name}"), f"missing state/{name}")

        seen1 = json.load(open("state/signals_seen.json", encoding="utf-8"))
        event_key = WC.event_key(EVENT_TITLE, EVENT_DATE, "thisweekend")
        evergreen_key = WC.evergreen_key(EVERGREEN_TITLE)
        adult_evergreen_key = WC.evergreen_key(ADULT_EVERGREEN_TITLE, "adult")
        civic_opp_key = WC.civic_key(CIVIC_OPPORTUNITY_TITLE)
        self.assertIn(event_key, seen1["seen"])
        self.assertIn(evergreen_key, seen1["seen"])
        self.assertIn(adult_evergreen_key, seen1["seen"])
        self.assertIn(civic_opp_key, seen1["seen"])

        mem1 = json.load(open("state/memory.json", encoding="utf-8"))
        self.assertIn(EVERGREEN_TITLE, mem1["evergreen"])
        self.assertEqual(mem1["evergreen"][EVERGREEN_TITLE]["last_suggested"], X.today_iso())
        # Seed catalog only -- ADULT_EVERGREEN_TITLE reuses an existing seed name, so this
        # run adds nothing new to the 42 family + 10 adult seeded entries.
        self.assertEqual(len(mem1["evergreen"]), len(C.SEED_EVERGREEN) + len(C.SEED_EVERGREEN_ADULT))

        log1 = open("state/weekend_log.md", encoding="utf-8").read()
        sent1 = _sent_titles(log1)
        self.assertTrue(any(EVENT_TITLE in line for line in sent1))
        self.assertTrue(any(EVERGREEN_TITLE in line for line in sent1))
        self.assertTrue(any(ADULT_HIGH_TITLE in line for line in sent1))
        self.assertTrue(any(ADULT_LOW_TITLE in line for line in sent1))
        self.assertTrue(any(CIVIC_OPPORTUNITY_TITLE in line for line in sent1))
        self.assertTrue(any(CIVIC_NOTICE_TITLE in line for line in sent1))
        self.assertTrue(any(ADULT_EVERGREEN_TITLE in line for line in sent1))

        signals1 = json.load(open("state/weekend_signals.json", encoding="utf-8"))
        by_title1 = {s["title"]: s for s in signals1["signals"]}
        self.assertEqual(by_title1[EVENT_TITLE]["verdict"], "sent")
        self.assertEqual(by_title1[EVERGREEN_TITLE]["verdict"], "sent")
        self.assertEqual(by_title1[CIVIC_OPPORTUNITY_TITLE]["verdict"], "sent")

        # --- row (a) LANG-GATE: adult_fit 95 must not save a blocking language barrier ---
        self.assertNotEqual(by_title1[BLOCKING_TITLE]["verdict"], "sent",
                            "language_barrier: blocking must drop the candidate regardless of score")
        self.assertFalse(any(BLOCKING_TITLE in line for line in sent1),
                         "blocking-language item must never appear in the sent email")

        # --- row (g) SORT-KEY, end-to-end consequence: higher adult_fit sorts first ---
        adult_pool1 = _pool_lines(log1, "Adult events")
        self.assertTrue(adult_pool1, "Adult events pool should be non-empty in run 1")
        high_idx = next(i for i, l in enumerate(adult_pool1) if ADULT_HIGH_TITLE in l)
        low_idx = next(i for i, l in enumerate(adult_pool1) if ADULT_LOW_TITLE in l)
        self.assertLess(high_idx, low_idx,
                        "adult_fit 90 item must be listed before the adult_fit 72 item")

        # --- second run, same canned inputs ---
        WC.main()

        signals2 = json.load(open("state/weekend_signals.json", encoding="utf-8"))
        by_title2 = {s["title"]: s for s in signals2["signals"]}
        self.assertEqual(by_title2[EVENT_TITLE]["verdict"], "suppressed",
                         "event should be suppressed on the second run (still in TTL)")
        self.assertEqual(by_title2[CIVIC_NOTICE_TITLE]["verdict"], "suppressed",
                         "civic_notice should be suppressed on the second run (21d TTL)")
        # --- row (e): civic_opportunity's 180-day cooldown, not the 21-day event TTL ---
        self.assertEqual(by_title2[CIVIC_OPPORTUNITY_TITLE]["verdict"], "suppressed",
                         "civic_opportunity must still be suppressed in run 2 (180d cooldown)")

        log2 = open("state/weekend_log.md", encoding="utf-8").read()
        sent2 = _sent_titles(log2)
        self.assertFalse(any(EVENT_TITLE in line for line in sent2),
                         "suppressed event must not appear in 'Sent this run' again")
        self.assertFalse(any(CIVIC_OPPORTUNITY_TITLE in line for line in sent2),
                         "civic_opportunity still in cooldown must not be re-sent")
        self.assertTrue(sent2, "evergreen fallback should still guarantee non-empty content")

        # --- row (f), end-to-end consequence: each audience's evergreen fallback rotates
        # within its own audience, never borrowing the other's catalog entry ---
        family_ever2 = _pool_lines(log2, "Family evergreens")
        adult_ever2 = _pool_lines(log2, "Adult evergreens")
        self.assertTrue(family_ever2, "family evergreen fallback should still guarantee non-empty content")
        self.assertFalse(any(EVERGREEN_TITLE in l for l in family_ever2),
                         "evergreen still in cooldown must not be re-sent")
        self.assertTrue(adult_ever2, "adult evergreen fallback should still guarantee non-empty content")
        self.assertFalse(any(ADULT_EVERGREEN_TITLE in l for l in adult_ever2),
                         "adult evergreen still in cooldown must not be re-sent")

    def test_build_links(self):
        # real source_url is passed through untouched; maps + search always constructed
        src, maps, search = WC.build_links(
            {"title": "Kapana Fest", "location": "Kapana, Plovdiv", "source_url": "https://kapana.bg/fest"})
        self.assertEqual(src, "https://kapana.bg/fest")
        self.assertIn("google.com/maps", maps)
        self.assertIn("Kapana", maps)
        self.assertTrue(search.startswith("https://www.google.com/search?q="))

        # no source_url -> "", but maps/search still built from location/title
        src, maps, search = WC.build_links(
            {"title": "Stara Zagora Zoo", "location": "Stara Zagora", "source_url": ""})
        self.assertEqual(src, "")
        self.assertIn("Stara+Zagora", maps)

        # title only, missing keys -> no crash; maps falls back to the title
        src, maps, search = WC.build_links({"title": "Some Parade"})
        self.assertEqual(src, "")
        self.assertIn("Some+Parade", maps)
        self.assertIn("Some+Parade", search)

    def test_prefix_civic_cutoff_not_applied_to_lookalike_event(self):
        """PREFIX: prune_seen()'s civic-only 180d cutoff must key off 'civic|' WITH the
        pipe. An event merely titled 'Civic Center Opening' slugs to
        'civic-center-opening|...' and must get the ordinary 21d event TTL, not 180d."""
        import datetime as dt
        old_date = (dt.date.today() - dt.timedelta(days=30)).isoformat()

        lookalike_key = WC.event_key("Civic Center Opening", "2099-01-03", "thisweek")
        real_civic_key = WC.civic_key("Something durable and civic")
        state = {"seen": {lookalike_key: old_date, real_civic_key: old_date}, "monthly_count": {}}

        pruned = WC.prune_seen(state)
        self.assertNotIn(lookalike_key, pruned["seen"],
                         "an event titled 'Civic ...' must use the 21d event TTL, not the 180d civic cooldown "
                         "(30 days old should already be pruned)")
        self.assertIn(real_civic_key, pruned["seen"],
                     "a real civic| key must survive 30 days old under the 180d cooldown "
                     "(this half proves the test would fail if the pipe check were dropped)")

    def test_every_schema_category_maps_through_role_for_or_is_a_known_exception(self):
        """The silent-lockstep row: every category enum value in both Stage-1 schemas must
        either resolve through role_for() or be one of the two categories keyed another way."""
        family_enum = C.STAGE1_FAMILY_SCHEMA["properties"]["candidates"]["items"]["properties"]["category"]["enum"]
        adult_enum = C.STAGE1_ADULT_SCHEMA["properties"]["candidates"]["items"]["properties"]["category"]["enum"]
        for value in set(family_enum) | set(adult_enum):
            self.assertTrue(
                WC.role_for(value) is not None or value in ("evergreen", "civic_opportunity"),
                f"category {value!r} does not map to a role and is not a known exception",
            )
        with self.assertRaises(KeyError):
            WC.role_for("something_new")

    def test_format_weather_fully_populated_day_has_no_placeholder(self):
        """The other silent row: a renamed key in week_weather()'s day dicts would print '?'
        into a live prompt with no test failure otherwise."""
        rendered = WC.format_weather([_WEEK_DAYS[0]])
        self.assertNotIn("?", rendered)
        self.assertEqual(WC.format_weather([]), "forecast unavailable")

    def test_select_evergreens_never_crosses_audiences(self):
        """row (f), unit level: an adult catalog entry must never fill the family fallback
        slot, and vice versa."""
        old_date = "2000-01-01"
        mem = {"evergreen": {
            "Family Fallback Spot": {"audience": "family", "last_suggested": old_date, "location": ""},
            "Adult Fallback Spot": {"audience": "adult", "last_suggested": old_date, "location": ""},
        }}
        family_pick = WC.select_evergreens([], mem, "family")
        self.assertEqual(len(family_pick), 1)
        self.assertEqual(family_pick[0]["title"], "Family Fallback Spot")

        adult_pick = WC.select_evergreens([], mem, "adult")
        self.assertEqual(len(adult_pick), 1)
        self.assertEqual(adult_pick[0]["title"], "Adult Fallback Spot")

    def test_select_events_sorts_by_score_of_not_family_fit(self):
        """row (g), unit level: an adult_fit 90 item must sort above an adult_fit 72 item.
        Sorting by c.get('family_fit', 0) instead would score both 0 and lose the order."""
        higher = {"title": "High", "category": "event_thisweek", "adult_fit": 90}
        lower = {"title": "Low", "category": "event_thisweek", "adult_fit": 72}
        ranked = WC.select_events([lower, higher], "adult")
        self.assertEqual([c["title"] for c in ranked], ["High", "Low"])


if __name__ == "__main__":
    unittest.main()
