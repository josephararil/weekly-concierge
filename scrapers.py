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
from urllib.parse import urljoin, urlencode

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
    Returns response text, or None if the fetch never succeeded. Prints the concrete
    HTTP status or exception on failure — silent None returns here are what made past
    scraper failures unexplainable in the logs (e.g. plovdiv_bg going from 20 items to 0
    with no clue why)."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "bg,en-US;q=0.7,en;q=0.3",
    }
    reason = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.ok:
                return r.text
            reason = f"HTTP {r.status_code}"
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])
                continue
            break
        except requests.exceptions.RequestException as exc:
            reason = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])
                continue
            break
    print(f"  [fetch] {url}: failed ({reason})")
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
# than as a structured parser. The HTML page itself is reachable, but a structured
# parser was ruled out after investigating two routes to its real event data:
#   1. Direct JSON API: eventim.bg's own suggest-widget JS reveals the real backing
#      endpoint (public-api.eventim.com/websearch/search/api/exploration/v1/productGroups,
#      apiClientId "web__eventim-bgr") and its exact query params (webId, search_term,
#      language, page, page_size, ...), reverse-engineered from the site's own bundled
#      JS. But every request to that path — correct params or not — gets a 403 "Access
#      Denied" from Akamai's edge (the API host's own root path 404s cleanly, so this is
#      a deliberate WAF block on that specific path, not a routing/param problem). No
#      combination of browser-like headers changes the outcome; it looks like Akamai Bot
#      Manager fingerprinting the TLS handshake, which plain `requests` can't spoof.
#   2. pyventim (the suggested library fallback): pulls in playwright, patchright,
#      curl_cffi and scrapling as transitive dependencies — i.e. it bypasses Akamai with
#      browser automation / TLS impersonation under the hood, exactly the
#      heavy-dependency approach this project avoids. Not adopted.
# Both routes fail without a headless browser, so eventim.bg stays raw-fetch. It still
# runs every week; if CI's network can reach it, FIND gets a text blob, and if not,
# harvest() logs a clean FAILED.
# ticketstation.bg is also raw-fetch only: it's a client-rendered Vue SPA — the static
# HTML is just an empty <div id="app"> shell plus a compiled js/app.js bundle that
# fetches events from an API after JS executes. There is no event markup in the fetched
# HTML for BeautifulSoup to select, so a structured parser can't be written or verified
# against real HTML. Revisit only if the site ships server-rendered listing pages.
# ticketbg, programata, visitplovdiv, plovdiv_bg and lostinplovdiv also have structured
# parsers (see SCRAPERS below); they're kept here as the raw-fetch fallback if a site's
# markup/endpoint ever changes underneath the parser.
# programata's raw-fetch entry still points at /sofia (a broader page) rather than the
# structured parser's /kids/ category, so the fallback blob covers more ground than the
# structured path if that one ever breaks. Same idea for plovdiv_bg's raw-fetch entry,
# which points at the homepage rather than the structured parser's /feed/ RSS.
RAW_FETCH_SOURCES = {
    "eventim":              "https://www.eventim.bg/en/city/plovdiv-52/",
    "ticketstation":        "https://ticketstation.bg/",
    "ticketbg":             "https://www.ticket.bg/",
    "dtp":                  "https://dtp.bg/",
    "rnhm":                 "https://www.rnhm.org/",
    "oldplovdiv":           "https://oldplovdiv.bg/",
    "programata":           "https://programata.bg/sofia",
    "programata_adult":     "https://programata.bg/kino/",
    "plovdiv_bg":           "https://www.plovdiv.bg/",
    "visitplovdiv":         "https://visitplovdiv.com/",
    "marica":               "https://www.marica.bg/",
    "lostinplovdiv":          "https://lostinplovdiv.com/",
    "plovdiv24":            "https://www.plovdiv24.bg/",
    "trafficnews":          "https://trafficnews.bg/plovdiv/",
    "podtepeto":            "https://podtepeto.com/",
    "dcnews":               "https://dcnews.bg/",
    "plovdivnews":          "https://plovdivnews.bg/category/plovdiv/",
    # Raw-fetch only (no structured parser — see the comment above each name for why):
    "plovdiv_online":       "https://plovdiv-online.com/",
    "plovdivtime":          "https://plovdivtime.bg/",
    "sphotel":              "https://sphotel.net/blog/",
}
# plovdiv_online: a genuinely Plovdiv-branded site ("Новини от Пловдив"), but its feed's
# signal is weaker than the four structured sources above — mostly crime/tabloid/national
# opinion pieces, with only occasional real local civic content (e.g. a duplicate of the
# Тракия/Хемус lane-restriction story dcnews also carries). Kept raw-fetch: cheap to add,
# not worth a dedicated parser at this signal-to-noise ratio.
# plovdivtime: no RSS feed (/feed/ 404s), but its "Ела и виж" (Come and See) section runs
# a genuinely useful recurring feature — a dated "Къде да отидем в <day>, Пловдив" (Where
# to go on <day> in Plovdiv) digest — visible directly on the homepage. No structured
# parser because there's no feed and no per-item date/link markup to key off without one;
# FIND parses the homepage blob same as any other raw-fetch source.
# sphotel: a hotel's blog, low prestige, but its RSS feed matches the lead's prediction —
# recurring seasonal roundups ("17 free things to do in Plovdiv", "Concerts in Plovdiv
# 2026", "Summer 2026: Events in Plovdiv"). Like lostinplovdiv, the blurb visible in the
# feed is too thin to extract a dozen dated entries from a roundup; unlike lostinplovdiv,
# the volume here (10 posts, mostly evergreen) didn't justify building the same
# detail-fetch enrichment machinery, so this stays a plain raw-fetch of the blog index.


