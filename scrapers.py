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
_BG_MONTH_PATTERN = "|".join(BG_MONTHS)


def bg_date(text, today=None):
    """Best-effort parse of a Bulgarian date expression to an ISO date string, or None.
    Handles numeric dd.mm.yyyy / dd/mm/yyyy and 'DD <bulgarian month name> [YYYY]'.
    A missing year is assumed to be the next upcoming occurrence relative to `today`."""
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

    m = re.search(rf"\b(\d{{1,2}})[\-–]?\s*(?:ти|ви|ри|ми)?\.?\s*({_BG_MONTH_PATTERN})\.?\s*(\d{{4}})?", t)
    if m:
        day = int(m.group(1))
        month = BG_MONTHS[m.group(2)]
        year_text = m.group(3)
        year = int(year_text) if year_text else today.year
        try:
            date = dt.date(year, month, day)
        except ValueError:
            return None
        if not year_text and date < today:
            date = dt.date(year + 1, month, day)
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


def scrape_plovdiv2019(pages=PLOVDIV2019_PAGES):
    """Structured parser for plovdiv2019.eu's event archive. The site's own JS calendar
    widget just navigates to /en/events?f_time=all&page=N (see its resource_builds JS),
    which IS server-rendered — cards live in div.program-resume-wrapper with an h2 title,
    a <time datetime=...> for the start date, and a .location .value link."""
    items = []
    for page in range(1, pages + 1):
        html = fetch(f"{PLOVDIV2019_BASE}/en/events?f_time=all&page={page}")
        if not html:
            break
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", class_="program-resume-wrapper")
        if not cards:
            break
        for card in cards:
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
            url = link.get("href", "") if link else ""
            items.append(_make_item("plovdiv2019", title, when_text, date_iso, location, url))
    return items


BILET_BASE = "https://bilet.bg"


def scrape_bilet():
    """Structured parser for bilet.bg's homepage event carousels. Cards are <a href="/.../
    events/...">, with a title <p>, a date <span> ('YYYY-MM-DD HH:MM'), and a location <span>."""
    html = fetch(f"{BILET_BASE}/")
    if not html:
        return []
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
        href = card.get("href", "")
        url = href if href.startswith("http") else BILET_BASE + href
        items.append(_make_item("bilet", title, when_text, date_iso, location, url))
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
        try:
            if source in SCRAPERS:
                items = SCRAPERS[source]()
            elif source in RAW_FETCH_SOURCES:
                items = raw_fetch(source, RAW_FETCH_SOURCES[source])
            else:
                print(f"  [harvest] {source}: unknown source, skipping")
                continue
            print(f"  [harvest] {source}: {len(items)} items")
            all_items.extend(items)
        except Exception as exc:
            print(f"  [harvest] {source}: FAILED ({type(exc).__name__}: {exc})")

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
