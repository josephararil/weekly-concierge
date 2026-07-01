"""Offline tests for scrapers.py — no network access. Run with: python -m unittest test_scrapers"""

import datetime as dt
import unittest
from unittest.mock import patch

import config as C
import scrapers


class TestBgDate(unittest.TestCase):
    def test_numeric_dot_format(self):
        self.assertEqual(scrapers.bg_date("28.11.2025"), "2025-11-28")

    def test_numeric_slash_format(self):
        self.assertEqual(scrapers.bg_date("5/6/2026"), "2026-06-05")

    def test_bulgarian_month_name_with_year(self):
        self.assertEqual(scrapers.bg_date("28 ноември 2025"), "2025-11-28")

    def test_bulgarian_month_name_ordinal_suffix(self):
        self.assertEqual(scrapers.bg_date("15-ти януари 2026"), "2026-01-15")

    def test_bulgarian_month_name_without_year_rolls_forward(self):
        today = dt.date(2026, 7, 1)
        # A date already past this year (e.g. January) should roll to next year.
        self.assertEqual(scrapers.bg_date("15 януари", today=today), "2027-01-15")
        # A date still ahead this year should stay in the current year.
        self.assertEqual(scrapers.bg_date("15 август", today=today), "2026-08-15")

    def test_invalid_calendar_date_returns_none(self):
        self.assertIsNone(scrapers.bg_date("31 февруари 2026"))

    def test_no_date_found_returns_none(self):
        self.assertIsNone(scrapers.bg_date("no date here"))

    def test_empty_input_returns_none(self):
        self.assertIsNone(scrapers.bg_date(""))
        self.assertIsNone(scrapers.bg_date(None))


class TestTextOf(unittest.TestCase):
    def test_strips_scripts_and_collapses_whitespace(self):
        html = "<html><body><script>evil()</script>  <p>Hello   world</p>  </body></html>"
        self.assertEqual(scrapers.text_of(html), "Hello world")

    def test_caps_length(self):
        html = "<p>" + ("x" * 100) + "</p>"
        self.assertEqual(len(scrapers.text_of(html, max_chars=10)), 10)


class TestRegistryWiring(unittest.TestCase):
    def test_enabled_sources_all_resolve(self):
        for source in C.ENABLED_SOURCES:
            self.assertTrue(
                source in scrapers.SCRAPERS or source in scrapers.RAW_FETCH_SOURCES,
                f"{source} is in config.ENABLED_SOURCES but not registered in scrapers.py",
            )

    def test_no_source_registered_in_both_tiers(self):
        overlap = set(scrapers.SCRAPERS) & set(scrapers.RAW_FETCH_SOURCES)
        self.assertEqual(overlap, set())

    def test_facebook_stub_raises_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            scrapers.scrape_facebook()


class TestHarvestNeverCrashes(unittest.TestCase):
    def test_harvest_survives_every_source_failing(self):
        with patch("scrapers.fetch", return_value=None), \
             patch.object(scrapers, "scrape_plovdiv2019", side_effect=RuntimeError("boom")), \
             patch.object(scrapers, "scrape_bilet", side_effect=RuntimeError("boom")):
            result = scrapers.harvest("2026-07-01")
        self.assertEqual(result, [])

    def test_harvest_dedupes_by_title_and_date(self):
        dupe_item = scrapers._make_item("src_a", "Same Event", date_iso="2026-07-04")
        with patch("scrapers.SCRAPERS", {"plovdiv2019": lambda: [dupe_item], "bilet": lambda: [dict(dupe_item)]}), \
             patch("scrapers.RAW_FETCH_SOURCES", {}), \
             patch.object(C, "ENABLED_SOURCES", ["plovdiv2019", "bilet"]):
            result = scrapers.harvest("2026-07-01")
        self.assertEqual(len(result), 1)

    def test_harvest_skips_unknown_source(self):
        with patch.object(C, "ENABLED_SOURCES", ["not_a_real_source"]):
            result = scrapers.harvest("2026-07-01")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
