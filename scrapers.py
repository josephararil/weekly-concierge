"""Harvest tier for the weekend concierge — collects raw event listings from Bulgarian
event/ticketing/municipal sites. NO LLM calls happen here; this only gathers material for
Stage 1 (FIND) to parse. Every source runs inside try/except so one dead site never loses
the others (see harvest()).

Two tiers per source:
  - Raw-fetch (RAW_FETCH_SOURCES): one URL -> one RawItem whose description is the page's
    visible text. FIND parses events out of the blob. Cheapest way to add a source.
  - Structured (SCRAPERS): a dedicated BeautifulSoup parser returns clean per-event
    RawItems (title/date/location split out). Worth it for high-value, stable sites.
"""

import re
import time
import datetime as dt
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import config as C

USER_AGENT = "Mozilla/5.0 (compatible; WeekendConciergeBot/1.0; family activity finder for Plovdiv)"

_FETCH_TIMEOUT = 15
_FETCH_RETRIES = 2
_RETRY_DELAYS = [2, 4]

RAW_FETCH_MAX_CHARS = 4000  # cap page-text blobs so one bloated source can't dominate FIND's input

# ── Bulgarian date parsing ───────────────────────────────────────────────────
BG_MONTHS = {
    "януари": 1, "февруари": 2, "март": 3, "април": 4, "май": 5, "юни": 6,
    "юли": 7, "август": 8, "септември": 9, "октомври": 10, "ноември": 11, "декември": 12,
}
EN_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
MONTHS = {**BG_MONTHS, **EN_MONTHS}
# Longest names first so alternation doesn't stop on a shorter prefix match.
_MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))


def _roll_forward_if_needed(date, year, month, day, today, year_given):
    if not year_given and date < today:
        return dt.date(year + 1, month, day)
    return date


def bg_date_range(text, today=None):
    """Best-effort parse of a date-range expression ('10-14 юли', '10 юли - 12 август
    2026') to a (start_iso, end_iso) tuple, or (None, None) if no range is found. When
    the range's start month is omitted it's assumed to match the end month."""
    if not text:
        return (None, None)
    today = today or dt.date.today()
    t = text.strip().lower()

    m = re.search(
        rf"\b(\d{{1,2}})\.?\s*(?:({_MONTH_PATTERN})\.?)?\s*[\-–—]\s*"
        rf"(\d{{1,2}})\.?\s*({_MONTH_PATTERN})\.?\s*(\d{{4}})?\b",
        t,
    )
    if not m:
        return (None, None)

    day1 = int(m.group(1))
    month1_text = m.group(2)
    day2 = int(m.group(3))
    month2 = MONTHS[m.group(4)]
    month1 = MONTHS[month1_text] if month1_text else month2
    year_text = m.group(5)
    year = int(year_text) if year_text else today.year

    try:
        start = dt.date(year, month1, day1)
        end = dt.date(year, month2, day2)
    except ValueError:
        return (None, None)

    if not year_text and end < today:
        year += 1
        start = dt.date(year, month1, day1)
        end = dt.date(year, month2, day2)

    return (start.isoformat(), end.isoformat())


def bg_date(text, today=None):
    """Best-effort parse of a Bulgarian/English date expression to an ISO date string,
    or None. Handles numeric dd.mm.yyyy / dd/mm/yyyy, bare dd.mm (no year), date ranges
    ('10-14 юли' -> the start date; use bg_date_range for the full span), and
    'DD <month name> [YYYY]' in Bulgarian or English. A missing year is assumed to be
    the next upcoming occurrence relative to `today`."""
    if not text:
        return None
    today = today or dt.date.today()
    t = text.strip().lower()

    m = re.search(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b", t)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return dt.date(year, month, day).isoformat()
        except ValueError:
            return None

    start, _end = bg_date_range(t, today)
    if start:
        return start

    m = re.search(rf"\b(\d{{1,2}})[\-–]?\s*(?:ти|ви|ри|ми)?\.?\s*({_MONTH_PATTERN})\.?\s*(\d{{4}})?", t)
    if m:
        day = int(m.group(1))
        month = MONTHS[m.group(2)]
        year_text = m.group(3)
        year = int(year_text) if year_text else today.year
        try:
            date = dt.date(year, month, day)
        except ValueError:
            return None
        date = _roll_forward_if_needed(date, year, month, day, today, bool(year_text))
        return date.isoformat()

    m = re.search(r"\b(\d{1,2})[./](\d{1,2})\b", t)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        try:
            date = dt.date(today.year, month, day)
        except ValueError:
            return None
        date = _roll_forward_if_needed(date, today.year, month, day, today, False)
        return date.isoformat()

    return None


# ── Shared HTTP/text helpers ─────────────────────────────────────────────────

def fetch(url, timeout=_FETCH_TIMEOUT, retries=_FETCH_RETRIES):
    """GET url with a polite UA and exponential-backoff retry on transient failures.
    Returns response text, or None if the fetch never succeeded."""
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.ok:
                return r.text
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])
                continue
            return None
        except requests.exceptions.RequestException:
            if attempt < retries:
                time.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])
                continue
            return None
    return None


