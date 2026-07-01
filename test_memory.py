"""Offline tests for memory.py — no network access. Run with: python -m unittest test_memory"""

import datetime as dt
import json
import os
import shutil
import tempfile
import unittest

import memory as M


class MemoryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_state_dir = M.STATE_DIR
        M.STATE_DIR = self._tmpdir

    def tearDown(self):
        M.STATE_DIR = self._orig_state_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestLoadSave(MemoryTestCase):
    def test_load_missing_file_returns_fresh_shape(self):
        mem = M.load()
        self.assertEqual(mem, {"evergreen": {}, "ledger": []})

    def test_load_corrupt_file_returns_fresh_shape(self):
        with open(os.path.join(self._tmpdir, "memory.json"), "w") as f:
            f.write("{not json")
        mem = M.load()
        self.assertEqual(mem, {"evergreen": {}, "ledger": []})

    def test_save_then_load_round_trips(self):
        mem = M.load()
        M.record_evergreen(mem, "Stara Zagora Zoo", location="Stara Zagora", area="Stara Zagora")
        M.save(mem)
        reloaded = M.load()
        self.assertIn("Stara Zagora Zoo", reloaded["evergreen"])

    def test_save_writes_memory_md(self):
        mem = M.load()
        M.save(mem)
        self.assertTrue(os.path.exists(os.path.join(self._tmpdir, "memory.md")))


class TestRecordEvergreen(MemoryTestCase):
    def test_new_entry_has_expected_fields(self):
        mem = M.load()
        M.record_evergreen(mem, "Rowing Channel", location="Plovdiv", area="Plovdiv",
                            description="Bike/walk along the water", tags=["outdoor", "free"],
                            source="local knowledge")
        entry = mem["evergreen"]["Rowing Channel"]
        self.assertEqual(entry["location"], "Plovdiv")
        self.assertEqual(entry["area"], "Plovdiv")
        self.assertEqual(entry["description"], "Bike/walk along the water")
        self.assertEqual(entry["tags"], ["outdoor", "free"])
        self.assertEqual(entry["source"], "local knowledge")
        self.assertIsNone(entry["last_suggested"])
        self.assertEqual(entry["discovered"], dt.date.today().isoformat())

    def test_update_preserves_unset_fields(self):
        mem = M.load()
        M.record_evergreen(mem, "Bachkovo Monastery", location="Bachkovo", description="Old monastery")
        M.record_evergreen(mem, "Bachkovo Monastery", tags=["day-trip"])
        entry = mem["evergreen"]["Bachkovo Monastery"]
        self.assertEqual(entry["location"], "Bachkovo")
        self.assertEqual(entry["description"], "Old monastery")
        self.assertEqual(entry["tags"], ["day-trip"])

    def test_discovered_date_does_not_change_on_update(self):
        mem = M.load()
        M.record_evergreen(mem, "Museum")
        first_discovered = mem["evergreen"]["Museum"]["discovered"]
        M.record_evergreen(mem, "Museum", description="updated")
        self.assertEqual(mem["evergreen"]["Museum"]["discovered"], first_discovered)

    def test_suggested_bumps_last_suggested_to_today(self):
        mem = M.load()
        M.record_evergreen(mem, "Zoo", suggested=True)
        self.assertEqual(mem["evergreen"]["Zoo"]["last_suggested"], dt.date.today().isoformat())

    def test_not_suggested_preserves_previous_last_suggested(self):
        mem = M.load()
        M.record_evergreen(mem, "Zoo", suggested=True)
        stamped = mem["evergreen"]["Zoo"]["last_suggested"]
        M.record_evergreen(mem, "Zoo", description="update, not a new suggestion")
        self.assertEqual(mem["evergreen"]["Zoo"]["last_suggested"], stamped)


class TestRecordSuggestion(MemoryTestCase):
    def test_appends_ledger_entry_with_expected_fields(self):
        mem = M.load()
        M.record_suggestion(mem, "Summer Concert", "event_this_weekend", "Sat 19:00",
                             location="Plovdiv", url="https://example.com", score=80,
                             verdict="sent", note="family friendly")
        self.assertEqual(len(mem["ledger"]), 1)
        entry = mem["ledger"][0]
        self.assertEqual(entry["title"], "Summer Concert")
        self.assertEqual(entry["category"], "event_this_weekend")
        self.assertEqual(entry["when"], "Sat 19:00")
        self.assertEqual(entry["location"], "Plovdiv")
        self.assertEqual(entry["url"], "https://example.com")
        self.assertEqual(entry["score"], 80)
        self.assertEqual(entry["verdict"], "sent")
        self.assertEqual(entry["note"], "family friendly")
        self.assertEqual(entry["date"], dt.date.today().isoformat())


class TestPrune(MemoryTestCase):
    def test_drops_entries_older_than_max_ledger_days(self):
        mem = M.load()
        old_date = (dt.date.today() - dt.timedelta(days=M.MAX_LEDGER_DAYS + 1)).isoformat()
        mem["ledger"].append({"date": old_date, "title": "Old Event"})
        M.record_suggestion(mem, "Fresh Event", "evergreen", "anytime")
        M.prune(mem)
        titles = [e.get("title") for e in mem["ledger"]]
        self.assertNotIn("Old Event", titles)
        self.assertIn("Fresh Event", titles)

    def test_caps_ledger_at_max_entries(self):
        mem = M.load()
        for i in range(M.MAX_LEDGER_ENTRIES + 10):
            M.record_suggestion(mem, f"Event {i}", "evergreen", "anytime")
        M.prune(mem)
        self.assertEqual(len(mem["ledger"]), M.MAX_LEDGER_ENTRIES)
        self.assertEqual(mem["ledger"][-1]["title"], f"Event {M.MAX_LEDGER_ENTRIES + 9}")


class TestSummarizeForPrompt(MemoryTestCase):
    def test_empty_memory_returns_placeholder(self):
        mem = M.load()
        self.assertEqual(M.summarize_for_prompt(mem), "(no prior memory)")

    def test_includes_off_cooldown_evergreen(self):
        mem = M.load()
        M.record_evergreen(mem, "Zoo", area="Stara Zagora", description="Petting zoo")
        text = M.summarize_for_prompt(mem)
        self.assertIn("Zoo", text)
        self.assertIn("Stara Zagora", text)

    def test_excludes_on_cooldown_evergreen(self):
        mem = M.load()
        M.record_evergreen(mem, "Zoo", suggested=True)
        text = M.summarize_for_prompt(mem)
        self.assertNotIn("Zoo", text)

    def test_includes_evergreen_suggested_before_cooldown_window(self):
        mem = M.load()
        M.record_evergreen(mem, "Zoo")
        stale = (dt.date.today() - dt.timedelta(days=M.EVERGREEN_COOLDOWN_DAYS + 1)).isoformat()
        mem["evergreen"]["Zoo"]["last_suggested"] = stale
        text = M.summarize_for_prompt(mem)
        self.assertIn("Zoo", text)

    def test_includes_recent_suggestions(self):
        mem = M.load()
        M.record_suggestion(mem, "Circus", "event_this_weekend", "Sun", verdict="sent")
        text = M.summarize_for_prompt(mem)
        self.assertIn("Circus", text)
        self.assertIn("sent", text)


if __name__ == "__main__":
    unittest.main()
