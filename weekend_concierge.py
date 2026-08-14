"""
weekend_concierge.py — Weekend Concierge (weekly; HARVEST -> FIND -> SKEPTIC -> anti-repeat
-> CONCIERGE -> email)

Pipeline (see PLAN.md for the full diagram):
  HARVEST   (scrapers.py, no LLM)      -- per-source try/except -> [], never crashes the run.
  Stage 1   FIND x2 (search + reasoning) -- TWO independent calls, family and adult, each with
                                          its own brief and schema. The family path's quality
                                          comes from a narrow brief, so widening it into one
                                          combined brief would degrade both. Family candidates
                                          are family_fit-scored; adult ones carry adult_fit
                                          (culture) or civic_value (local facts), never both.
                                          Either path failing degrades to [] without losing
                                          the other.
  Stage 2   SKEPTIC (search)           -- ONE batch call over the merged pool, verifying every
                                          candidate's real existence/date/radius. keep |
                                          correct | kill. The hallucination guard -- never
                                          invents, and never judges suitability.
  Anti-repeat filter (signals_seen.json) -- suppresses events/civic items/evergreens still in
                                          cooldown; evergreens fall back to least-recently-
                                          suggested (per audience) so the email is never empty.
  Stage 3   CONCIERGE (no search)      -- writes the warm soft-itinerary email in four
                                          time-first sections: This weekend / During the week /
                                          Looking ahead / Good to know.
  Memory write + email (always sends).

Outputs every run: state/weekend_signals.json, state/weekend_log.md, state/memory.json/.md,
state/signals_seen.json.
"""

import json
import re
import urllib.parse
import datetime as dt
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass
import config as C
import common as X
import llm_chain as L
import memory as M
import scrapers
import weather


def _section(title):
    """Print a section banner so the CI run log reads as clear, scannable stages."""
    print(f"\n{'=' * 66}\n  {title}\n{'=' * 66}")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def event_key(title, date_iso, role):
    """role = 'thisweekend' | 'thisweek' | 'lookahead' | 'civicnotice' -- a look-ahead item can
    re-surface as 'happening now' once its date actually arrives."""
    return f"{slug(title)}|{date_iso or ''}|{role}"


def evergreen_key(name, audience="family"):
    """Family keys are byte-identical to the pre-audience form on purpose: the live
    state/signals_seen.json holds `evergreen|<slug>` entries, and changing the shape would
    reset the running family rotation."""
    prefix = "evergreen|" if audience == "family" else f"evergreen|{audience}|"
    return f"{prefix}{slug(name)}"


def civic_key(title):
    """Durable civic facts are keyed without a date -- a new mall is interesting once, and
    stays true for months, so it gets CIVIC_OPPORTUNITY_COOLDOWN_DAYS rather than the event
    TTL. The trailing pipe matters; see prune_seen."""
    return f"civic|{slug(title)}"


# Category -> anti-repeat role. Deliberately a dict subscript rather than .get(): an
# unmapped category must raise loudly here, because returning None would silently produce
# the key "slug|date|None", which never matches a stored key, so the item would never be
# suppressed and would re-send every single week. That failure is invisible in a test run.
# None is a legitimate value for the two categories that build their keys another way.
_ROLE_BY_CATEGORY = {
    "event_this_weekend": "thisweekend",
    "event_thisweek":     "thisweek",
    "event_lookahead":    "lookahead",
    "civic_notice":       "civicnotice",
    "evergreen":          None,  # keyed by evergreen_key()
    "civic_opportunity":  None,  # keyed by civic_key()
}

EVENT_CATEGORIES = ("event_this_weekend", "event_thisweek", "event_lookahead")
CIVIC_CATEGORIES = ("civic_opportunity", "civic_notice")


def role_for(category):
    return _ROLE_BY_CATEGORY[category]


def score_field(candidate, audience):
    """Every candidate carries exactly ONE score, chosen by its category and audience."""
    if candidate.get("category") in CIVIC_CATEGORIES:
        return "civic_value"
    return "adult_fit" if audience == "adult" else "family_fit"


def score_of(candidate, audience):
    """Ranking signal. Adult and civic candidates carry no family_fit at all, so ranking by
    family_fit (as this pipeline used to) would score every one of them 0 and order them
    arbitrarily -- the email would still hold the right items, with the concierge's
    prioritisation signal silently destroyed. A missing score is 0, which fails every floor."""
    try:
        return int(candidate.get(score_field(candidate, audience)) or 0)
    except (TypeError, ValueError):
        return 0