def fetch_soup(url, timeout=_FETCH_TIMEOUT, retries=_FETCH_RETRIES):
    """fetch() + BeautifulSoup parse in one step. Returns None if the fetch failed."""
    html = fetch(url, timeout=timeout, retries=retries)
    if not html:
        return None
    return BeautifulSoup(html, "html.parser")


def resolve_url(base, href):
    """Join a possibly-relative href against a page's base URL."""
    if not href:
        return ""
    return urljoin(base, href)


def text_of(html, max_chars=RAW_FETCH_MAX_CHARS):
    """Strip scripts/styles and return the page's collapsed visible text, capped in length."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True)).strip()
    return text[:max_chars]


def _make_item(source, title, when_text="", date_iso=None, location="", url="", description=""):
    return {
        "source": source,
        "title": (title or "").strip(),
        "when_text": (when_text or "").strip(),
        "date_iso": date_iso,
        "location": (location or "").strip(),
        "url": url or "",
        "description": description or "",
    }


# ── Tier 1: raw-fetch sources (one-liner each) ───────────────────────────────
# Each entry is fetched verbatim and turned into a single RawItem whose description is
# the page-text blob; FIND parses events out of it. eventim.bg is included here rather
# than as a structured parser — it consistently times out / never resolves from this
# dev environment (likely Cloudflare bot-challenge), so a hand-written parser could
# never be verified against real HTML. It still runs every week; if CI's network can
# reach it, FIND gets a text blob, and if not, harvest() logs a clean FAILED.
# ticketstation.bg is also raw-fetch only: it's a client-rendered Vue SPA — the static
# HTML is just an empty <div id="app"> shell plus a compiled js/app.js bundle that
# fetches events from an API after JS executes. There is no event markup in the fetched
# HTML for BeautifulSoup to select, so a structured parser can't be written or verified
# against real HTML. Revisit only if the site ships server-rendered listing pages.
# ticketbg also has a structured parser (see SCRAPERS below); it's kept here as the
# raw-fetch fallback if the site's markup ever changes underneath the parser.
RAW_FETCH_SOURCES = {
    "eventim":              "https://www.eventim.bg/en/city/plovdiv-52/",
    "ticketstation":        "https://ticketstation.bg/",
    "ticketbg":             "https://www.ticket.bg/",
    "dtp":                  "https://dtp.bg/",
    "rnhm":                 "https://www.rnhm.org/",
    "oldplovdiv":           "https://oldplovdiv.bg/",
    "programata":           "https://programata.bg/sofia",
    "starazagora_tourist":  "https://tourist.stara-zagora.bg/",
    "plovdiv_bg":           "https://www.plovdiv.bg/",
    "visitplovdiv":         "https://visitplovdiv.com/",
    "marica":               "https://www.marica.bg/",
}


def raw_fetch(source, url):
    html = fetch(url)
    if not html:
        return []
    description = text_of(html)
    if not description:
        return []
    return [_make_item(source, title=f"{source} — page snapshot", url=url, description=description)]


# ── Tier 2: structured parsers ───────────────────────────────────────────────

PLOVDIV2019_BASE = "https://plovdiv2019.eu"
PLOVDIV2019_PAGES = 3  # each page holds ~12 cards; plenty of headroom over LOOKAHEAD_WEEKS


def _parse_plovdiv2019(html, today=None):
    """Pure parse of one plovdiv2019.eu events page. Cards live in
    div.program-resume-wrapper with an h2 title, a <time datetime=...> for the start
    date, and a .location .value link."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.find_all("div", class_="program-resume-wrapper"):
        h2 = card.find("h2")
        title = h2.get_text(strip=True) if h2 else ""
        if not title:
            continue
        time_tag = card.find("time")
        when_text = time_tag.get_text(" ", strip=True) if time_tag else ""
        date_iso = None
        if time_tag and time_tag.get("datetime"):
            date_iso = time_tag["datetime"].split(" ")[0]
        loc_value = card.select_one(".location .value")
        location = loc_value.get_text(strip=True) if loc_value else ""
        link = card.find("a", class_="go")
        url = resolve_url(PLOVDIV2019_BASE, link.get("href", "")) if link else ""
        items.append(_make_item("plovdiv2019", title, when_text, date_iso, location, url))
    return items