def raw_fetch(source, url):
    html = fetch(url)
    if not html:
        return []
    description = text_of(html)
    if not description:
        print(f"  [raw_fetch] {source}: fetched {url} ({len(html)} chars) but extracted no visible text")
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

# Towns within the ~90-min Plovdiv radius (see config.RADIUS_MINUTES / the FIND prompts' example
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


PROGRAMATA_BASE = "https://programata.bg"
PROGRAMATA_KIDS_URL = f"{PROGRAMATA_BASE}/kids/"
PROGRAMATA_ADULT_URLS = [
    f"{PROGRAMATA_BASE}/kino/",
    f"{PROGRAMATA_BASE}/muzika/kontserti-partita/",
    f"{PROGRAMATA_BASE}/izlozhbi/",
    f"{PROGRAMATA_BASE}/stsena/postanovki/",
]


def _parse_programata(html, today=None):
    """Pure parse of programata.bg's Kids category page. Cards live in div.post-list-entry
    with an h3 > a for title/url. programata.bg is an editorial/magazine site, not an event
    calendar: listing cards carry no date or venue field — dates only appear as free-form
    prose inside each article body (e.g. 'every Saturday in June and July at 21:30'), too
    unstructured to regex reliably. date_iso/location stay unset here; FIND/SKEPTIC resolve
    the date from the linked article or their own search."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.find_all("div", class_="post-list-entry"):
        h3 = card.find("h3")
        link = h3.find("a") if h3 else None
        title = link.get_text(strip=True) if link else ""
        if not title:
            continue
        url = resolve_url(PROGRAMATA_BASE, link.get("href", ""))
        items.append(_make_item("programata", title, url=url))
    return items


def scrape_programata():
    """Structured parser for programata.bg's Kids category — chosen over the generic
    /sofia page (a venue directory listing cinemas, not events) as the category most
    relevant to this family. Fetches the page and delegates parsing to _parse_programata."""
    html = fetch(PROGRAMATA_KIDS_URL)
    if not html:
        return []
    return _parse_programata(html)


def scrape_programata_adult():
    """Structured parser for programata.bg's four adult-interest categories (cinema,
    concerts, exhibitions, theatre) — same card markup as the Kids category, so this
    reuses _parse_programata unchanged rather than writing a new parser. National site
    with a strong Sofia skew; no Plovdiv filter here, FIND/SKEPTIC own that judgement.
    Each category is fetched/parsed in its own try/except so one dead category yields
    [] without losing the others."""
    items = []
    for url in PROGRAMATA_ADULT_URLS:
        try:
            html = fetch(url)
            if not html:
                continue
            for item in _parse_programata(html):
                item["source"] = "programata_adult"
                items.append(item)
        except Exception as e:
            print(f"  [scrape_programata_adult] {url}: {e}")
    return items


VISITPLOVDIV_BASE = "https://www.visitplovdiv.com"
# The /en/eventsplovdiv "culture calendar" page itself renders empty (<div class="event_block">
# stays blank) — its own JS fills it in by calling this XML endpoint after page load. Rather
# than scrape the empty shell, we call the same endpoint directly, exactly like plovdiv2019's
# JS-navigates-to-a-server-rendered-page trick. Response is XML, not HTML: BeautifulSoup needs
# features="xml" here, because "html.parser" treats <link> as the void HTML tag and silently
# drops its text content (the node URL).


def _parse_visitplovdiv_date(text):
    """Parse the endpoint's 'DD/MM/YYYY[, DD/MM/YYYY, ...]' date field, taking the first
    occurrence (recurring events list one date per recurrence)."""
    if not text:
        return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", text.split(",")[0].strip())
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def _parse_visitplovdiv(html, today=None):
    """Pure parse of visitplovdiv.com's culture-calendar XML feed. Each <items> node holds
    title/sdate/edate/date/content/type/link; sdate/edate may list several comma-separated
    recurrence dates, we take the first as this event's date_iso. Events whose edate has
    already passed are dropped; ongoing/future ones are kept even if sdate is in the past."""
    today = today or dt.date.today()
    soup = BeautifulSoup(html, "xml")
    items = []
    for node in soup.find_all("items"):
        title_tag = node.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            continue

        edate_iso = _parse_visitplovdiv_date(node.find("edate").get_text(strip=True)) if node.find("edate") else None
        if edate_iso and dt.date.fromisoformat(edate_iso) < today:
            continue

        sdate_tag = node.find("sdate")
        date_iso = _parse_visitplovdiv_date(sdate_tag.get_text(strip=True)) if sdate_tag else None
        when_tag = node.find("date")
        when_text = when_tag.get_text(strip=True) if when_tag else ""
        content_tag = node.find("content")
        description = content_tag.get_text(" ", strip=True) if content_tag else ""
        link_tag = node.find("link")
        url = resolve_url(VISITPLOVDIV_BASE, link_tag.get_text(strip=True)) if link_tag else ""
        items.append(_make_item("visitplovdiv", title, when_text, date_iso, "", url, description))
    return items


def scrape_visitplovdiv(lookahead_days=None):
    """Structured parser for visitplovdiv.com's culture calendar. Queries the site's own
    AJAX endpoint (see _parse_visitplovdiv) for events between today and today+lookahead_days
    (defaults to config.LOOKAHEAD_WEEKS plus a week of headroom), then delegates to
    _parse_visitplovdiv."""
    today = dt.date.today()
    lookahead_days = lookahead_days or (C.LOOKAHEAD_WEEKS + 1) * 7
    end = today + dt.timedelta(days=lookahead_days)
    fmt = "%d/%m/%Y"
    params = {
        "between_date_filter[value][date]": today.strftime(fmt),
        "field_fedb_value[min][date]": today.strftime(fmt),
        "field_fedb_value[max][date]": end.strftime(fmt),
        "field_fedb_value2[min][date]": today.strftime(fmt),
        "field_fedb_value2[max][date]": end.strftime(fmt),
    }
    xml = fetch(f"{VISITPLOVDIV_BASE}/en/cevents_page_month?{urlencode(params)}")
    if not xml:
        return []
    return _parse_visitplovdiv(xml)


PLOVDIV_BG_BASE = "https://www.plovdiv.bg"
PLOVDIV_BG_FEED_URL = f"{PLOVDIV_BG_BASE}/feed/"
PLOVDIV_BG_PAGES = 2  # WordPress feed paginates via ?paged=N, ~10 posts/page.
# Upgraded from HTML-scraping the /category/events/ listing to the site-wide RSS feed.
# The events-only category never carries the Транспорт/Актуално posts — bus reroutes,
# road closures, water-main work — that are exactly the highest-value civic_notice
# content this product exists for; the feed does, for free, plus a real per-item
# permalink instead of an HTML card scrape. wp-json is disabled site-wide (404 with a
# WordPress-branded 404 page, not a CDN block — confirmed both /wp-json/ and
# /wp-json/wp/v2/categories 404 the same way as a random bad path), but /feed/ and
# /category/events/feed/ both 200 with real RSS. This project uses the broader
# /feed/, not the category-scoped one.


NEWS_RSS_DESCRIPTION_MAX_CHARS = 2000
# Several of these sites' RSS plugins append "Материалът <a>Title</a> е публикуван за
# пръв път на <a>Site</a>." (lit. "This post was first published on Site") to the end of
# every <description>/<content:encoded> blob. It's pure noise for FIND — the real URL is
# already in <link> — so it's stripped rather than passed through as if it were content.
_WP_RSS_FOOTER_RE = re.compile(r"\s*Материалът\b.*$", re.S)


def _clean_news_rss_html(html_text):
    """Strip HTML tags from an RSS <description>/<content:encoded> CDATA blob (some of
    these feeds embed real markup — <p>, <a>, <img> — inside the CDATA rather than plain
    text) and drop the "first published on" footer. Caps length since <content:encoded>
    can run to a full multi-paragraph article plus image captions."""
    if not html_text:
        return ""
    text = BeautifulSoup(html_text, "html.parser").get_text(" ", strip=True)
    text = _WP_RSS_FOOTER_RE.sub("", text).strip()
    return text[:NEWS_RSS_DESCRIPTION_MAX_CHARS]


def _parse_news_rss(xml, source, today=None):
    """Pure parse of a standard RSS 2.0 <item> feed (title/link/description, optionally
    a richer <content:encoded>, per item) — shared by every plain news-RSS source below
    (plovdiv.bg, trafficnews, podtepeto, dcnews, plovdivnews) since they all emit the
    same shape regardless of platform (WordPress or a bespoke CMS). Prefers
    <content:encoded> over <description> when present: several sources' feeds carry the
    full article body there for free (no extra per-item fetch, unlike lostinplovdiv's
    detail-fetch enrichment), while <description> alone is a one-sentence teaser too
    thin to extract an event's actual time/age-range/booking details from. pubDate,
    where present, is the article's PUBLISH date, not an event/disruption date, so it's
    ignored in favor of best-effort bg_date() extraction from the free-form Bulgarian
    prose in the body text. Many posts are general news with no event/disruption date at
    all; those simply get date_iso=None and FIND/SKEPTIC decide relevance."""
    soup = BeautifulSoup(xml, "xml")
    items = []
    for node in soup.find_all("item"):
        title_tag = node.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            continue
        link_tag = node.find("link")
        url = link_tag.get_text(strip=True) if link_tag else ""
        content_tag = node.find("encoded")
        desc_tag = node.find("description")
        raw_html = (content_tag.get_text() if content_tag else "") or (desc_tag.get_text() if desc_tag else "")
        description = _clean_news_rss_html(raw_html)
        date_iso = bg_date(description, today) if description else None
        items.append(_make_item(source, title, date_iso=date_iso, url=url, description=description))
    return items


def _parse_plovdiv_bg(xml, today=None):
    """Pure parse of plovdiv.bg's site-wide RSS feed (WordPress default /feed/).
    Upgraded from HTML-scraping the /category/events/ listing to this feed because the
    events-only category never carries the Транспорт/Актуално posts — bus reroutes,
    road closures, water-main work — that are exactly the highest-value civic_notice
    content this product exists for; the feed does, for free, plus a real per-item
    permalink instead of an HTML card scrape. wp-json is disabled site-wide (404 with a
    WordPress-branded 404 page, not a CDN block — confirmed both /wp-json/ and
    /wp-json/wp/v2/categories 404 the same way as a random bad path), but /feed/ and
    /category/events/feed/ both 200 with real RSS. This project uses the broader
    /feed/, not the category-scoped one."""
    return _parse_news_rss(xml, "plovdiv_bg", today)


def scrape_plovdiv_bg(pages=PLOVDIV_BG_PAGES):
    """Structured parser for plovdiv.bg's site-wide RSS feed. Fetches up to `pages`
    (newest first; WordPress paginates a feed via ?paged=N) and delegates parsing to
    _parse_plovdiv_bg; stops early once a page yields no items."""
    items = []
    for page in range(1, pages + 1):
        url = PLOVDIV_BG_FEED_URL if page == 1 else f"{PLOVDIV_BG_FEED_URL}?paged={page}"
        xml = fetch(url)
        if not xml:
            break  # fetch() already logged the concrete reason
        page_items = _parse_plovdiv_bg(xml)
        if not page_items:
            break
        items.extend(page_items)
    return items


TRAFFICNEWS_FEED_URL = "https://trafficnews.bg/rss/category/plovdiv/"
# trafficnews.bg is a national outlet, but its own /plovdiv/ category (confirmed via a
# manual href scan of its homepage) is genuinely Plovdiv-scoped news, and it publishes a
# dedicated per-category RSS feed with no wp-json/HTML-scrape needed. One fetch returns
# 50 items (5x a typical WordPress feed's default page size) — no pagination needed.
# The feed has no <pubDate> at all (unlike the WordPress sources below), so date_iso
# relies entirely on bg_date() finding a date in the description, which is rare for
# ordinary news teasers; most items will carry date_iso=None, same as programata.


def _parse_trafficnews(xml, today=None):
    """Pure parse of trafficnews.bg's Plovdiv-category RSS feed. See _parse_news_rss."""
    return _parse_news_rss(xml, "trafficnews", today)