def floor_for(candidate, audience):
    """The score floor this candidate must clear, or None if exempt.

    Evergreens are exempt, and must stay exempt: they are the guaranteed fallback that keeps
    the email from ever being empty. Applying a floor to them would look correct in testing,
    since most evergreens score well above every threshold."""
    category = candidate.get("category")
    if category == "evergreen":
        return None
    if category == "civic_opportunity":
        return C.MIN_CIVIC_OPPORTUNITY_SCORE
    if category == "civic_notice":
        return C.MIN_CIVIC_NOTICE_SCORE
    return C.MIN_ADULT_SCORE if audience == "adult" else C.MIN_INCLUDE_SCORE


def load_feedback():
    try:
        with open("preferences.md", encoding="utf-8") as f:
            text = f.read().strip()
        return text or "(no preferences recorded yet)"
    except FileNotFoundError:
        return "(no preferences recorded yet)"


def load_taste():
    """The adult equivalent of load_feedback() -- hand-edited scored exemplars that calibrate
    adult_fit. Injected verbatim into the adult FIND prompt and into CONCIERGE."""
    try:
        with open("taste.md", encoding="utf-8") as f:
            text = f.read().strip()
        return text or "(no taste calibration recorded yet)"
    except FileNotFoundError:
        return "(no taste calibration recorded yet)"


# --- anti-repeat state ---

def load_seen():
    return X.load_json("signals_seen.json", {"seen": {}, "monthly_count": {}})


def prune_seen(state):
    """Events, evergreens and durable civic facts use three different cooldown lengths, so
    prune each key by the TTL implied by its prefix rather than a single global cutoff.

    The `civic|` test MUST include the pipe. An event titled "Civic Center Opening" slugs to
    `civic-center-opening` and its key begins `civic-center-opening|...`, so a pipe-less prefix
    test would quietly hand that event a 180-day cooldown instead of 21."""
    cutoff_event     = (dt.date.today() - dt.timedelta(days=C.EVENT_TTL_DAYS)).isoformat()
    cutoff_evergreen = (dt.date.today() - dt.timedelta(days=C.EVERGREEN_COOLDOWN_DAYS)).isoformat()
    cutoff_civic     = (dt.date.today() - dt.timedelta(days=C.CIVIC_OPPORTUNITY_COOLDOWN_DAYS)).isoformat()

    def _keep(key, seen_date):
        if key.startswith("evergreen|"):
            cutoff = cutoff_evergreen
        elif key.startswith("civic|"):
            cutoff = cutoff_civic
        else:
            cutoff = cutoff_event
        return seen_date >= cutoff

    state["seen"] = {k: v for k, v in state.get("seen", {}).items() if _keep(k, v)}
    return state


def is_seen(state, key):
    return key in state.get("seen", {})


def mark_seen(state, key):
    state.setdefault("seen", {})[key] = X.today_iso()


# --- selection ---

def select_events(fresh_events, audience):
    """All non-evergreen survivors for one audience, ranked by that audience's score (for the
    concierge's prioritization only), already filtered for anti-repeat cooldown by the caller.
    No count cap — every survivor that passed FIND + SKEPTIC + anti-repeat goes to the
    concierge."""
    return sorted(fresh_events, key=lambda c: score_of(c, audience), reverse=True)


def select_evergreens(evergreen_survivors, mem, audience="family"):
    """All off-cooldown survivor evergreens for one audience, ranked by that audience's score.
    If none are off cooldown (or none survived this run at all), fall back to a single
    least-recently-suggested catalog entry so the email is never empty.

    The fallback filters the catalog by audience, so an adult evergreen can never fill the
    family slot and vice versa."""
    catalog = mem.get("evergreen", {})
    cutoff = (dt.date.today() - dt.timedelta(days=C.EVERGREEN_COOLDOWN_DAYS)).isoformat()

    off_cooldown = []
    for c in evergreen_survivors:
        last = catalog.get(c.get("title", ""), {}).get("last_suggested")
        if not last or last < cutoff:
            off_cooldown.append(c)
    picks = sorted(off_cooldown, key=lambda c: score_of(c, audience), reverse=True)

    if not picks:
        same_audience = [(name, e) for name, e in catalog.items()
                         if e.get("audience", "family") == audience]
        ranked = sorted(same_audience, key=lambda kv: kv[1].get("last_suggested") or "")
        for name, entry in ranked[:1]:
            picks.append({
                "title": name, "category": "evergreen", "when_text": "", "date_iso": None,
                "location": entry.get("location", ""), "audience": audience,
                score_field({"category": "evergreen"}, audience): 60,
                "reason": entry.get("description", ""), "source_url": entry.get("url", ""),
                "practical": entry.get("practical", ""), "confidence": "high",
            })
    return picks


# --- actionable links ---