def scrape_plovdiv2019(pages=PLOVDIV2019_PAGES):
    """Structured parser for plovdiv2019.eu's event archive. The site's own JS calendar
    widget just navigates to /en/events?f_time=all&page=N (see its resource_builds JS),
    which IS server-rendered. Fetches each page and delegates parsing to
    _parse_plovdiv2019; stops early once a page yields no cards."""
    items = []
    for page in range(1, pages + 1):
        html = fetch(f"{PLOVDIV2019_BASE}/en/events?f_time=all&page={page}")
        if not html:
            break
        page_items = _parse_plovdiv2019(html)
        if not page_items:
            break
        items.extend(page_items)
    return items


BILET_BASE = "https://bilet.bg"


def _parse_bilet(html, today=None):
    """Pure parse of bilet.bg's homepage HTML. Cards are <a href="/.../events/...">,
    with a title <p>, a date <span> ('YYYY-MM-DD HH:MM'), and a location <span>."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.select("a[href*='/events/']"):
        title_tag = card.select_one("p.line-clamp-2, p.text-sm.font-bold")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            continue
        spans = card.find_all("span", class_=lambda c: c and "line-clamp" in c)
        when_text = spans[0].get_text(strip=True) if spans else ""
        location = spans[1].get_text(" ", strip=True) if len(spans) > 1 else ""
        date_match = re.match(r"(\d{4}-\d{2}-\d{2})", when_text)
        date_iso = date_match.group(1) if date_match else None
        url = resolve_url(BILET_BASE, card.get("href", ""))
        items.append(_make_item("bilet", title, when_text, date_iso, location, url))
    return items


def scrape_bilet():
    """Structured parser for bilet.bg's homepage event carousels. Fetches the homepage
    and delegates parsing to _parse_bilet."""
    html = fetch(f"{BILET_BASE}/")
    if not html:
        return []
    return _parse_bilet(html)


TICKETBG_BASE = "https://www.ticket.bg"

# Towns within the ~90-min Plovdiv radius (see config.RADIUS_MINUTES / FIND_PROMPT's example
# list). Sofia is farther but still worth surfacing as a look-ahead-only idea, never as a
# same-weekend suggestion — everything else nationwide (Varna, Burgas, Ruse, Gabrovo, Veliko
# Tarnovo, Sozopol, ...) is out of scope for this family.
_TICKETBG_RADIUS_CITIES = ("пловдив", "асеновград", "стара загора", "пазарджик", "хисар")
_TICKETBG_SOFIA = "софия"
_TICKETBG_SOFIA_LOOKAHEAD_DAYS = 14  # Sofia trips need real advance planning, not a same-week ask


def _parse_ticketbg_date(when_text, today):
    """ticket.bg gives no year, e.g. '01 Окт., Четв., 19:00 ч.' (day, abbreviated month,
    abbreviated weekday, time). Match the abbreviation as a prefix of a full month name and
    assume the next upcoming occurrence, same convention as bg_date's roll-forward."""
    m = re.match(r"(\d{1,2})\s+([^\s.,]+)\.?,", when_text.strip())
    if not m:
        return None
    day = int(m.group(1))
    abbr = m.group(2).strip().lower()
    month = next((num for name, num in BG_MONTHS.items() if name.startswith(abbr)), None)
    if month is None:
        return None
    try:
        date = dt.date(today.year, month, day)
    except ValueError:
        return None
    if date < today:
        date = dt.date(today.year + 1, month, day)
    return date.isoformat()


