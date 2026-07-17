"""Offline tests for scrapers.py — no network access. Run with: python -m unittest test_scrapers"""

import datetime as dt
import os
import unittest
from unittest.mock import patch

import config as C
import scrapers

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "tests", "fixtures")


def load_fixture(name):
    """Read a fixture HTML file from tests/fixtures/ for feeding to a _parse_<source>()."""
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


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

    def test_english_month_name_with_year(self):
        self.assertEqual(scrapers.bg_date("11 July 2026"), "2026-07-11")

    def test_bare_dd_mm_no_year_rolls_forward(self):
        today = dt.date(2026, 7, 1)
        self.assertEqual(scrapers.bg_date("15.01", today=today), "2027-01-15")
        self.assertEqual(scrapers.bg_date("15.08", today=today), "2026-08-15")

    def test_date_range_same_month_returns_start(self):
        self.assertEqual(scrapers.bg_date("10-14 юли 2026"), "2026-07-10")

    def test_date_range_different_months_returns_start(self):
        self.assertEqual(scrapers.bg_date("10 юли – 12 август 2026"), "2026-07-10")


class TestBgDateRange(unittest.TestCase):
    def test_same_month_range(self):
        self.assertEqual(scrapers.bg_date_range("10–14 юли 2026"), ("2026-07-10", "2026-07-14"))

    def test_different_month_range(self):
        self.assertEqual(
            scrapers.bg_date_range("10 юли – 12 август 2026"),
            ("2026-07-10", "2026-08-12"),
        )

    def test_no_year_rolls_forward(self):
        today = dt.date(2026, 7, 1)
        self.assertEqual(scrapers.bg_date_range("10-14 януари", today=today), ("2027-01-10", "2027-01-14"))

    def test_no_range_returns_none_tuple(self):
        self.assertEqual(scrapers.bg_date_range("28 ноември 2025"), (None, None))
        self.assertEqual(scrapers.bg_date_range(""), (None, None))


class TestResolveUrl(unittest.TestCase):
    def test_joins_relative_href(self):
        self.assertEqual(
            scrapers.resolve_url("https://example.com/en", "/en/events/foo"),
            "https://example.com/en/events/foo",
        )

    def test_passes_through_absolute_href(self):
        self.assertEqual(
            scrapers.resolve_url("https://example.com", "https://other.com/x"),
            "https://other.com/x",
        )

    def test_empty_href_returns_empty_string(self):
        self.assertEqual(scrapers.resolve_url("https://example.com", ""), "")


class TestFetchSoup(unittest.TestCase):
    def test_returns_none_on_fetch_failure(self):
        with patch("scrapers.fetch", return_value=None):
            self.assertIsNone(scrapers.fetch_soup("https://example.com"))

    def test_returns_soup_on_success(self):
        with patch("scrapers.fetch", return_value="<p>hi</p>"):
            soup = scrapers.fetch_soup("https://example.com")
        self.assertEqual(soup.find("p").get_text(), "hi")


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

    def test_harvest_falls_back_to_raw_fetch_when_structured_returns_empty(self):
        item = scrapers._make_item("dual", "Raw Fallback Event", date_iso="2026-07-04")
        with patch("scrapers.SCRAPERS", {"dual": lambda: []}), \
             patch("scrapers.RAW_FETCH_SOURCES", {"dual": "https://example.com"}), \
             patch("scrapers.raw_fetch", return_value=[item]), \
             patch.object(C, "ENABLED_SOURCES", ["dual"]):
            result = scrapers.harvest("2026-07-01")
        self.assertEqual(result, [item])

    def test_harvest_skips_raw_fetch_when_structured_succeeds(self):
        item = scrapers._make_item("dual", "Structured Event", date_iso="2026-07-04")
        with patch("scrapers.SCRAPERS", {"dual": lambda: [item]}), \
             patch("scrapers.RAW_FETCH_SOURCES", {"dual": "https://example.com"}), \
             patch("scrapers.raw_fetch") as mock_raw_fetch, \
             patch.object(C, "ENABLED_SOURCES", ["dual"]):
            result = scrapers.harvest("2026-07-01")
        mock_raw_fetch.assert_not_called()
        self.assertEqual(result, [item])

    def test_harvest_falls_back_when_structured_raises(self):
        item = scrapers._make_item("dual", "Raw Fallback After Error", date_iso="2026-07-04")

        def boom():
            raise RuntimeError("boom")

        with patch("scrapers.SCRAPERS", {"dual": boom}), \
             patch("scrapers.RAW_FETCH_SOURCES", {"dual": "https://example.com"}), \
             patch("scrapers.raw_fetch", return_value=[item]), \
             patch.object(C, "ENABLED_SOURCES", ["dual"]):
            result = scrapers.harvest("2026-07-01")
        self.assertEqual(result, [item])