def scrape_trafficnews():
    """Structured parser for trafficnews.bg's dedicated Plovdiv-category feed."""
    xml = fetch(TRAFFICNEWS_FEED_URL)
    if not xml:
        return []
    return _parse_trafficnews(xml)


PODTEPETO_FEED_URL = "https://podtepeto.com/feed/"
# "Podtepeto" (лит. "under the tepeta/hills") is itself a Plovdiv nickname; this is a
# dedicated Plovdiv news site (confirmed: 95 Plovdiv mentions across just 10 items in
# its own default WordPress /feed/ — the densest signal of every candidate evaluated),
# covering exactly the mix this product wants: family activities (a free kids' LEGO
# workshop), culture (an opera-at-the-antique-theatre gig, a film festival), and civic
# awareness (a heatwave warning, a public safety protest) in the same feed.


def _parse_podtepeto(xml, today=None):
    """Pure parse of podtepeto.com's default WordPress RSS feed. See _parse_news_rss."""
    return _parse_news_rss(xml, "podtepeto", today)


def scrape_podtepeto():
    """Structured parser for podtepeto.com's WordPress /feed/."""
    xml = fetch(PODTEPETO_FEED_URL)
    if not xml:
        return []
    return _parse_podtepeto(xml)


DCNEWS_FEED_URL = "https://dcnews.bg/feed/"
# dcnews.bg's own title tag is "Новини от Пловдив, Асеновград и региона" (News from
# Plovdiv, Asenovgrad and the region) — genuinely regional, and its default feed already
# carries exactly the civic-disruption content this product wants most: a water-main
# reconstruction in Строево, toll-camera lane restrictions on the Тракия/Хемус
# motorways, a wildfire near Hisar — all inside the ~90-min radius.