def build_links(c):
    """Ready-made links so the reader never has to go googling, and so the concierge
    never has to invent a URL. source_url is the real page FIND/a scraper found (may be
    ""); maps_url and search_url are constructed deterministically and always resolve.
    Returns (source_url, maps_url, search_url) — any may be "" if unbuildable."""
    title = (c.get("title") or "").strip()
    location = (c.get("location") or "").strip()
    maps_q = location or title
    search_q = " ".join(p for p in (title, location) if p)
    maps_url = ("https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(maps_q)
                if maps_q else "")
    search_url = ("https://www.google.com/search?q=" + urllib.parse.quote_plus(search_q)
                  if search_q else "")
    return (c.get("source_url") or "").strip(), maps_url, search_url


# --- weather formatting ---

def format_weather(days):
    """Render the ordered per-day forecast dicts from weather.week_weather() into a plain-text
    block for the LLM prompts and the run log -- actual numbers, not a pre-classified label,
    so the model reasons about the forecast itself.

    The keys read here must match week_weather()'s exactly. A renamed field would print '?'
    into a live prompt and the model would invent plausible weather to fill the gap, which is
    why test_concierge asserts a fully-populated day renders with no '?' in it."""
    if not days:
        return "forecast unavailable"
    lines = []
    for w in days:
        lines.append(
            f"{w.get('label', '?')} ({w.get('date', '?')}): {w.get('condition', '?')}, "
            f"{w.get('min_temp_c', '?')}-{w.get('max_temp_c', '?')}°C "
            f"(feels {w.get('feels_like_min_c', '?')}-{w.get('feels_like_max_c', '?')}°C), "
            f"{w.get('humidity_pct', '?')}% humidity, {w.get('cloud_cover_pct', '?')}% cloud cover, "
            f"{w.get('rain_chance_pct', '?')}% chance of rain"
        )
    return "\n".join(lines)


def degraded_summary(stage_results):
    """(subject_prefix, banner_html, banner_text, log_line) for a run where any LLM stage
    failed outright. A stage that merely fell back to a later model is the chain WORKING --
    it gets a log line only, never a banner, or the flag stops meaning anything."""
    failed = [name for name, r in stage_results if not r.ok]
    fell   = [name for name, r in stage_results if r.ok and r.fell_back]
    log = ""
    if failed or fell:
        log = (f"- LLM: {len(failed)} stage(s) failed ({', '.join(failed) or 'none'}); "
               f"{len(fell)} fell back to a later model ({', '.join(fell) or 'none'})")
    if not failed:
        return "", "", "", log
    stages = ", ".join(failed)
    banner_html = ('<p><b>Heads up:</b> this email is incomplete. The following stage(s) could not be '
                   f'reached after retrying every configured model: {stages}. Sections below may be '
                   'thin or missing, and some items were sent without verification.</p>')
    banner_text = ("Heads up: this email is incomplete. The following stage(s) could not be reached "
                   f"after retrying every configured model: {stages}. Sections below may be thin or "
                   "missing, and some items were sent without verification.\n")
    return "[degraded] ", banner_html, banner_text, log


# --- fallback email (used only if CONCIERGE fails or returns incomplete output) ---

def _fallback_email(sections, today):
    """sections: list of (label, items) in the same four-section order the concierge is asked
    to write, so a fallback email reads like a plainer version of the real one."""
    html = [f"<h2>Weekend Concierge — {today}</h2>"]
    text = [f"Weekend Concierge — {today}"]
    for label, items in sections:
        if not items:
            continue
        html.append(f"<h3>{label}</h3><ul>")
        text.append(f"\n{label}:")
        for c in items:
            when = c.get("when_text") or c.get("date_iso") or ""
            source_url, maps_url, search_url = build_links(c)
            info_url = source_url or search_url
            link_html, link_text = [], []
            if info_url:
                link_html.append(f'<a href="{info_url}">{"details" if source_url else "look it up"}</a>')
                link_text.append(info_url)
            if maps_url:
                link_html.append(f'<a href="{maps_url}">map</a>')
                link_text.append(f"map: {maps_url}")
            links_h = (" — " + " · ".join(link_html)) if link_html else ""
            links_t = ("  [" + " | ".join(link_text) + "]") if link_text else ""
            html.append(f"<li><b>{c.get('title','?')}</b> ({when}, {c.get('location','')}) "
                        f"— {c.get('reason','')}{links_h}</li>")
            text.append(f"- {c.get('title','?')} ({when}, {c.get('location','')}) "
                        f"— {c.get('reason','')}{links_t}")
        html.append("</ul>")
    return "".join(html), "\n".join(text)


# --- markdown log ---

