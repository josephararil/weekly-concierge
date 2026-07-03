"""
weekend_concierge.py — Weekend Concierge (weekly; HARVEST -> FIND -> SKEPTIC -> anti-repeat
-> CONCIERGE -> email)

Pipeline (see PLAN.md for the full diagram):
  HARVEST   (scrapers.py, no LLM)      -- per-source try/except -> [], never crashes the run.
  Stage 1   FIND (search + reasoning)  -- consolidates harvest + search leads + own knowledge
                                          into structured candidates (event_this_weekend |
                                          event_lookahead | evergreen), each family_fit-scored.
  Stage 2   SKEPTIC (search)           -- ONE batch call verifying every candidate's real
                                          existence/date/family-relevance/radius. keep | correct
                                          | kill. The hallucination guard -- never invents.
  Anti-repeat filter (signals_seen.json) -- suppresses events/evergreens still in cooldown;
                                          evergreens fall back to least-recently-suggested so
                                          the email is never empty.
  Stage 3   CONCIERGE (no search)      -- writes the warm soft-itinerary email.
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
import memory as M
import scrapers
import weather


def _section(title):
    """Print a section banner so the CI run log reads as clear, scannable stages."""
    print(f"\n{'=' * 66}\n  {title}\n{'=' * 66}")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def event_key(title, date_iso, role):
    """role = 'thisweekend' | 'lookahead' -- a look-ahead item can re-surface as
    'happening now' once its weekend actually arrives."""
    return f"{slug(title)}|{date_iso or ''}|{role}"


def evergreen_key(name):
    return f"evergreen|{slug(name)}"


def load_feedback():
    try:
        with open("preferences.md", encoding="utf-8") as f:
            text = f.read().strip()
        return text or "(no preferences recorded yet)"
    except FileNotFoundError:
        return "(no preferences recorded yet)"


# --- anti-repeat state ---

def load_seen():
    return X.load_json("signals_seen.json", {"seen": {}, "monthly_count": {}})


def prune_seen(state):
    """Events and evergreens use different cooldown lengths, so prune each key by the
    TTL implied by its prefix rather than a single global cutoff."""
    cutoff_event     = (dt.date.today() - dt.timedelta(days=C.EVENT_TTL_DAYS)).isoformat()
    cutoff_evergreen = (dt.date.today() - dt.timedelta(days=C.EVERGREEN_COOLDOWN_DAYS)).isoformat()

    def _keep(key, seen_date):
        cutoff = cutoff_evergreen if key.startswith("evergreen|") else cutoff_event
        return seen_date >= cutoff

    state["seen"] = {k: v for k, v in state.get("seen", {}).items() if _keep(k, v)}
    return state


def is_seen(state, key):
    return key in state.get("seen", {})


def mark_seen(state, key):
    state.setdefault("seen", {})[key] = X.today_iso()


# --- selection ---

def select_events(fresh_events):
    """All non-evergreen survivors, ranked by family_fit (for the concierge's prioritization
    only), already filtered for anti-repeat cooldown by the caller. No count cap — every
    survivor that passed FIND + SKEPTIC + anti-repeat goes to the concierge."""
    return sorted(fresh_events, key=lambda c: c.get("family_fit", 0), reverse=True)


def select_evergreens(evergreen_survivors, mem):
    """All off-cooldown survivor evergreens, ranked by family_fit. If none are off cooldown
    (or none survived this run at all), fall back to a single least-recently-suggested
    catalog entry so the email is never empty."""
    catalog = mem.get("evergreen", {})
    cutoff = (dt.date.today() - dt.timedelta(days=C.EVERGREEN_COOLDOWN_DAYS)).isoformat()

    off_cooldown = []
    for c in evergreen_survivors:
        last = catalog.get(c.get("title", ""), {}).get("last_suggested")
        if not last or last < cutoff:
            off_cooldown.append(c)
    picks = sorted(off_cooldown, key=lambda c: c.get("family_fit", 0), reverse=True)

    if not picks:
        ranked = sorted(catalog.items(), key=lambda kv: kv[1].get("last_suggested") or "")
        for name, entry in ranked[:1]:
            picks.append({
                "title": name, "category": "evergreen", "when_text": "", "date_iso": None,
                "location": entry.get("location", ""), "family_fit": 60,
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

def format_weather(weekend_weather):
    """Render the raw per-day forecast dicts from weather.weekend_weather() into a plain-text
    block for the LLM prompts and the run log -- actual numbers, not a pre-classified label,
    so the model reasons about the forecast itself."""
    lines = []
    for day in ("Sat", "Sun"):
        w = weekend_weather.get(day) or {}
        if not w:
            lines.append(f"{day}: forecast unavailable")
            continue
        lines.append(
            f"{day} ({w.get('date', '?')}): {w.get('condition', '?')}, "
            f"{w.get('min_temp_c', '?')}-{w.get('max_temp_c', '?')}°C "
            f"(feels {w.get('feels_like_min_c', '?')}-{w.get('feels_like_max_c', '?')}°C), "
            f"{w.get('humidity_pct', '?')}% humidity, {w.get('cloud_cover_pct', '?')}% cloud cover, "
            f"{w.get('rain_chance_pct', '?')}% chance of rain"
        )
    return "\n".join(lines)


# --- fallback email (used only if CONCIERGE fails or returns incomplete output) ---

def _fallback_email(events, evergreens, today):
    html = [f"<h2>Weekend Concierge — {today}</h2>"]
    text = [f"Weekend Concierge — {today}"]
    for label, items in (("This weekend & ahead", events), ("Also worth knowing", evergreens)):
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

def write_log(today, candidates, selected_events, selected_evergreens, weather_text, subject):
    lines = [f"# Weekend Concierge — {today}", "", f"**Subject:** {subject}", "",
             f"**Weather:** {weather_text.replace(chr(10), ' · ')}", ""]
    lines.append(f"_{len(candidates)} candidate(s) considered · {len(selected_events)} event(s) + "
                 f"{len(selected_evergreens)} evergreen(s) sent._")
    lines.append("")
    lines.append("## Sent this run")
    for c in selected_events + selected_evergreens:
        when = c.get("when_text") or c.get("date_iso") or ""
        lines.append(f"- **{c.get('title','?')}** ({c.get('category','?')}, {when}, "
                     f"{c.get('location','')}) — {c.get('reason','')}")
    lines.append("")
    lines.append("## All candidates")
    for c in sorted(candidates, key=lambda x: x.get("family_fit", 0), reverse=True):
        lines.append(f"- #{c.get('candidate_id','?')} [{c.get('verdict','?')}] {c.get('title','?')} "
                     f"({c.get('category','?')}, fit={c.get('family_fit','?')}) — {c.get('note','')}")
    with open("state/weekend_log.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# --- main ---

def main():
    today_iso = X.today_iso()
    today = dt.date.today()
    _section(f"WEEKEND CONCIERGE · {today_iso} · provider={X.PROVIDER}")
    print(f"  models: find={C.MODEL_FIND} · skeptic={C.MODEL_SKEPTIC} · concierge={C.MODEL_CONCIERGE}")

    # Memory + feedback, loaded once and injected into every stage prompt. Seed the
    # evergreen catalog with SEED_EVERGREEN on first run only (existing entries win).
    mem = M.load()
    for seed in C.SEED_EVERGREEN:
        if seed["name"] not in mem["evergreen"]:
            M.record_evergreen(mem, seed["name"], location=seed.get("location", ""),
                                area=seed.get("area", ""), description=seed.get("description", ""),
                                tags=seed.get("tags"), url=seed.get("url", ""),
                                practical=seed.get("practical", ""), source=seed.get("source", "seed"))
    mem_text = M.summarize_for_prompt(mem)
    feedback = load_feedback()
    print(f"  memory: {len(mem['evergreen'])} evergreen(s), {len(mem['ledger'])} ledger entry(s) loaded")

    _section("WEATHER")
    weekend_weather = weather.weekend_weather(C.PLOVDIV_LATLON, today)
    weather_text = format_weather(weekend_weather)
    print(f"  {weather_text.replace(chr(10), ' · ')}")

    _section("HARVEST")
    harvest_items = scrapers.harvest(today_iso)
    harvest_text = "\n\n".join(
        f"[{i['source']}] {i['title']} ({i.get('when_text', '')}, {i.get('location', '')}): "
        f"{i.get('description', '')[:800]}"
        for i in harvest_items
    ) or "(no harvested material this run)"
    print(f"  {len(harvest_items)} harvested item(s)")

    # Stage 1: FIND -- consolidate harvest + search leads + own knowledge into candidates.
    _section("STAGE 1 · FIND")
    try:
        find_directive = (C.SEARCH_DIRECTIVE_ANTHROPIC
                          if X.resolved_provider(C.PROVIDER_FIND) == "anthropic" else "")
        raw1 = X.llm(
            messages=[{"role": "user", "content": C.FIND_PROMPT.format(
                today=today_iso, home_area=C.HOME_AREA, radius_minutes=C.RADIUS_MINUTES,
                lookahead_weeks=C.LOOKAHEAD_WEEKS, harvest=harvest_text, memory=mem_text,
                feedback=feedback, search_directive=find_directive,
            )}],
            model=C.MODEL_FIND, max_tokens=C.MAX_TOKENS_FIND, want_search=True,
            response_schema=C.STAGE1_RESPONSE_SCHEMA, provider=C.PROVIDER_FIND,
            search_prompt=C.SEARCH_PROMPT.format(today=today_iso, home_area=C.HOME_AREA,
                                                  radius_minutes=C.RADIUS_MINUTES,
                                                  lookahead_weeks=C.LOOKAHEAD_WEEKS),
        )
        candidates = (X.parse_json_block(raw1) or {}).get("candidates", [])
    except Exception as e:
        print(f"  [FAIL] Stage 1 LLM/parse error: {type(e).__name__}: {e} — treating as 0 candidates")
        candidates = []
    candidates = [c for c in candidates if isinstance(c, dict)]
    # Run-local candidate_id so Stage 2 can correlate verdicts robustly.
    for i, c in enumerate(candidates, 1):
        c["candidate_id"] = i

    if not candidates:
        print("  0 candidates returned")
    else:
        print(f"  {len(candidates)} candidate(s) returned:")
        for c in candidates:
            print(f"    #{c['candidate_id']} [{c.get('category', '?')}] {c.get('title', '?')} "
                  f"(fit={c.get('family_fit', '?')}, conf={c.get('confidence', '?')})")

    # Stage 2: SKEPTIC -- one batch verification call, the hallucination guard.
    _section("STAGE 2 · SKEPTIC")
    verdicts_by_id = {}
    if candidates:
        try:
            raw2 = X.llm(
                messages=[{"role": "user", "content": C.SKEPTIC_PROMPT.format(
                    today=today_iso, home_area=C.HOME_AREA, radius_minutes=C.RADIUS_MINUTES,
                    candidates=json.dumps(candidates, ensure_ascii=False, indent=2), memory=mem_text,
                )}],
                model=C.MODEL_SKEPTIC, max_tokens=C.MAX_TOKENS_SKEPTIC, want_search=True,
                response_schema=C.STAGE2_RESPONSE_SCHEMA, provider=C.PROVIDER_SKEPTIC,
            )
            verdicts = X.parse_json_block(raw2) or []
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
            # SKEPTIC failed outright (no verdicts at all): non-evergreen items are
            # entirely unverified, so drop them rather than risk a hallucination.
            verdict, note = "kill", "skeptic verification failed — dropping unverified event"

        c["verdict"], c["note"] = verdict, note
        if verdict == "kill":
            print(f"    [KILL    ] #{c['candidate_id']} {c.get('title', '?')} — {note}")
            continue
        if not is_evergreen and c.get("family_fit", 0) < C.MIN_INCLUDE_SCORE:
            c["verdict"] = "skipped"
            print(f"    [LOW-FIT ] #{c['candidate_id']} {c.get('title', '?')} — family_fit "
                  f"{c.get('family_fit', '?')} < {C.MIN_INCLUDE_SCORE}")
            continue
        print(f"    [{verdict.upper():<9}] #{c['candidate_id']} {c.get('title', '?')} — {note}")
        survivors.append(c)
    print(f"  -> {len(survivors)} survivor(s)")

    # Anti-repeat filter + selection.
    _section("ANTI-REPEAT FILTER")
    seen_state = load_seen()
    seen_state = prune_seen(seen_state)

    event_survivors     = [c for c in survivors if c.get("category") in ("event_this_weekend", "event_lookahead")]
    evergreen_survivors = [c for c in survivors if c.get("category") == "evergreen"]

    fresh_events = []
    for c in event_survivors:
        role = "thisweekend" if c["category"] == "event_this_weekend" else "lookahead"
        key = event_key(c.get("title", ""), c.get("date_iso"), role)
        c["_key"] = key
        if is_seen(seen_state, key):
            c["verdict"] = "suppressed"
            print(f"    [SUPPRESS] {c.get('title', '?')} — seen within {C.EVENT_TTL_DAYS}d cooldown")
            continue
        fresh_events.append(c)

    selected_events = select_events(fresh_events)
    for c in fresh_events:
        if c not in selected_events:
            c["verdict"] = "skipped"

    selected_evergreens = select_evergreens(evergreen_survivors, mem)
    for c in selected_evergreens:
        c.setdefault("_key", evergreen_key(c["title"]))

    for c in selected_events + selected_evergreens:
        c["verdict"] = "sent"
        mark_seen(seen_state, c["_key"])

    print(f"  -> {len(selected_events)} event(s), {len(selected_evergreens)} evergreen(s) selected")

    # Stage 3: CONCIERGE -- write the email.
    _section("STAGE 3 · CONCIERGE")
    concierge_candidates = []
    for c in selected_events + selected_evergreens:
        source_url, maps_url, search_url = build_links(c)
        concierge_candidates.append({
            "title": c.get("title"), "category": c.get("category"), "when_text": c.get("when_text"),
            "date_iso": c.get("date_iso"), "location": c.get("location"), "reason": c.get("reason"),
            "family_fit": c.get("family_fit"), "practical": c.get("practical", ""),
            "source_url": source_url, "maps_url": maps_url, "search_url": search_url,
        })

    subject, html, text = None, "", ""
    try:
        raw3 = X.llm(
            messages=[{"role": "user", "content": C.CONCIERGE_PROMPT.format(
                today=today_iso, home_area=C.HOME_AREA,
                candidates=json.dumps(concierge_candidates, ensure_ascii=False, indent=2),
                weather=weather_text, feedback=feedback, memory=mem_text,
            )}],
            model=C.MODEL_CONCIERGE, max_tokens=C.MAX_TOKENS_CONCIERGE, want_search=False,
            response_schema=C.CONCIERGE_RESPONSE_SCHEMA, provider=C.PROVIDER_CONCIERGE,
        )
        out = X.parse_json_block(raw3) or {}
        subject, html, text = out.get("subject"), out.get("html", ""), out.get("text", "")
    except Exception as e:
        print(f"  [FAIL] Stage 3 LLM/parse error: {type(e).__name__}: {e} — using fallback email")

    if not html or not text:
        print("  concierge output incomplete — building a plain fallback email")
        fallback_html, fallback_text = _fallback_email(selected_events, selected_evergreens, today_iso)
        html = html or fallback_html
        text = text or fallback_text
    subject = subject or f"Weekend Concierge — {today_iso}"

    # Memory write: ledger entry per candidate that reached Stage 2; evergreen catalog
    # grows with anything new SKEPTIC confirmed, and included evergreens get last_suggested
    # bumped so the cooldown rotation actually rotates.
    _section("MEMORY + OUTPUTS")
    for c in event_survivors:
        M.record_suggestion(mem, c.get("title", ""), c.get("category", ""),
                            c.get("when_text") or c.get("date_iso") or "",
                            location=c.get("location", ""), url=c.get("source_url", ""),
                            score=c.get("family_fit"), verdict=c.get("verdict", "skipped"),
                            note=c.get("note", ""))
    for c in evergreen_survivors:
        if c.get("title") not in mem["evergreen"]:
            M.record_evergreen(mem, c["title"], location=c.get("location", ""),
                               description=c.get("reason", ""), source="find")
    for c in selected_evergreens:
        M.record_evergreen(mem, c["title"], location=c.get("location", ""),
                           description=c.get("reason", ""), suggested=True)
        M.record_suggestion(mem, c["title"], "evergreen", c.get("when_text", ""),
                            location=c.get("location", ""), score=c.get("family_fit"),
                            verdict="sent", note=c.get("reason", ""))

    M.prune(mem)
    M.save(mem)
    print(f"  memory written: {len(mem['evergreen'])} evergreen(s), {len(mem['ledger'])} ledger entry(s)")

    signals = [{
        "candidate_id": c.get("candidate_id"), "title": c.get("title"), "category": c.get("category"),
        "when_text": c.get("when_text"), "date_iso": c.get("date_iso"), "location": c.get("location"),
        "family_fit": c.get("family_fit"), "reason": c.get("reason"), "source_url": c.get("source_url"),
        "confidence": c.get("confidence"), "verdict": c.get("verdict"), "note": c.get("note"),
    } for c in candidates]
    X.save_json("weekend_signals.json", {"generated": today_iso, "signals": signals})
    write_log(today_iso, candidates, selected_events, selected_evergreens, weather_text, subject)
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
          f"{len(selected_events)} event(s) + {len(selected_evergreens)} evergreen(s) sent")


if __name__ == "__main__":
    main()