def _parse_dcnews(xml, today=None):
    """Pure parse of dcnews.bg's default WordPress RSS feed. See _parse_news_rss."""
    return _parse_news_rss(xml, "dcnews", today)


def scrape_dcnews():
    """Structured parser for dcnews.bg's WordPress /feed/."""
    xml = fetch(DCNEWS_FEED_URL)
    if not xml:
        return []
    return _parse_dcnews(xml)


PLOVDIVNEWS_FEED_URL = "https://plovdivnews.bg/category/plovdiv/feed/"
# plovdivnews.bg's homepage/default feed is a general Bulgarian news portal (world,
# politics, horoscopes) that happens to be Plovdiv-branded — its top-level /feed/ skews
# national. Its own nav exposes a genuine /category/plovdiv/ section, and that
# category's feed is real local news (a rowing-championship win, a bus reroute, a heat
# health advisory). Overlaps some with plovdiv_bg and dcnews (same underlying stories,
# independently written up) — harmless, harvest()'s title+date dedupe absorbs it.


def _parse_plovdivnews(xml, today=None):
    """Pure parse of plovdivnews.bg's Plovdiv-category RSS feed. See _parse_news_rss."""
    return _parse_news_rss(xml, "plovdivnews", today)


def scrape_plovdivnews():
    """Structured parser for plovdivnews.bg's /category/plovdiv/feed/."""
    xml = fetch(PLOVDIVNEWS_FEED_URL)
    if not xml:
        return []
    return _parse_plovdivnews(xml)