class TestParsePlovdiv2019Fixture(unittest.TestCase):
    def test_parses_cards_from_fixture(self):
        html = load_fixture("plovdiv2019.html")
        items = scrapers._parse_plovdiv2019(html)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["title"], "Open-Air Puppet Theatre")
        self.assertEqual(first["date_iso"], "2026-07-11")
        self.assertEqual(first["location"], "Ancient Theatre")
        self.assertEqual(first["url"], "https://plovdiv2019.eu/en/events/open-air-puppet-theatre")


class TestParseBiletFixture(unittest.TestCase):
    def test_parses_cards_from_fixture(self):
        html = load_fixture("bilet.html")
        items = scrapers._parse_bilet(html)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["title"], "Summer Jazz Night")
        self.assertEqual(first["date_iso"], "2026-07-18")
        self.assertEqual(first["location"], "Plovdiv, Roman Stadium")
        self.assertEqual(first["url"], "https://bilet.bg/en/events/summer-jazz-night")


class TestParseTicketbgFixture(unittest.TestCase):
    def test_parses_cards_from_fixture(self):
        html = load_fixture("ticketbg.html")
        items = scrapers._parse_ticketbg(html, today=dt.date(2026, 7, 2))
        # Varna (Chamkoria) and Burgas events are dropped as out of the Plovdiv radius.
        self.assertEqual(len(items), 3)
        titles = [item["title"] for item in items]
        self.assertIn("Неделя сутрин", titles)
        self.assertNotIn("Чамкория", titles)

        first = items[0]
        self.assertEqual(first["title"], "Неделя сутрин")
        self.assertEqual(first["date_iso"], "2026-07-07")
        self.assertEqual(first["location"], "Летен Театър Пловдив, Пловдив")
        self.assertEqual(first["url"], "https://www.ticket.bg/bilet/nedelq-sutrin")

        sofia = next(item for item in items if item["title"] == "Концерт на Анелия")
        self.assertEqual(sofia["date_iso"], "2026-11-28")
        self.assertEqual(sofia["location"], "Арена 8888 София, София")


class TestParseProgramataFixture(unittest.TestCase):
    def test_parses_cards_from_fixture(self):
        html = load_fixture("programata.html")
        items = scrapers._parse_programata(html)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["title"], "Зелено училище в Дом на културата Искър")
        self.assertEqual(first["url"], "https://programata.bg/kids/zele/")

        second = items[1]
        self.assertEqual(
            second["title"],
            "Disney Лято на историите | Специално шоу на пеещите фонтани в Пловдив",
        )
        self.assertEqual(
            second["url"],
            "https://programata.bg/kids/disney-lyato-na-istoriite-speczialno-shou-na-peesthite-fontani-v-plovdiv/",
        )


class TestParseVisitplovdivFixture(unittest.TestCase):
    def test_parses_items_from_fixture(self):
        xml = load_fixture("visitplovdiv.html")
        # "90 Years – 90 Paintings" ends 02/07/2026, before this `today` — dropped as past.
        items = scrapers._parse_visitplovdiv(xml, today=dt.date(2026, 7, 3))
        self.assertEqual(len(items), 3)
        titles = [item["title"] for item in items]
        self.assertNotIn("90 Years – 90 Paintings", titles)

        summer = next(item for item in items if item["title"] == "Summer in the Old Town")
        self.assertEqual(summer["date_iso"], "2026-06-04")
        self.assertEqual(summer["url"], "https://www.visitplovdiv.com/en/node/15909")

        craft_school = next(item for item in items if item["title"] == "Craft School - Plovdiv 2026")
        self.assertEqual(craft_school["date_iso"], "2026-04-18")


class TestParsePlovdivBgFixture(unittest.TestCase):
    def test_parses_cards_from_fixture(self):
        html = load_fixture("plovdiv_bg.html")
        items = scrapers._parse_plovdiv_bg(html, today=dt.date(2026, 7, 2))
        self.assertEqual(len(items), 3)

        concert = next(item for item in items if "Илка Александрова" in item["title"])
        # Prose date has an explicit year ("На 20 септември 2026 г."), parsed unambiguously.
        self.assertEqual(concert["date_iso"], "2026-09-20")
        self.assertEqual(
            concert["url"],
            "https://www.plovdiv.bg/tsaritsata-na-avtorskata-narodna-pesen-ilka-aleksandrova-chestva-50-godini-na-stsena-s-golyam-kontsert-na-antichniya-teatar-v-plovdiv/",
        )

        talk = next(item for item in items if "Герджиков" in item["title"])
        self.assertIsNotNone(talk["date_iso"])