def _parse_ticketbg(html, today=None):
    """Pure parse of ticket.bg's homepage HTML. Cards are div.productItem, each with an
    a.productItemLink (href + title attribute = event title), a strong.sr-only with
    'Title - Venue - City / Country', and a span.productEventStarts with the date/time."""
    today = today or dt.date.today()
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.find_all("div", class_="productItem"):
        link = card.find("a", class_="productItemLink")
        if not link:
            continue
        title = (link.get("title") or "").strip()
        if not title:
            continue

        sr = card.find("strong", class_="sr-only")
        location = ""
        if sr:
            parts = [p.strip() for p in sr.get_text(strip=True).split(" - ") if p.strip()]
            if len(parts) >= 2:
                city = parts[-1].split("/")[0].strip()
                venue = parts[-2]
                location = f"{venue}, {city}" if venue and venue != city else city
        city_lower = location.lower()
        in_radius = any(town in city_lower for town in _TICKETBG_RADIUS_CITIES)
        is_sofia = _TICKETBG_SOFIA in city_lower

        starts = card.find("span", class_="productEventStarts")
        when_text = starts.get_text(strip=True) if starts else ""
        date_iso = _parse_ticketbg_date(when_text, today) if when_text else None

        if not in_radius:
            if not is_sofia:
                continue
            if date_iso and (dt.date.fromisoformat(date_iso) - today).days < _TICKETBG_SOFIA_LOOKAHEAD_DAYS:
                continue

        url = resolve_url(TICKETBG_BASE, link.get("href", ""))
        items.append(_make_item("ticketbg", title, when_text, date_iso, location, url))
    return items


def scrape_ticketbg():
    """Structured parser for ticket.bg's homepage event grid. Fetches the homepage and
    delegates parsing to _parse_ticketbg."""
    html = fetch(f"{TICKETBG_BASE}/")
    if not html:
        return []
    return _parse_ticketbg(html)


def scrape_facebook(source=None):
    """Documented stub. Facebook event pages require an authenticated session and
    aggressively block anonymous/automated fetches (login walls, anti-bot checks) —
    not solvable with plain requests + BeautifulSoup. Left unimplemented on purpose;
    FIND's web search partially compensates by surfacing FB-announced events indexed
    elsewhere. Revisit if/when a lightweight auth path is worth the maintenance cost."""
    raise NotImplementedError("scrape_facebook: Facebook requires auth/anti-bot handling, not yet implemented")


SCRAPERS = {
    "plovdiv2019": scrape_plovdiv2019,
    "bilet": scrape_bilet,
    "ticketbg": scrape_ticketbg,
    "facebook": scrape_facebook,
}


# ── Harvest ───────────────────────────────────────────────────────────────────

def _dedupe(items):
    seen = set()
    out = []
    for item in items:
        key = (item["title"].strip().lower(), item.get("date_iso"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def harvest(today=None):
    """Run every source in config.ENABLED_SOURCES inside try/except and return the
    combined, deduped, volume-capped list of RawItems. A single dead source never
    takes down the run — its failure is logged and it contributes []."""
    all_items = []
    for source in C.ENABLED_SOURCES:
        has_structured = source in SCRAPERS
        has_raw_fetch = source in RAW_FETCH_SOURCES
        if not has_structured and not has_raw_fetch:
            print(f"  [harvest] {source}: unknown source, skipping")
            continue

        items, path = [], "structured"
        if has_structured:
            try:
                items = SCRAPERS[source]()
            except Exception as exc:
                print(f"  [harvest] {source}: structured parser FAILED ({type(exc).__name__}: {exc})")

        if not items and has_raw_fetch:
            path = "raw-fetch fallback" if has_structured else "raw-fetch"
            try:
                items = raw_fetch(source, RAW_FETCH_SOURCES[source])
            except Exception as exc:
                print(f"  [harvest] {source}: raw-fetch FAILED ({type(exc).__name__}: {exc})")
                continue

        print(f"  [harvest] {source}: {len(items)} item(s) [{path}]")
        all_items.extend(items)

    deduped = _dedupe(all_items)
    capped = deduped[:C.MAX_HARVEST_ITEMS]
    if len(deduped) > len(capped):
        print(f"  [harvest] capped {len(deduped)} deduped items down to {len(capped)}")
    return capped


if __name__ == "__main__":
    today = dt.date.today().isoformat()
    print(f"Harvesting for today={today}...")
    results = harvest(today)
    print(f"\nTotal: {len(results)} items after dedupe/cap")
    by_source = {}
    for item in results:
        by_source[item["source"]] = by_source.get(item["source"], 0) + 1
    for source, count in sorted(by_source.items()):
        print(f"  {source}: {count}")