LOSTINPLOVDIV_BASE = "https://lostinplovdiv.com"
# The site dropped its old /en/articles archive listing (now 404) after a theme change
# (Jannah/WordPress) and its front page is now the newest-first feed instead. Cards are
# no longer paginated with a query param either — /en/ alone already yields ~56 cards,
# well over LOSTINPLOVDIV_LIMIT.
LOSTINPLOVDIV_ARTICLES_URL = f"{LOSTINPLOVDIV_BASE}/en/"
LOSTINPLOVDIV_LIMIT = 30  # the listing has no pagination param — one fetch returns dozens
# of cards, newest first. This is a weekly harvest, so we only need the newest slice: it
# reliably covers the latest "What to do in Plovdiv (DD.MM - DD.MM)" weekly digest plus
# several evergreen thematic roundups.
LOSTINPLOVDIV_DETAIL_MAX_CHARS = 2500  # enough for a full dated event list (e.g. a "which
# events in June" roundup runs ~2000 chars); the listing blurb alone is only 1-2 sentences.
LOSTINPLOVDIV_MAX_DETAIL_FETCHES = 10  # cap extra per-article requests per harvest run
_LOSTINPLOVDIV_WEEK_RE = re.compile(r"\((\d{1,2})\.(\d{1,2})\s*[-–—]\s*(\d{1,2})\.(\d{1,2})\)")
# The listing blurb is too short to be useful for actual activity/event guides (e.g. "which
# events should we not miss in June" collapses to one teaser sentence with zero of the dates
# it lists), but is a waste of a request for pure local-history trivia ("who was the first
# Bulgarian photographer to..."). This heuristic flags titles that read as an actionable
# guide — a numbered listicle ("5 caves...", "5 sports clubs...") or one containing an
# activity/event word — so only those get a follow-up fetch of the full article body.
_LOSTINPLOVDIV_NUMBERED_TITLE_RE = re.compile(r"^\s*\d+\s+\S")
_LOSTINPLOVDIV_WHERE_QUESTION_RE = re.compile(r"\bwhere\s+(is|are|to|can|will)\b", re.IGNORECASE)
_LOSTINPLOVDIV_ACTIVITY_WORDS_RE = re.compile(
    r"\b(events?|concerts?|festivals?|performances?|shows?|exhibitions?|workshops?|fairs?|"
    r"markets?|premieres?|screenings?|cinemas?|camps?|classes?|tours?|celebrations?|parties?|"
    r"openings?|kids?|children|family|playgrounds?|activit\w*|guides?|places?|spots?|parks?|"
    r"museums?|caf[eé]s?|coffee|restaurants?|food|bakeri\w*|clubs?|programs?|schedules?|"
    r"calendars?|weekends?|not\s*miss|what\s*to\s*do|things\s*to\s*do)\b",
    re.IGNORECASE,
)