def write_log(today, candidates, sent_pools, weather_text, subject, llm_note=""):
    """sent_pools: list of (pool_name, items) — the five candidate pools, so every item sent is
    attributable to a pool, and every drop is attributable to a score against a named floor.
    That is what the owner tunes the floors from."""
    total_sent = sum(len(items) for _, items in sent_pools)
    lines = [f"# Weekend Concierge — {today}", "", f"**Subject:** {subject}", "", "**Weather:**", ""]
    # One bullet per day rather than a single joined line -- at 7 days a joined line runs to
    # ~770 characters, which is unreadable in a markdown file.
    lines += [f"- {day}" for day in weather_text.splitlines()]
    lines.append("")
    lines.append(f"_{len(candidates)} candidate(s) considered · {total_sent} sent._")
    if llm_note:
        lines.append(llm_note)
    lines.append("")
    lines.append("## Sent this run")
    for pool_name, items in sent_pools:
        if not items:
            continue
        lines.append(f"### {pool_name} ({len(items)})")
        for c in items:
            when = c.get("when_text") or c.get("date_iso") or ""
            lines.append(f"- **{c.get('title','?')}** ({c.get('category','?')}, {when}, "
                         f"{c.get('location','')}) — {c.get('reason','')}")
    lines.append("")
    lines.append("## All candidates")
    lines.append("_Sorted by score. `fit=` names the field each candidate was actually judged on, "
                 "with the floor that rejected it where one did._")
    ranked = sorted(candidates,
                    key=lambda x: score_of(x, x.get("audience", "family")), reverse=True)
    for c in ranked:
        audience = c.get("audience", "family")
        field = score_field(c, audience)
        floor = floor_for(c, audience)
        floor_text = "exempt" if floor is None else f"floor {floor}"
        lines.append(f"- #{c.get('candidate_id','?')} [{c.get('verdict','?')}] {c.get('title','?')} "
                     f"({audience}/{c.get('category','?')}, {field}={c.get(field,'?')}, "
                     f"{floor_text}) — {c.get('note','')}")
    with open("state/weekend_log.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# --- anti-repeat keying ---

def filter_seen(pool, seen_state):
    """Key each candidate by its category and drop the ones still in cooldown. Durable civic
    facts key without a date and sit out CIVIC_OPPORTUNITY_COOLDOWN_DAYS; everything else here
    keys on slug|date|role for EVENT_TTL_DAYS."""
    fresh = []
    for c in pool:
        category = c.get("category")
        if category == "civic_opportunity":
            key, cooldown = civic_key(c.get("title", "")), C.CIVIC_OPPORTUNITY_COOLDOWN_DAYS
        else:
            key = event_key(c.get("title", ""), c.get("date_iso"), role_for(category))
            cooldown = C.EVENT_TTL_DAYS
        c["_key"] = key
        if is_seen(seen_state, key):
            c["verdict"] = "suppressed"
            print(f"    [SUPPRESS] {c.get('title', '?')} — seen within {cooldown}d cooldown")
            continue
        fresh.append(c)
    return fresh


# --- stage 1 ---

def _run_find(label, prompt_text, search_text, schema, stage_results):
    """One Stage-1 FIND call. Each path fails independently to []: one dead path must never
    cost us the other, mirroring the per-source resilience in scrapers.harvest()."""
    try:
        res = L.call_llm(prompt_text, stage=f"find-{label}", max_tokens=C.MAX_TOKENS_FIND,
                         want_search=True, search_prompt=search_text,
                         search_preamble=C.SEARCH_RESULTS_PREAMBLE, response_schema=schema,
                         provider=C.PROVIDER_FIND, web_search_max_uses=C.WEB_SEARCH_MAX_USES)
        stage_results.append((f"find-{label}", res))
        if not res.ok:
            print(f"  [FAIL] Stage 1 ({label}) unavailable: {res.error} — treating as 0 candidates")
            return []
        found = (X.parse_json_block(res.text) or {}).get("candidates", [])
    except Exception as e:
        print(f"  [FAIL] Stage 1 ({label}) LLM/parse error: {type(e).__name__}: {e} "
              f"— treating as 0 candidates")
        return []
    return [c for c in found if isinstance(c, dict)]


# --- main ---

def main():
    today_iso = X.today_iso()
    today = dt.date.today()
    stage_results = []
    _section(f"WEEKEND CONCIERGE · {today_iso} · provider={L.resolved_provider()}")
    print(f"  llm config: {L.resolved_config()}")

    # Memory + feedback, loaded once and injected into every stage prompt. Seed the
    # evergreen catalog with SEED_EVERGREEN on first run only (existing entries win).
    mem = M.load()
    # The `name not in catalog` guard is what stops an adult seed overwriting an existing
    # family entry's audience (and vice versa) on any run after the first.
    for seed, audience in ([(s, "family") for s in C.SEED_EVERGREEN]
                           + [(s, "adult") for s in C.SEED_EVERGREEN_ADULT]):
        if seed["name"] not in mem["evergreen"]:
            M.record_evergreen(mem, seed["name"], location=seed.get("location", ""),
                                area=seed.get("area", ""), description=seed.get("description", ""),
                                tags=seed.get("tags"), url=seed.get("url", ""),
                                practical=seed.get("practical", ""), source=seed.get("source", "seed"),
                                audience=audience)
    mem_text_family = M.summarize_for_prompt(mem, "family")
    mem_text_adult  = M.summarize_for_prompt(mem, "adult")
    # SKEPTIC and CONCIERGE span both audiences. They get the family summary: the recent-
    # suggestions half is audience-independent and identical in both, and only the evergreen-
    # catalog half differs — neither shared stage needs the adult catalog, since every adult
    # item it handles is already in front of it as a candidate.
    mem_text_shared = mem_text_family
    feedback = load_feedback()
    taste = load_taste()
    print(f"  memory: {len(mem['evergreen'])} evergreen(s), {len(mem['ledger'])} ledger entry(s) loaded")

    # "Happening soon" window: tomorrow through run-day + WEEK_WINDOW_DAYS, inclusive. On the
    # Friday cron that is Sat..next Fri, so a Thursday festival six days out is caught with no
    # cron change. Both FIND paths share it.
    window_start = (today + dt.timedelta(days=1)).isoformat()
    window_end   = (today + dt.timedelta(days=C.WEEK_WINDOW_DAYS)).isoformat()
    print(f"  window: {window_start} .. {window_end} ({C.WEEK_WINDOW_DAYS}d)")

    _section("WEATHER")
    week_days = weather.week_weather(C.PLOVDIV_LATLON, today)
    weather_text = format_weather(week_days)
    print(f"  {weather_text.replace(chr(10), ' · ')}")

    _section("HARVEST")
    harvest_items = scrapers.harvest(today_iso)
    harvest_text = "\n\n".join(
        f"[{i['source']}] {i['title']} ({i.get('when_text', '')}, {i.get('location', '')}): "
        f"{i.get('description', '')[:800]}"
        for i in harvest_items
    ) or "(no harvested material this run)"
    print(f"  {len(harvest_items)} harvested item(s)")

    # Stage 1: FIND -- two independent calls. The family brief is narrow on purpose (that
    # narrowness is where its quality comes from), so the adult brief is a separate call
    # rather than a widening of it.
    _section("STAGE 1 · FIND")
    find_directive = (C.SEARCH_DIRECTIVE_ANTHROPIC
                      if L.resolved_provider(C.PROVIDER_FIND) == "anthropic" else "")
    window = {"window_start": window_start, "window_end": window_end}

    family_found = _run_find(
        "family",
        C.FIND_FAMILY_PROMPT.format(
            today=today_iso, home_area=C.HOME_AREA, radius_minutes=C.RADIUS_MINUTES,
            lookahead_weeks=C.LOOKAHEAD_WEEKS, harvest=harvest_text, memory=mem_text_family,
            feedback=feedback, search_directive=find_directive, **window),
        C.SEARCH_FAMILY_PROMPT.format(today=today_iso, home_area=C.HOME_AREA,
                                      radius_minutes=C.RADIUS_MINUTES,
                                      lookahead_weeks=C.LOOKAHEAD_WEEKS),
        C.STAGE1_FAMILY_SCHEMA,
        stage_results,
    )
    for c in family_found:
        c["audience"] = "family"

    adult_found = _run_find(
        "adult",
        C.FIND_ADULT_PROMPT.format(
            today=today_iso, home_area=C.HOME_AREA, radius_minutes=C.RADIUS_MINUTES,
            lookahead_weeks=C.LOOKAHEAD_WEEKS, harvest=harvest_text, memory=mem_text_adult,
            feedback=feedback, taste=taste, search_directive=find_directive, **window),
        C.SEARCH_ADULT_PROMPT.format(today=today_iso, home_area=C.HOME_AREA,
                                     radius_minutes=C.RADIUS_MINUTES,
                                     lookahead_weeks=C.LOOKAHEAD_WEEKS),
        C.STAGE1_ADULT_SCHEMA,
        stage_results,
    )
    for c in adult_found:
        c["audience"] = "adult"

    # candidate_id is assigned AFTER the merge so ids are unique across the combined pool --
    # SKEPTIC sees one batch and correlates its verdicts by id.
    candidates = family_found + adult_found
    for i, c in enumerate(candidates, 1):
        c["candidate_id"] = i

    print(f"  family path: {len(family_found)} · adult path: {len(adult_found)}")
    if not candidates:
        print("  0 candidates returned")
    else:
        print(f"  {len(candidates)} candidate(s) returned:")
        for c in candidates:
            audience = c.get("audience", "family")
            field = score_field(c, audience)
            print(f"    #{c['candidate_id']} [{audience}/{c.get('category', '?')}] "
                  f"{c.get('title', '?')} ({field}={c.get(field, '?')}, "
                  f"lang={c.get('language_barrier', '-')}, conf={c.get('confidence', '?')})")

    # Stage 2: SKEPTIC -- one batch verification call, the hallucination guard.
    _section("STAGE 2 · SKEPTIC")
    verdicts_by_id = {}
    skeptic_available = False
    if candidates:
        try:
            res2 = L.call_llm(
                C.SKEPTIC_PROMPT.format(
                    today=today_iso, home_area=C.HOME_AREA, radius_minutes=C.RADIUS_MINUTES,
                    candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
                    memory=mem_text_shared,
                ),
                stage="skeptic", max_tokens=C.MAX_TOKENS_SKEPTIC, want_search=True,
                search_prompt=None, search_preamble=C.SEARCH_RESULTS_PREAMBLE,
                response_schema=C.STAGE2_RESPONSE_SCHEMA, provider=C.PROVIDER_SKEPTIC,
            )
            stage_results.append(("skeptic", res2))
            skeptic_available = res2.ok
            verdicts = X.parse_json_block(res2.text) or []
        except Exception as e:
            print(f"  [FAIL] Stage 2 LLM/parse error: {type(e).__name__}: {e}")
            verdicts = []
        if not isinstance(verdicts, list):
            verdicts = []
        for v in verdicts:
            if isinstance(v, dict) and v.get("candidate_id") is not None:
                verdicts_by_id[v["candidate_id"]] = v
    else:
        print("  nothing to verify")

    survivors = []
    for c in candidates:
        verified = True
        v = verdicts_by_id.get(c["candidate_id"])
        is_evergreen = c.get("category") == "evergreen"
        if v is not None:
            verdict = v.get("verdict", "keep")
            note = v.get("note", "")
            if verdict == "correct":
                if v.get("corrected_date_iso"):
                    c["date_iso"] = v["corrected_date_iso"]
                if v.get("corrected_location"):
                    c["location"] = v["corrected_location"]
        elif is_evergreen:
            # Evergreens are known-real by construction (maintained catalog) -- no
            # existence check needed even if SKEPTIC didn't return a verdict for it.
            verdict, note = "keep", "evergreen — known-real, no skeptic verdict needed"
        elif verdicts_by_id:
            # SKEPTIC ran and returned verdicts, just not for this candidate_id --
            # lean toward keep (only a positive reason to believe it's fake should kill it).
            verdict, note = "keep", "no skeptic verdict matched — kept by default"
        else:
            # No verdicts at all -- two very different causes, and conflating them is
            # Invariant SKEPTIC-INFRA-IS-NOT-REJECTION.
            if skeptic_available:
                # SKEPTIC ran and returned nothing usable: treat as a real rejection.
                verdict, note = "kill", "skeptic verification failed — dropping unverified event"
            else:
                # SKEPTIC never executed (every model in the chain failed). Killing here
                # would let one provider outage empty the whole email, which is exactly
                # what happened on 2026-08-14. Keep the item, flag it UNVERIFIED, and let
                # the degraded-run banner tell the reader why.
                verdict = "keep"
                # ASCII on purpose (the plan's draft used an em dash here). `note` is
                # persisted by common.save_json(), which writes ensure_ascii=False through
                # an open() with no encoding= -- so a non-ASCII note written on Windows
                # lands as cp1252, and CI then reads it back as utf-8 and crashes. Same
                # reason as the language-gate branch below.
                note = "SKEPTIC unavailable (provider failure) -- sent without verification"
                verified = False

        c["verdict"], c["note"], c["verified"] = verdict, note, verified
        if verdict == "kill":
            print(f"    [KILL    ] #{c['candidate_id']} {c.get('title', '?')} — {note}")
            continue
        # Language gate, applied BEFORE the floor: a blocking barrier is structural, not a
        # matter of degree, so no score can rescue it. "partial" deliberately carries NO
        # numeric penalty here -- the adult FIND prompt already scores partial items lower,
        # and subtracting points in Python as well would double-count the same fact.
        if c.get("language_barrier") == "blocking":
            c["verdict"] = "skipped"
            # Say so in the note too: this is the one drop the log can't otherwise explain,
            # since the score and floor columns will show a passing score (a blocking item is
            # often high-scoring) and read as though it should have been sent.
            # Keep this ASCII. `note` is serialised by common.save_json(), which passes
            # ensure_ascii=False but opens the file without encoding="utf-8" -- so on a
            # non-UTF-8 locale (Windows) a non-ASCII note writes cp1252 and the next read
            # fails. common.py is copied from deal-hunter and not ours to fix here.
            c["note"] = f"dropped: language_barrier=blocking; {note}"
            print(f"    [LANG-BLOCK] #{c['candidate_id']} {c.get('title', '?')} — "
                  f"unattendable without Bulgarian")
            continue
        audience = c.get("audience", "family")
        floor = floor_for(c, audience)
        if floor is not None and score_of(c, audience) < floor:
            c["verdict"] = "skipped"
            print(f"    [LOW-FIT ] #{c['candidate_id']} {c.get('title', '?')} — "
                  f"{score_field(c, audience)} {score_of(c, audience)} < {floor}")
            continue
        print(f"    [{verdict.upper():<9}] #{c['candidate_id']} {c.get('title', '?')} — {note}")
        survivors.append(c)
    print(f"  -> {len(survivors)} survivor(s)")

    # Anti-repeat filter + selection.
    _section("ANTI-REPEAT FILTER")
    seen_state = load_seen()
    seen_state = prune_seen(seen_state)

    # Five pools. Every downstream consumer -- selection, the log, the concierge payload and
    # the fallback email -- is driven from exactly these, so nothing can silently fall between
    # two of them.
    def _pool(audience, categories):
        return [c for c in survivors
                if c.get("audience", "family") == audience and c.get("category") in categories]

    family_events     = _pool("family", EVENT_CATEGORIES)
    family_evergreens = _pool("family", ("evergreen",))
    adult_events      = _pool("adult", EVENT_CATEGORIES)
    adult_evergreens  = _pool("adult", ("evergreen",))
    civic_items       = [c for c in survivors if c.get("category") in CIVIC_CATEGORIES]

    selected_family_events = select_events(filter_seen(family_events, seen_state), "family")
    selected_adult_events  = select_events(filter_seen(adult_events, seen_state), "adult")
    # Civic items need no selector -- they are already floor-filtered and cooldown-filtered,
    # and pass straight through. Ranked only so the concierge sees the outage before the mall.
    selected_civic = sorted(filter_seen(civic_items, seen_state),
                            key=lambda c: score_of(c, "adult"), reverse=True)

    selected_family_evergreens = select_evergreens(family_evergreens, mem, "family")
    selected_adult_evergreens  = select_evergreens(adult_evergreens, mem, "adult")
    for c in selected_family_evergreens:
        c.setdefault("audience", "family")
        c.setdefault("_key", evergreen_key(c["title"], "family"))
    for c in selected_adult_evergreens:
        c.setdefault("audience", "adult")
        c.setdefault("_key", evergreen_key(c["title"], "adult"))

    selected_events     = selected_family_events + selected_adult_events
    selected_evergreens = selected_family_evergreens + selected_adult_evergreens
    for c in selected_events + selected_civic + selected_evergreens:
        c["verdict"] = "sent"
        mark_seen(seen_state, c["_key"])

    print(f"  -> {len(selected_family_events)} family event(s), {len(selected_adult_events)} adult "
          f"event(s), {len(selected_civic)} civic item(s), {len(selected_family_evergreens)} family "
          f"+ {len(selected_adult_evergreens)} adult evergreen(s) selected")

    # Stage 3: CONCIERGE -- write the email.
    _section("STAGE 3 · CONCIERGE")
    concierge_candidates = []
    for c in selected_events + selected_civic + selected_evergreens:
        source_url, maps_url, search_url = build_links(c)
        payload = {
            "title": c.get("title"), "category": c.get("category"),
            "audience": c.get("audience", "family"), "when_text": c.get("when_text"),
            "date_iso": c.get("date_iso"), "location": c.get("location"), "reason": c.get("reason"),
            "practical": c.get("practical", ""),
            "source_url": source_url, "maps_url": maps_url, "search_url": search_url,
        }
        # Exactly one score per candidate, under its own name -- the prompt is told all three
        # are internal-only ranking signals and must never reach the copy.
        for field in ("family_fit", "adult_fit", "civic_value"):
            if c.get(field) is not None:
                payload[field] = c.get(field)
        concierge_candidates.append(payload)

    subject, html, text = None, "", ""
    try:
        res3 = L.call_llm(
            C.CONCIERGE_PROMPT.format(
                today=today_iso, home_area=C.HOME_AREA,
                candidates=json.dumps(concierge_candidates, ensure_ascii=False, indent=2),
                weather=weather_text, feedback=feedback, memory=mem_text_shared, taste=taste,
            ),
            stage="concierge", max_tokens=C.MAX_TOKENS_CONCIERGE, want_search=False,
            response_schema=C.CONCIERGE_RESPONSE_SCHEMA, provider=C.PROVIDER_CONCIERGE,
        )
        stage_results.append(("concierge", res3))
        out = X.parse_json_block(res3.text) or {}
        subject, html, text = out.get("subject"), out.get("html", ""), out.get("text", "")
    except Exception as e:
        print(f"  [FAIL] Stage 3 LLM/parse error: {type(e).__name__}: {e} — using fallback email")

    # The four time-first email sections, in the order the concierge is asked to write them.
    # Reused for the fallback email so it reads like a plainer version of the real one.
    def _when(*categories):
        return [c for c in selected_events if c.get("category") in categories]

    sections = [
        ("This weekend",    _when("event_this_weekend")),
        ("During the week", _when("event_thisweek")),
        ("Looking ahead",   _when("event_lookahead")),
        ("Good to know",    selected_civic + selected_evergreens),
    ]

    if not html or not text:
        print("  concierge output incomplete — building a plain fallback email")
        fallback_html, fallback_text = _fallback_email(sections, today_iso)
        html = html or fallback_html
        text = text or fallback_text
    subject = subject or f"Weekend Concierge — {today_iso}"

    degraded_prefix, banner_html, banner_text, llm_log_line = degraded_summary(stage_results)
    html = banner_html + html
    text = banner_text + text
    subject = degraded_prefix + subject

    # Memory write: ledger entry per candidate that reached Stage 2; evergreen catalog
    # grows with anything new SKEPTIC confirmed, and included evergreens get last_suggested
    # bumped so the cooldown rotation actually rotates.
    _section("MEMORY + OUTPUTS")
    for c in family_events + adult_events + civic_items:
        M.record_suggestion(mem, c.get("title", ""), c.get("category", ""),
                            c.get("when_text") or c.get("date_iso") or "",
                            location=c.get("location", ""), url=c.get("source_url", ""),
                            score=score_of(c, c.get("audience", "family")),
                            verdict=c.get("verdict", "skipped"), note=c.get("note", ""))
    for c, audience in ([(c, "family") for c in family_evergreens]
                        + [(c, "adult") for c in adult_evergreens]):
        if c.get("title") not in mem["evergreen"]:
            M.record_evergreen(mem, c["title"], location=c.get("location", ""),
                               description=c.get("reason", ""), source="find", audience=audience)
    for c, audience in ([(c, "family") for c in selected_family_evergreens]
                        + [(c, "adult") for c in selected_adult_evergreens]):
        M.record_evergreen(mem, c["title"], location=c.get("location", ""),
                           description=c.get("reason", ""), suggested=True, audience=audience)
        M.record_suggestion(mem, c["title"], "evergreen", c.get("when_text", ""),
                            location=c.get("location", ""), score=score_of(c, audience),
                            verdict="sent", note=c.get("reason", ""))

    M.prune(mem)
    M.save(mem)
    print(f"  memory written: {len(mem['evergreen'])} evergreen(s), {len(mem['ledger'])} ledger entry(s)")

    signals = [{
        "candidate_id": c.get("candidate_id"), "title": c.get("title"), "category": c.get("category"),
        "audience": c.get("audience", "family"),
        "when_text": c.get("when_text"), "date_iso": c.get("date_iso"), "location": c.get("location"),
        "family_fit": c.get("family_fit"), "adult_fit": c.get("adult_fit"),
        "civic_value": c.get("civic_value"), "language_barrier": c.get("language_barrier"),
        "reason": c.get("reason"), "source_url": c.get("source_url"),
        "confidence": c.get("confidence"), "verdict": c.get("verdict"), "note": c.get("note"),
        "verified": c.get("verified", True),
    } for c in candidates]
    X.save_json("weekend_signals.json", {"generated": today_iso, "signals": signals})
    write_log(today_iso, candidates, [
        ("Family events", selected_family_events),
        ("Adult events", selected_adult_events),
        ("Good to know — civic", selected_civic),
        ("Family evergreens", selected_family_evergreens),
        ("Adult evergreens", selected_adult_evergreens),
    ], weather_text, subject, llm_note=llm_log_line)
    print("  wrote state/weekend_signals.json, state/weekend_log.md")

    # Email -- always sends (weekly ritual; evergreen guarantees non-empty content).
    # Anti-repeat marks are already applied above regardless of send outcome, so a
    # transient SMTP failure doesn't leave the run in a half-updated state.
    _section("EMAIL")
    try:
        X.send_email(subject, html, text)
        print(f"  [EMAIL SENT] {subject}")
    except Exception as e:
        print(f"  [FAIL] email send error: {type(e).__name__}: {e}")

    X.save_json("signals_seen.json", seen_state)

    _section("RUN COMPLETE")
    print(f"  {len(candidates)} found -> {len(survivors)} survived skeptic -> "
          f"{len(selected_events)} event(s) + {len(selected_civic)} civic + "
          f"{len(selected_evergreens)} evergreen(s) sent")


if __name__ == "__main__":
    main()