class TestParseLostinplovdivFixture(unittest.TestCase):
    def test_parses_cards_from_fixture(self):
        html = load_fixture("lostinplovdiv.html")
        items = scrapers._parse_lostinplovdiv(html, today=dt.date(2026, 7, 2))
        # The no-title card is skipped.
        self.assertEqual(len(items), 4)

        cafe = next(item for item in items if "TOP 100" in item["title"])
        self.assertIsNone(cafe["date_iso"])
        self.assertEqual(
            cafe["url"],
            "https://lostinplovdiv.com/en/articles/plovdiv-cafe-enters-the-top-100-in-the-world/",
        )

        digest = next(item for item in items if item["title"].startswith("What to do in Plovdiv"))
        # Title embeds its own date range ("26.06 - 02.07"); the start date is taken as-is
        # in today's year, not rolled forward like bg_date's future-listing convention.
        self.assertEqual(digest["date_iso"], "2026-06-26")
        self.assertEqual(digest["url"], "https://lostinplovdiv.com/en/articles/what-to-do-in-plovdiv-358/")


class TestLostinplovdivIsActionable(unittest.TestCase):
    """Real titles pulled from a production run: pure local-history trivia should stay
    blurb-only, while genuine activity/event guides should get a full-article fetch."""

    def test_trivia_titles_are_not_actionable(self):
        for title in [
            "Which Plovdiv actress starred in the first Bulgarian film?",
            "Who was the first Bulgarian photographer to capture Old Plovdiv?",
            "How did the people of Plovdiv spend their summer 100 years ago?",
        ]:
            self.assertFalse(scrapers._lostinplovdiv_is_actionable(title), title)

    def test_guide_titles_are_actionable(self):
        for title in [
            "5 caves in the Rhodope Mountains for the bravest",
            "Which events should we not miss in Plovdiv in June?",
            "Where are the most delicious pancakes in Plovdiv?",
        ]:
            self.assertTrue(scrapers._lostinplovdiv_is_actionable(title), title)


class TestFetchLostinplovdivDetail(unittest.TestCase):
    def test_extracts_and_caps_main_content_text(self):
        html = load_fixture("lostinplovdiv_detail.html")
        with patch("scrapers.fetch", return_value=html):
            text = scrapers._fetch_lostinplovdiv_detail("https://lostinplovdiv.com/en/articles/x")
        self.assertIn("EVENTS IN JUNE IN BORIS HRISTOV", text)
        self.assertIn("Annual dance performance", text)
        self.assertIn("benefit concert", text)

    def test_returns_empty_string_on_fetch_failure(self):
        with patch("scrapers.fetch", return_value=None):
            self.assertEqual(scrapers._fetch_lostinplovdiv_detail("https://lostinplovdiv.com/en/articles/x"), "")


class TestScrapeLostinplovdivEnrichment(unittest.TestCase):
    def test_enriches_only_actionable_items_up_to_the_fetch_cap(self):
        listing_html = load_fixture("lostinplovdiv.html")
        detail_html = load_fixture("lostinplovdiv_detail.html")

        def fake_fetch(url, *args, **kwargs):
            return listing_html if url == scrapers.LOSTINPLOVDIV_ARTICLES_URL else detail_html

        with patch("scrapers.fetch", side_effect=fake_fetch), \
             patch("scrapers.LOSTINPLOVDIV_MAX_DETAIL_FETCHES", 10):
            items = scrapers.scrape_lostinplovdiv()

        # "What to do in Plovdiv (...)" is actionable (activity keyword) -> enriched.
        digest = next(item for item in items if item["title"].startswith("What to do in Plovdiv"))
        self.assertIn("EVENTS IN JUNE IN BORIS HRISTOV", digest["description"])

        # "Who was the first Bulgarian photographer..." is pure trivia -> no activity
        # keyword, no numbered/where-question title -> stays as the short listing blurb.
        trivia = next(item for item in items if "photographer" in item["title"])
        self.assertNotIn("EVENTS IN JUNE", trivia["description"])
        self.assertIn("Nikola Stamenov", trivia["description"])


if __name__ == "__main__":
    unittest.main()