def _lostinplovdiv_is_actionable(title):
    """True if a listing title reads as an activity/event guide worth a full-article
    fetch, rather than pure local-history/trivia content (see the comment above)."""
    if _LOSTINPLOVDIV_NUMBERED_TITLE_RE.match(title):
        return True
    if _LOSTINPLOVDIV_WHERE_QUESTION_RE.search(title):
        return True
    return bool(_LOSTINPLOVDIV_ACTIVITY_WORDS_RE.search(title))


def _fetch_lostinplovdiv_detail(url):
    """Fetch one lostinplovdiv article page and return its full body text (the
    entry-content paragraphs, whitespace-collapsed and capped), or "" on any failure.
    Only called for titles _lostinplovdiv_is_actionable() flags as worth it."""
    html = fetch(url)
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find(class_="entry-content")
    if not main:
        return ""
    text = re.sub(r"\s+", " ", main.get_text(" ", strip=True)).strip()
    return text[:LOSTINPLOVDIV_DETAIL_MAX_CHARS]


def _parse_lostinplovdiv_week_title(title, today):
    """Extract the start date from a 'What to do in Plovdiv (DD.MM - DD.MM)' weekly
    digest title (no year given). Unlike bg_date's roll-forward convention for future
    listings, this digest describes the current/just-finished week relative to its
    publish date, so the date is taken at face value in today's year rather than rolled
    forward. Returns None for any other article title."""
    m = _LOSTINPLOVDIV_WEEK_RE.search(title)
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    try:
        return dt.date(today.year, month, day).isoformat()
    except ValueError:
        return None


