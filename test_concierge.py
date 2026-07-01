"""
Offline verification for weekend_concierge.py.

Runs the real pipeline in a throwaway temp directory (non-destructive) with:
  - common.llm stubbed to return canned FIND (Stage 1) + SKEPTIC (Stage 2) JSON and a
    canned CONCIERGE (Stage 3) HTML/text payload, dispatched by response_schema identity.
  - scrapers.harvest / weather.weekend_weather stubbed to avoid any network access.
  - SMTP env left unset, so common.send_email raises and weekend_concierge.main() catches
    and prints it (email "sends" every run regardless).

Runs main() twice on the same day to verify:
  - state/weekend_signals.json, state/weekend_log.md, state/memory.json/.md,
    state/signals_seen.json are all written.
  - Run 1: the event and the evergreen candidate are both selected and marked seen.
  - Run 2: the same event is suppressed (anti-repeat cooldown) and the evergreen rotates
    to a different off-cooldown catalog entry (least-recently-suggested fallback), since
    the one sent in run 1 is now within its cooldown window.

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

_STAGE1 = {"candidates": [
    {"title": EVENT_TITLE, "category": "event_this_weekend", "when_text": "Saturday, 11:00",
     "date_iso": EVENT_DATE, "location": "Ancient Theatre, Plovdiv",
     "family_fit": 80, "reason": "Lots for a 4-year-old to enjoy.",
     "source_url": "https://example.bg/circus", "confidence": "high"},
    {"title": "Rowing Channel bike ride", "category": "evergreen", "when_text": "",
     "date_iso": None, "location": "Kanala, Plovdiv", "family_fit": 70,
     "reason": "Easy free outdoor outing.", "source_url": "", "confidence": "high"},
]}

_STAGE2 = [
    {"candidate_id": 1, "verdict": "keep", "corrected_date_iso": None,
     "corrected_location": None, "note": "verified via search"},
    {"candidate_id": 2, "verdict": "keep", "corrected_date_iso": None,
     "corrected_location": None, "note": "known catalog entry"},
]

_STAGE3 = {"subject": "Your weekend, sorted",
           "html": "<p>Test email body.</p>",
           "text": "Test email body."}


def _stub_llm(messages, model, max_tokens=2000, want_search=False, response_schema=None,
              provider=None, search_prompt=None):
    if response_schema is C.STAGE1_RESPONSE_SCHEMA:
        return json.dumps(_STAGE1)
    if response_schema is C.STAGE2_RESPONSE_SCHEMA:
        return json.dumps(_STAGE2)
    if response_schema is C.CONCIERGE_RESPONSE_SCHEMA:
        return json.dumps(_STAGE3)
    raise AssertionError(f"unexpected llm() call with response_schema={response_schema!r}")


def _sent_titles(log_text):
    """Titles listed under '## Sent this run' in weekend_log.md."""
    section = log_text.split("## Sent this run", 1)[1].split("## All candidates", 1)[0]
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
        self._real_weather = weather.weekend_weather
        X.llm = _stub_llm
        scrapers.harvest = lambda today=None: []
        weather.weekend_weather = lambda latlon, today: {"Sat": "MILD", "Sun": "MILD"}
        os.environ.pop("SMTP_HOST", None)

    def tearDown(self):
        X.llm = self._real_llm
        scrapers.harvest = self._real_harvest
        weather.weekend_weather = self._real_weather
        os.chdir(self._cwd)
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def test_two_runs_write_state_and_rotate(self):
        WC.main()

        for name in ("weekend_signals.json", "weekend_log.md", "memory.json", "memory.md",
                     "signals_seen.json"):
            self.assertTrue(os.path.exists(f"state/{name}"), f"missing state/{name}")

        seen1 = json.load(open("state/signals_seen.json", encoding="utf-8"))
        event_key = WC.event_key(EVENT_TITLE, EVENT_DATE, "thisweekend")
        evergreen_key = WC.evergreen_key("Rowing Channel bike ride")
        self.assertIn(event_key, seen1["seen"])
        self.assertIn(evergreen_key, seen1["seen"])

        mem1 = json.load(open("state/memory.json", encoding="utf-8"))
        self.assertIn("Rowing Channel bike ride", mem1["evergreen"])
        self.assertEqual(mem1["evergreen"]["Rowing Channel bike ride"]["last_suggested"], X.today_iso())
        self.assertEqual(len(mem1["evergreen"]), len(C.SEED_EVERGREEN))  # seeded on first run

        log1 = open("state/weekend_log.md", encoding="utf-8").read()
        sent1 = _sent_titles(log1)
        self.assertTrue(any(EVENT_TITLE in line for line in sent1))
        self.assertTrue(any("Rowing Channel bike ride" in line for line in sent1))

        signals1 = json.load(open("state/weekend_signals.json", encoding="utf-8"))
        by_title1 = {s["title"]: s for s in signals1["signals"]}
        self.assertEqual(by_title1[EVENT_TITLE]["verdict"], "sent")
        self.assertEqual(by_title1["Rowing Channel bike ride"]["verdict"], "sent")

        # --- second run, same canned inputs ---
        WC.main()

        signals2 = json.load(open("state/weekend_signals.json", encoding="utf-8"))
        by_title2 = {s["title"]: s for s in signals2["signals"]}
        self.assertEqual(by_title2[EVENT_TITLE]["verdict"], "suppressed",
                         "event should be suppressed on the second run (still in TTL)")

        log2 = open("state/weekend_log.md", encoding="utf-8").read()
        sent2 = _sent_titles(log2)
        self.assertFalse(any(EVENT_TITLE in line for line in sent2),
                         "suppressed event must not appear in 'Sent this run' again")
        self.assertFalse(any("Rowing Channel bike ride" in line for line in sent2),
                         "evergreen still in cooldown must not be re-sent")
        self.assertTrue(sent2, "evergreen fallback should still guarantee non-empty content")

        mem2 = json.load(open("state/memory.json", encoding="utf-8"))
        rotated_name = next(iter(_sent_titles(log2)))
        self.assertNotIn("Rowing Channel bike ride", rotated_name)


if __name__ == "__main__":
    unittest.main()