def _parse_lostinplovdiv(html, today=None):
    """Pure parse of lostinplovdiv.com's /en/ front-page feed (its Jannah WordPress theme).
    Cards carry class post-item, with an h2.post-title > a for title/url, a .post-meta
    .date span for the publish date (not an event date — often relative, e.g. '5 hours
    ago'), and a p.post-excerpt blurb. Most articles are editorial (evergreen roundups,
    local trivia, one-off news) with no event date at all — those get date_iso=None for
    FIND/SKEPTIC to resolve. The recurring 'What to do in Plovdiv (DD.MM - DD.MM)' weekly
    digest is the one title format that embeds its own date range, parsed by
    _parse_lostinplovdiv_week_title. Card hrefs are relative to /en/ (no leading slash), so
    they're resolved against LOSTINPLOVDIV_ARTICLES_URL, not the bare domain."""
    today = today or dt.date.today()
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.find_all(class_="post-item")[:LOSTINPLOVDIV_LIMIT]:
        h2 = card.find(class_="post-title")
        link = h2.find("a") if h2 else None
        title = link.get_text(strip=True) if link else ""
        if not title:
            continue
        meta = card.find(class_="post-meta")
        date_span = meta.find(class_="date") if meta else None
        when_text = date_span.get_text(strip=True) if date_span else ""
        date_iso = _parse_lostinplovdiv_week_title(title, today)
        desc_tag = card.find(class_="post-excerpt")
        description = desc_tag.get_text(" ", strip=True) if desc_tag else ""
        url = resolve_url(LOSTINPLOVDIV_ARTICLES_URL, link.get("href", ""))
        items.append(_make_item("lostinplovdiv", title, when_text, date_iso, "", url, description))
    return items


def scrape_lostinplovdiv():
    """Structured parser for lostinplovdiv.com's English front-page feed — the site's own
    bilingual, hand-curated guide to Plovdiv (weekly what-to-do digests, evergreen
    thematic roundups, local food/culture spots). Fetches /en/ and delegates to
    _parse_lostinplovdiv, then replaces the one-sentence listing blurb with the full
    article body (see _fetch_lostinplovdiv_detail) for titles that look like an actual
    activity/event guide, up to LOSTINPLOVDIV_MAX_DETAIL_FETCHES extra requests — the
    blurb alone is too thin for FIND to extract, say, a June event calendar's dozen
    dated entries from."""
    html = fetch(LOSTINPLOVDIV_ARTICLES_URL)
    if not html:
        return []
    items = _parse_lostinplovdiv(html)
    fetched = 0
    for item in items:
        if fetched >= LOSTINPLOVDIV_MAX_DETAIL_FETCHES:
            break
        if not _lostinplovdiv_is_actionable(item["title"]):
            continue
        detail = _fetch_lostinplovdiv_detail(item["url"])
        if detail:
            item["description"] = detail
        fetched += 1
    return items


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
    "programata": scrape_programata,
    "programata_adult": scrape_programata_adult,
    "visitplovdiv": scrape_visitplovdiv,
    "plovdiv_bg": scrape_plovdiv_bg,
    "lostinplovdiv": scrape_lostinplovdiv,
    "trafficnews": scrape_trafficnews,
    "podtepeto": scrape_podtepeto,
    "dcnews": scrape_dcnews,
    "plovdivnews": scrape_plovdivnews,
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


def _round_robin(per_source_items):
    """Interleave a list of per-source item lists (in ENABLED_SOURCES order) one item at
    a time. Plain concatenation plus a hard MAX_HARVEST_ITEMS cap means whichever
    sources are listed first in config.ENABLED_SOURCES eat the entire cap — with the
    civic/news sources added after the older event sources, that silently zeroed out
    every one of them every run once total volume crossed 200 (discovered while adding
    them: total pre-cap went from ~180 to 320, and none of the new sources survived the
    cap at all). Round-robin ensures every source gets a fair share before any single
    high-volume source (bilet, plovdiv2019, trafficnews) can exhaust the budget alone."""
    out = []
    index = 0
    while True:
        added_any = False
        for items in per_source_items:
            if index < len(items):
                out.append(items[index])
                added_any = True
        if not added_any:
            break
        index += 1
    return out


def harvest(today=None):
    """Run every source in config.ENABLED_SOURCES inside try/except and return the
    combined, deduped, volume-capped list of RawItems. A single dead source never
    takes down the run — its failure is logged and it contributes []."""
    per_source_items = []
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
        per_source_items.append(items)

    deduped = _dedupe(_round_robin(per_source_items))
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
