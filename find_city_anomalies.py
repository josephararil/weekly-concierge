"""
find_city_anomalies.py  —  Diamond Finder (daily; apidojo grounding with LLM fallback)

Three-stage gate (grounding runs BEFORE the skeptic, so desirability is judged on the
real bookable price rather than the Stage-1 estimate):
  Stage 1 (find):    one llm() call with web search. Asks for travel-arbitrage
                     candidates scored 0-100 across hotels, cruises, flight fares,
                     packages, and currency plays reachable from Plovdiv.
  Stage 2 (ground):  one ground_deal() call per gate survivor. Fetches live Booking.com
                     prices at bookable dates; drops hallucinations/over-ceiling deals and
                     merges the real price onto the survivors. (verdict: confirm/correct/kill)
  Stage 3 (skeptic): one llm() call, no search. Judges the LIVE price against absolute
                     per-country bands and assigns a tier (diamond / good / skip).

Outputs every run:
  state/city_signals.json  — Stage 1 candidate list (hunt=False always; schema kept for reference)
  state/city_signals.md    — human-readable log including grounding + tier outcomes
  state/signals_seen.json  — anti-spam TTL state, committed by CI

Emails an honest daily digest of every scored candidate (diamond/good/skip) whenever
there is a new or tier-changed one, plus a "seen & dropped" footer of grounding kills.
Anti-spam TTL (destination|window|tier) keeps repeats quiet; a day with nothing new
(or nothing found) sends nothing.
"""

import json, datetime as dt
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass
import config as C
import common as X
import memory as M


# --- run-log helpers ---

def _section(title):
    """Print a section banner so the CI run log reads as clear, scannable stages."""
    print(f"\n{'=' * 66}\n  {title}\n{'=' * 66}")


def _eur(v):
    """Format an optional EUR price for the log; '€?' when unknown."""
    return f"€{v}" if v is not None else "€?"


# Tier labels for the email/markdown (emoji ok in HTML/MD — never in console prints).
TIER_LABEL = {"diamond": "💎 Diamond", "good": "👍 Good find", "skip": "· Skipped"}
# Sort/priority rank for tiers in the digest (diamonds first, skips last).
TIER_RANK = {"diamond": 0, "good": 1, "skip": 2}
# Badge colour per tier.
TIER_COLOR = {"diamond": "#0a7d2e", "good": "#8a6d00", "skip": "#777"}


def _baseline_note(baselines, destination, window, grounded_ppn):
    """Short 'typically ~€X/night — N% under/over' note if a baseline exists for this
    destination+season, else ''. `baselines` must be the PRIOR-run snapshot (taken before
    this run recorded its own prices), else a deal compares against itself."""
    if grounded_ppn is None:
        return ""
    season = M.season_key(window or "")
    b = (baselines or {}).get(f"{destination}|{season}")
    base = b.get("realistic_price_eur") if b else None
    if not base:
        return ""
    diff = (grounded_ppn - base) / base * 100
    if diff <= -5:
        return f"Typically ~€{base}/night here — this is {abs(round(diff))}% under."
    if diff >= 5:
        return f"Typically ~€{base}/night here — this is {round(diff)}% over."
    return f"Typically ~€{base}/night here — about the usual rate."


def _sgn_str(n):
    """'+3' / '-15' / '?' — signed modifier for score breakdowns."""
    if n is None:
        return "?"
    return f"+{n}" if n >= 0 else f"{n}"


def _score_breakdown_text(d):
    """'Score: 58 desirability +11 price -3 transit = 66/100 (skip)' or '' if unscored."""
    final = d.get("final_score"); llm = d.get("llm_score")
    if final is None or llm is None:
        return ""
    return (f"Score: {llm} desirability {_sgn_str(d.get('price_adj'))} price "
            f"{_sgn_str(d.get('transit_adj'))} transit = {final}/100 ({d.get('tier', '?')})")


def _score_breakdown_html(d):
    txt = _score_breakdown_text(d)
    if not txt:
        return ""
    # Bold the final number for scanability.
    final = d.get("final_score")
    txt = txt.replace(f"= {final}/100", f"= <b>{final}</b>/100")
    return f"<div style='font-size:12px;color:#888;margin:4px 0'>{txt}</div>"


# --- stage correlation helper ---

def _match_candidate(verdict, candidates):
    """Find the Stage-1 candidate a Stage-2 verdict refers to.
    Primary key: the run-local deal_id (Python-assigned, robust). Falls back to an
    exact destination-string match if deal_id is absent or unrecognised."""
    vid = verdict.get("deal_id")
    if vid is not None:
        match = next((c for c in candidates if str(c.get("deal_id")) == str(vid)), None)
        if match:
            return match
    dest = verdict.get("destination", "")
    return next((c for c in candidates if c.get("destination") == dest), None)


# --- anti-spam state helpers ---

def load_seen():
    return X.load_json("signals_seen.json", {"seen": {}, "monthly_count": {}})


def prune_seen(state):
    cutoff = (dt.date.today() - dt.timedelta(days=C.SIGNAL_TTL_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in state.get("seen", {}).items() if v >= cutoff}
    return state


def seen_key(destination, window, tier=""):
    """Anti-spam key. Includes the tier so a tier CHANGE at the same destination+window
    (e.g. a skip becoming a good when the price drops) re-notifies instead of being
    suppressed — that upgrade is the whole point. Same destination+window+tier stays quiet
    for SIGNAL_TTL_DAYS."""
    return f"{destination}|{window}|{tier}"


def is_already_seen(state, destination, window, tier=""):
    return seen_key(destination, window, tier) in state.get("seen", {})


def mark_seen(state, destination, window, tier=""):
    state.setdefault("seen", {})[seen_key(destination, window, tier)] = X.today_iso()


def this_month():
    return dt.date.today().strftime("%Y-%m")


def monthly_email_count(state):
    return state.get("monthly_count", {}).get(this_month(), 0)


def increment_monthly(state):
    state.setdefault("monthly_count", {})[this_month()] = monthly_email_count(state) + 1


# --- email builders ---

def build_email_html(items, dropped, month_count, baselines):
    rows = ""
    for d in items:
        type_label = d.get("type", "").replace("_", " ").title()
        summary = d.get("assistant_summary") or d.get("reason", "")
        tier = d.get("tier", "good")
        tier_badge = TIER_LABEL.get(tier, "👍 Good find")
        badge_color = TIER_COLOR.get(tier, "#8a6d00")

        # "typically ~€X/night — N% under" comparison from prior-run baselines, if any.
        base_note = _baseline_note(baselines, d.get("destination", ""), d.get("window", ""),
                                   d.get("grounded_price_per_night_eur"))
        base_html = (
            f"<div style='font-size:13px;color:#555;margin:4px 0'>{base_note}</div>"
            if base_note else ""
        )

        # Options list — each with dates, price, and a booking link or how-to-book text
        opts_html = ""
        options = d.get("options") or []
        if options:
            opt_items = ""
            for opt in options:
                dates = opt.get("dates", "")
                pn = opt.get("price_per_night_eur")
                total = opt.get("total_eur")
                url = opt.get("booking_url") or ""
                source = opt.get("source", "")
                price_str = f"€{pn}/night · €{total} total" if (pn is not None and total is not None) else ""
                if url:
                    book_part = f"<a href='{url}' style='color:#1a56db;text-decoration:none'>Book now</a>"
                    src_note = (
                        f" &nbsp;<span style='color:#999;font-size:12px'>({source})</span>"
                        if source else ""
                    )
                else:
                    how = d.get("how_to_book") or source or "see grounding below"
                    book_part = f"<span style='color:#555'>{how}</span>"
                    src_note = ""
                cells = " &nbsp;·&nbsp; ".join(p for p in [dates, price_str, book_part + src_note] if p)
                opt_items += f"<li style='margin:5px 0;font-size:14px'>{cells}</li>"
            opts_html = f"<ul style='margin:6px 0 6px 20px;padding:0'>{opt_items}</ul>"
        elif d.get("how_to_book"):
            opts_html = (
                f"<div style='font-size:14px;color:#444;margin:6px 0'>"
                f"<b>How to book:</b> {d['how_to_book']}</div>"
            )

        # Family-price caveat for hotel deals: apidojo's live rate can under-report the
        # child surcharge (the exact trap that made a €103/night rate become €340 on click).
        child_caveat_html = (
            f"<div style='font-size:12px;color:#a15c00;margin:4px 0'>"
            f"⚠ Live rate is a base room price — reconfirm the 4-year-old is included at "
            f"this price on Booking before booking; child surcharges aren't always reflected.</div>"
            if d.get("type") == "hotel" else ""
        )
        grounding_html = (
            f"<div style='font-size:12px;color:#777;margin:4px 0'>Source: {d['grounding']}</div>"
            if d.get("grounding") else ""
        )
        red_flags_html = (
            f"<div style='font-size:13px;color:#c00;margin:4px 0'>Red flags: {d['red_flags']}</div>"
            if d.get("red_flags") else ""
        )
        # Compact score breakdown so the reasoning behind the tier is visible — especially
        # useful on a skip (you see exactly why it fell short and could still override it).
        score_html = _score_breakdown_html(d)

        rows += (
            f"<tr><td style='padding:14px 0;border-bottom:1px solid #eee'>"
            f"<div style='font-size:12px;font-weight:bold;color:{badge_color};margin-bottom:2px'>{tier_badge}</div>"
            f"<div style='font-size:17px;font-weight:bold'>{d['destination']}</div>"
            f"<div style='font-size:13px;color:#777;margin:3px 0'>"
            f"{type_label} &nbsp;·&nbsp; {d.get('window', '')}</div>"
            f"<div style='font-size:14px;color:#222;margin:6px 0'>{summary}</div>"
            f"{score_html}"
            f"{base_html}"
            f"{opts_html}"
            f"{child_caveat_html}"
            f"{grounding_html}"
            f"{red_flags_html}"
            f"</td></tr>"
        )

    n_diamond = sum(1 for d in items if d.get("tier") == "diamond")
    n_good = sum(1 for d in items if d.get("tier") == "good")
    n_skip = sum(1 for d in items if d.get("tier") == "skip")
    headline = " · ".join(p for p in [
        f"{n_diamond} diamond" if n_diamond else "",
        f"{n_good} good find(s)" if n_good else "",
        f"{n_skip} logged" if n_skip else "",
    ] if p) or f"{len(items)} find(s)"

    # "Seen & dropped" footer — candidates that reached grounding but were killed or
    # blocked before scoring, so you can see what the pipeline looked at and rejected.
    dropped_html = ""
    if dropped:
        drop_items = "".join(
            f"<li style='margin:4px 0;font-size:13px;color:#777'>"
            f"<b>{x['destination']}</b> ({x.get('window', '')}) — {x['kind']}: {x['reason']}</li>"
            for x in dropped
        )
        dropped_html = (
            f"<div style='margin-top:18px;padding-top:12px;border-top:1px solid #eee'>"
            f"<div style='font-size:12px;font-weight:bold;color:#999;margin-bottom:4px'>"
            f"Also seen &amp; dropped before scoring</div>"
            f"<ul style='margin:0 0 0 18px;padding:0'>{drop_items}</ul></div>"
        )

    conscience = ""
    if month_count >= 8:
        conscience = (
            f"<p style='color:#999;font-size:12px;margin-top:16px'>"
            f"Note: {month_count} email(s) sent this month — firing more than usual. "
            f"All are genuine finds, but worth checking if the tier bands need tuning.</p>"
        )
    return (
        f"<div style='font-family:system-ui,sans-serif;max-width:640px;padding:8px'>"
        f"<h2 style='margin-bottom:4px'>Diamond Finder</h2>"
        f"<p style='color:#555;margin:0 0 16px'>Today's digest — {headline}</p>"
        f"<table style='width:100%;border-collapse:collapse'>{rows}</table>"
        f"{dropped_html}"
        f"{conscience}"
        f"<p style='color:#bbb;font-size:11px;margin-top:16px'>"
        f"Prices are live from Booking.com at send time. 💎 diamonds are rare grab-it finds; "
        f"👍 good finds are solid but not urgent; skipped items are shown so you see what the "
        f"pipeline weighed and why. Verify before booking.</p>"
        f"</div>"
    )


def build_email_text(items, dropped, baselines):
    parts = []
    _text_tier = {"diamond": "DIAMOND", "good": "GOOD FIND", "skip": "SKIPPED"}
    for d in items:
        summary = d.get("assistant_summary") or d.get("reason", "")
        tier = d.get("tier", "good")
        tier_label = _text_tier.get(tier, "GOOD FIND")
        lines = [
            f"[{tier_label}] {d['destination']} ({d.get('type', '')})",
            f"Window: {d.get('window', '')}",
            summary,
        ]
        score_note = _score_breakdown_text(d)
        if score_note:
            lines.append(score_note)
        base_note = _baseline_note(baselines, d.get("destination", ""), d.get("window", ""),
                                   d.get("grounded_price_per_night_eur"))
        if base_note:
            lines.append(base_note)
        options = d.get("options") or []
        if options:
            lines.append("Options:")
            for opt in options:
                dates = opt.get("dates", "")
                pn = opt.get("price_per_night_eur")
                total = opt.get("total_eur")
                url = opt.get("booking_url") or ""
                source = opt.get("source", "")
                price_str = f"€{pn}/night · €{total} total" if (pn is not None and total is not None) else ""
                if url:
                    book_str = url
                    src_note = f" ({source})" if source else ""
                else:
                    book_str = d.get("how_to_book") or source or ""
                    src_note = ""
                cells = " · ".join(p for p in [dates, price_str, book_str + src_note] if p)
                lines.append(f"  - {cells}")
        elif d.get("how_to_book"):
            lines.append(f"How to book: {d['how_to_book']}")
        if d.get("type") == "hotel":
            lines.append("Note: live rate is a base room price — reconfirm the 4-year-old "
                         "is included at this price on Booking before booking.")
        if d.get("grounding"):
            lines.append(f"Source: {d['grounding']}")
        if d.get("red_flags"):
            lines.append(f"Red flags: {d['red_flags']}")
        parts.append("\n".join(lines))
    body = "\n\n---\n\n".join(parts)
    if dropped:
        drop_lines = ["Also seen & dropped before scoring:"]
        drop_lines += [
            f"  - {x['destination']} ({x.get('window', '')}) — {x['kind']}: {x['reason']}"
            for x in dropped
        ]
        body += "\n\n===\n\n" + "\n".join(drop_lines)
    return body


# --- markdown log ---

def write_md(today, candidates, picks, grounding_results=None, scores=None):
    """Human-readable run log. picks = diamond/good candidates; grounding_results =
    {deal_id: stage3 result} for every grounded candidate; scores = {deal_id: {llm,
    price_adj, transit_adj, final, tier, why, red_flags}} for every scored candidate."""
    grounding_results = grounding_results or {}
    scores = scores or {}
    n_diamond = sum(1 for s in scores.values() if s.get("tier") == "diamond")
    n_good = sum(1 for s in scores.values() if s.get("tier") == "good")

    def _sgn(n):
        return f"+{n}" if n is not None and n >= 0 else (f"{n}" if n is not None else "?")

    lines = [f"# Diamond Finder — {today}", ""]
    if not candidates:
        lines.append("_No candidates found today._")
    else:
        lines.append(
            f"_Stage 1: {len(candidates)} candidate(s). "
            f"{len(grounding_results)} grounded · {len(scores)} scored. "
            f"{n_diamond} diamond · {n_good} good._"
        )
        lines.append("")
        for c in sorted(candidates, key=lambda x: x.get("score", 0), reverse=True):
            dest = c.get("destination", "?")
            did = c.get("deal_id")
            s = scores.get(did)
            tier = s.get("tier") if s else None
            marker = {"diamond": " 💎", "good": " 👍", "skip": " ·"}.get(tier, "")
            find_score = c.get("score", 0)
            conf = c.get("confidence", "?")
            est = c.get("est_price_eur")
            est_str = f" · est €{est}/night" if est is not None else ""
            lines.append(f"### {dest}{marker} — FIND {find_score}/100 ({conf}){est_str}")
            lines.append(
                f"**Type:** {c.get('type', '?')} &nbsp; **Window:** {c.get('window', '?')}"
            )
            lines.append(f"{c.get('reason', '')}")
            if s:
                lines.append(
                    f"_Score: LLM {s['llm']} {_sgn(s['price_adj'])} price "
                    f"{_sgn(s['transit_adj'])} transit = **{s['final']}** → {tier}_"
                )
                if s.get("why"):
                    lines.append(f"_Scorer: {s['why']}_")
            lines.append("")

    if grounding_results:
        lines.append("## Grounding & scoring")
        lines.append("")
        # Iterate in candidate order for stability.
        for c in sorted(candidates, key=lambda x: x.get("score", 0), reverse=True):
            did = c.get("deal_id")
            r = grounding_results.get(did)
            if not r:
                continue
            verdict3 = r.get("verdict", "?")
            icon = "✅" if verdict3 == "confirm" else "🔧" if verdict3 == "correct" else "❌"
            dest3 = r.get("destination", c.get("destination", "?"))
            conf3 = r.get("confidence", "?")
            s = scores.get(did)
            score_str = f" → final **{s['final']}** ({s['tier']})" if s else ""
            lines.append(f"### {icon} {dest3} — {verdict3.upper()} (confidence: {conf3}){score_str}")
            if r.get("assistant_summary"):
                lines.append(f"**Summary:** {r['assistant_summary']}")
            opts = r.get("options", [])
            if opts:
                lines.append("**Options:**")
                for opt in opts:
                    dates = opt.get("dates", "?")
                    pn = opt.get("price_per_night_eur", "?")
                    total = opt.get("total_eur", "?")
                    url = opt.get("booking_url") or ""
                    src = opt.get("source", "")
                    link = f" · [book]({url})" if url else ""
                    src_note = f" · _{src}_" if src else ""
                    lines.append(f"  - {dates} · €{pn}/night · €{total} total{link}{src_note}")
            if r.get("how_to_book"):
                lines.append(f"**How to book:** {r['how_to_book']}")
            if r.get("grounding"):
                lines.append(f"**Grounding:** {r['grounding']}")
            if r.get("_block_reason"):
                lines.append(f"_🔒 Not scored (data-quality guard): {r['_block_reason']}_")
            lines.append("")
    with open("state/city_signals.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# --- helpers ---

def _dates_in_window(option_dates, candidate_window):
    """Rough sanity check: option dates should fall in the same YYYY-MM as the candidate window.
    If either string can't be parsed to YYYY-MM, let it through (don't block on ambiguity)."""
    import re
    opt_season = M.season_key(option_dates)
    win_season = M.season_key(candidate_window)
    ym = re.compile(r'^\d{4}-\d{2}$')
    if ym.match(opt_season) and ym.match(win_season):
        return opt_season == win_season
    return True


# --- Layer-3 grounding ---

def _ground_llm(diamond, mem_text, today):
    """Layer-3 grounding via LLM concierge + web search (current active implementation)."""
    candidate_json = json.dumps(diamond, ensure_ascii=False, indent=2)
    verify_prompt = C.VERIFY_PROMPT.format(
        today=today,
        candidate=candidate_json,
        memory=mem_text,
    )
    raw3 = X.llm(
        messages=[{"role": "user", "content": verify_prompt}],
        model=C.MODEL_VERIFY, max_tokens=C.MAX_TOKENS_VERIFY, want_search=True,
        response_schema=C.STAGE3_RESPONSE_SCHEMA,
        provider=C.PROVIDER_VERIFY,
    )
    return X.parse_json_block(raw3) or {}


# ── GROUNDING SEAM ──────────────────────────────────────────────────────────
# ground_deal is the active Layer-3 grounding function. Defaults to the apidojo
# Booking.com provider; falls back to _ground_llm on any import or runtime failure.
# Set HOTEL_PROVIDER="" to force LLM-only grounding.

def _resolve_ground_deal():
    if (C.HOTEL_PROVIDER or "").strip().lower() == "apidojo":
        try:
            from providers import ground_api
            return ground_api          # ground_api falls back to _ground_llm at runtime
        except Exception as e:
            print(f"  [providers] import failed, using LLM grounding: {e}")
    return _ground_llm

ground_deal = _resolve_ground_deal()


# --- main ---

def main():
    today = X.today_iso()
    _section(f"DIAMOND FINDER · {today} · provider={X.PROVIDER}")
    print(f"  models:  find={C.MODEL_FIND} · skeptic={C.MODEL_SKEPTIC} · verify={C.MODEL_VERIFY}")
    print(f"  gate:    FIND score>={C.STAGE1_MIN_SCORE} -> ground · anti-spam TTL {C.SIGNAL_TTL_DAYS}d")
    print(f"  scoring: par={C.DIAMOND_PAR_EUR} default €{C.DEFAULT_DIAMOND_PAR_EUR} · "
          f"price x{C.PRICE_SCORE_WEIGHT} (bonus<={C.PRICE_BONUS_CAP}) · transit +/-{C.TRANSIT_TIER1_BONUS} · "
          f"diamond>={C.DIAMOND_SCORE_THRESHOLD} good>={C.GOOD_SCORE_THRESHOLD}")

    # Load memory once; inject into all three stage prompts. Snapshot the baselines as
    # they were BEFORE this run so the email's "typically ~€X/night" comparison reflects
    # prior normals, not the prices this same run is about to record.
    mem = M.load()
    prior_baselines = {**mem.get("baselines", {})}
    mem_text = M.summarize_for_prompt(mem)
    print(f"  memory:  {len(mem['baselines'])} baseline(s), {len(mem['ledger'])} ledger entry(s) loaded")

    # Stage 1: find candidates with web search
    _section("STAGE 1 · FIND — live search + scoring")
    try:
        # The Anthropic Find model searches inline (web_search tool); the Gemini Find
        # model has no tool — its leads come via SEARCH_RESULTS_PREAMBLE — so the
        # tool-use directive is Anthropic-only. Keeps FIND_PROMPT honest per provider.
        find_directive = (C.SEARCH_DIRECTIVE_ANTHROPIC
                          if X.resolved_provider(C.PROVIDER_FIND) == "anthropic" else "")
        raw1 = X.llm(
            messages=[{"role": "user", "content": C.FIND_PROMPT.format(
                today=today, cities=C.cities_prompt_text(), memory=mem_text,
                search_directive=find_directive
            )}],
            model=C.MODEL_FIND, max_tokens=C.MAX_TOKENS_FIND, want_search=True,
            response_schema=C.STAGE1_RESPONSE_SCHEMA,
            provider=C.PROVIDER_FIND,
            search_prompt=C.SEARCH_PROMPT.format(today=today, cities=C.cities_prompt_text()),
        )
        candidates = (X.parse_json_block(raw1) or {}).get("candidates", [])
    except Exception as e:
        print(f"  [FAIL] Stage 1 LLM/parse error: {type(e).__name__}: {e} — treating as 0 candidates (silent day)")
        candidates = []
    candidates = [c for c in candidates if isinstance(c, dict)]
    # Assign a run-local deal_id (1-based) Python-side so downstream stages correlate
    # candidates by a stable integer key instead of fragile destination-string matching.
    # Run-local ONLY: not a persistent id — signals_seen/memory stay keyed by
    # destination+window so they survive across runs.
    for i, c in enumerate(candidates, 1):
        c["deal_id"] = i

    if not candidates:
        print("  0 candidates returned — genuine quiet day, OR a truncation/parse miss "
              "(check for a [gemini] WARNING above)")
    else:
        print(f"  {len(candidates)} candidate(s) returned (high->low score):")
        for c in sorted(candidates, key=lambda x: x.get("score", 0), reverse=True):
            hotel = c.get("hotel_name") or ""
            print(f"    #{c.get('deal_id')} score={str(c.get('score', '?')):>3}  "
                  f"{_eur(c.get('est_price_eur')):>5}  {str(c.get('window', '?')):<16}  "
                  f"{c.get('destination', '?')} [{c.get('type', '?')}]"
                  + (f" — {hotel}" if hotel else ""))

    # Stage 1 gate: forward only candidates FIND scored highly enough to be worth grounding.
    # This is pure triage on FIND's estimate (cost control). There is NO price filter here —
    # price is handled later by the deterministic scorer, so nothing is dropped on price.
    gate_survivors  = [c for c in candidates if c.get("score", 0) >= C.STAGE1_MIN_SCORE]
    below_threshold = [c for c in candidates if c.get("score", 0) < C.STAGE1_MIN_SCORE]

    if candidates:
        print(f"  gate (FIND score >= {C.STAGE1_MIN_SCORE}; no price filter — price is scored later):")
        for c in below_threshold:
            print(f"    [DROP ] #{c.get('deal_id')} {c.get('destination', '?')} — FIND score {c.get('score', '?')} < {C.STAGE1_MIN_SCORE}")
        for c in gate_survivors:
            print(f"    [PASS ] #{c.get('deal_id')} {c.get('destination', '?')} -> grounding")
        print(f"  -> {len(gate_survivors)} forwarded · {len(below_threshold)} below-threshold")

    # Stage 2: GROUND each gate survivor on live prices BEFORE scoring desirability.
    # This is the pivot of the pipeline: the scorer must reason about the REAL bookable
    # price, not the Stage-1 estimate. A grounding kill (hotel not found / hallucination)
    # drops the candidate here; a confirm/correct carries the live price on to the scorer.
    # Data-quality guards (unusable/low-confidence price, dates out of window) block a
    # candidate from scoring — but NOT a price ceiling (price is scored, never a wall).
    _section("STAGE 2 · GROUND — live price verification")
    grounded = []            # candidates (with grounded fields merged) forwarded to scorer
    grounding_results = {}    # deal_id -> stage3 result, for every grounded candidate
    if not gate_survivors:
        print("  nothing to ground — no candidate cleared the Stage 1 gate")
    else:
        provider_label = ("Booking.com apidojo (LLM fallback)"
                          if (C.HOTEL_PROVIDER or "").strip().lower() == "apidojo"
                          else "LLM concierge")
        print(f"  grounding {len(gate_survivors)} candidate(s) via {provider_label}…")
        for c in gate_survivors:
            dest = c.get("destination", "?")
            did  = c.get("deal_id")
            try:
                result = ground_deal(c, mem_text, today)
            except Exception as e:
                print(f"    [FAIL ] #{did} {dest}: grounding raised {type(e).__name__}: {e} — treating as kill")
                result = {}
            if not result:
                result = {}
            grounding_results[did] = result
            verdict3 = result.get("verdict", "kill")
            conf3    = result.get("confidence", "low")
            options3 = result.get("options") or []
            summary3 = M._clip(result.get("assistant_summary") or result.get("grounding") or "", 200)

            print(f"    [{verdict3.upper():<7}] #{did} {dest}  (confidence={conf3})")
            if options3:
                o = options3[0]
                print(f"             grounded: {_eur(o.get('price_per_night_eur'))}/night · "
                      f"{o.get('dates', '?')} · {_eur(o.get('total_eur'))} total")
            if summary3:
                print(f"             {summary3}")

            if verdict3 == "kill":
                print(f"             [DROP] grounding kill — not forwarded to scorer")
                continue

            # confirm/correct: data-quality guards only (no price ceiling).
            first_dates    = options3[0].get("dates", "") if options3 else ""
            block_reason = None
            if conf3 == "low":
                block_reason = "low confidence (unreliable price)"
            elif not options3:
                block_reason = "no grounded option returned"
            elif not _dates_in_window(first_dates, c.get("window", "")):
                block_reason = f"dates out of window ({first_dates!r} vs candidate window {c.get('window', '')!r})"
            if block_reason:
                result["_block_reason"] = block_reason
                print(f"             [BLOCK] not forwarded to scorer: {block_reason}")
                continue

            o = options3[0]
            grounded.append({**c,
                "verdict": verdict3,
                "options": options3,
                "how_to_book": result.get("how_to_book", ""),
                "grounding": result.get("grounding", ""),
                "assistant_summary": result.get("assistant_summary", ""),
                "confidence": conf3,
                "grounded_price_per_night_eur": o.get("price_per_night_eur"),
                "grounded_total_eur": o.get("total_eur"),
                "grounded_nights": o.get("nights"),
                "grounded_dates": o.get("dates"),
            })
            print(f"             [-> SCORER]")
        print(f"  -> {len(grounded)} grounded candidate(s) forwarded to scorer")

    # Stage 3: SCORER. The LLM returns a 0-100 desirability score per grounded candidate
    # (price held neutral). The pipeline then applies deterministic modifiers
    # (compute_final_score: price-vs-par + drive-vs-fly) and derives the tier from the final
    # score. Nothing is vetoed by the LLM — a low final score is dropped by default, but the
    # full breakdown (llm · price_adj · transit_adj · final · tier) is recorded for every
    # candidate so no signal is lost and the LLM's judgment stays visible for tuning.
    _section("STAGE 3 · SCORE — desirability + deterministic modifiers")
    scored_all = []  # every scored candidate (diamond/good/skip) — the full digest set
    picks = []       # diamond/good subset — the actionable "act now" finds
    scores = {}      # deal_id -> {llm, price_adj, transit_adj, final, tier, why, red_flags}
    if not grounded:
        print("  nothing to score — no candidate survived grounding")
    else:
        print(f"  scoring {len(grounded)} grounded candidate(s)…")
        scorer_input = [{
            "deal_id":                      c.get("deal_id"),
            "destination":                  c.get("destination", ""),
            "type":                         c.get("type", ""),
            "window":                       c.get("window", ""),
            "grounded_price_per_night_eur": c.get("grounded_price_per_night_eur"),
            "grounded_total_eur":           c.get("grounded_total_eur"),
            "grounded_nights":              c.get("grounded_nights"),
            "grounded_dates":               c.get("grounded_dates"),
            "diamond_par_eur":              C.get_diamond_par(c.get("destination", "")),
            "grounding_summary":            c.get("assistant_summary", ""),
            "original_est_price_eur":       c.get("est_price_eur"),
            "reason":                       c.get("reason", ""),
        } for c in grounded]
        scorer = C.SKEPTIC_PROMPT.format(
            today=today,
            diamond_threshold=C.DIAMOND_SCORE_THRESHOLD,
            good_threshold=C.GOOD_SCORE_THRESHOLD,
            candidates=json.dumps(scorer_input, ensure_ascii=False, indent=2),
            memory=mem_text,
        )
        try:
            raw2 = X.llm(
                messages=[{"role": "user", "content": scorer}],
                model=C.MODEL_SKEPTIC, max_tokens=C.MAX_TOKENS_SKEPTIC, want_search=False,
                response_schema=C.STAGE2_RESPONSE_SCHEMA,
                provider=C.PROVIDER_SKEPTIC,
            )
            verdicts = X.parse_json_block(raw2) or []
        except Exception as e:
            print(f"  [FAIL] Stage 3 scorer LLM/parse error: {type(e).__name__}: {e} — treating as 0 scores (silent day)")
            verdicts = []
        if not isinstance(verdicts, list):
            verdicts = []
        # Index the LLM scores by deal_id (robust to paraphrased destinations).
        llm_by_id = {}
        for v in verdicts:
            if not isinstance(v, dict):
                continue
            orig = _match_candidate(v, grounded)
            if orig:
                llm_by_id[orig.get("deal_id")] = v
            else:
                print(f"    [WARN  ] score matched no candidate "
                      f"(deal_id={v.get('deal_id')!r}, dest={v.get('destination')!r})")

        def _sgn(n):
            return f"+{n}" if n >= 0 else f"{n}"

        for c in grounded:
            did  = c.get("deal_id")
            dest = c.get("destination", "?")
            v    = llm_by_id.get(did)
            if v is None:
                print(f"    [WARN  ] #{did} {dest} — no score returned; treating as 0")
            raw_llm = (v or {}).get("score")
            llm_val = raw_llm if isinstance(raw_llm, (int, float)) else 0
            why     = (v or {}).get("why", "")
            red     = (v or {}).get("red_flags", "")
            ppn     = c.get("grounded_price_per_night_eur")
            final, price_adj, transit_adj = C.compute_final_score(llm_val, ppn, dest)
            tier = C.tier_for_score(final)
            scores[did] = {"llm": llm_val, "price_adj": price_adj, "transit_adj": transit_adj,
                           "final": final, "tier": tier, "why": why, "red_flags": red}

            label = {"diamond": "DIAMOND", "good": "GOOD", "skip": "SKIP"}[tier]
            print(f"    [{label:<7}] #{did} {dest} — llm {llm_val} {_sgn(price_adj)} price "
                  f"{_sgn(transit_adj)} transit = {final} -> {tier}")
            wl = M._clip(why, 150)
            if wl:
                print(f"             {wl}")

            scored_item = {**c, "tier": tier, "final_score": final, "llm_score": llm_val,
                           "price_adj": price_adj, "transit_adj": transit_adj,
                           "why": why, "red_flags": red}
            scored_all.append(scored_item)
            if tier in ("diamond", "good"):
                picks.append(scored_item)

        n_diamond = sum(1 for p in picks if p["tier"] == "diamond")
        n_good    = len(picks) - n_diamond
        n_skip    = len(scored_all) - len(picks)
        print(f"  -> {n_diamond} diamond · {n_good} good · {n_skip} skip")

    # Record outcomes + baselines for every gate survivor that reached grounding.
    # Every candidate's score breakdown is stored (final_score/llm_score) so memory keeps
    # the full signal — a good deal that scored 69 today may score 74 next week at a lower
    # price, and that history is now visible rather than thrown away by a veto.
    for c in gate_survivors:
        did       = c.get("deal_id")
        r3        = grounding_results.get(did) or {}
        g_verdict = r3.get("verdict", "kill")
        options   = r3.get("options") or []
        actual_price = options[0].get("price_per_night_eur") if options else None
        source3   = (options[0].get("source", "") if options else r3.get("grounding", ""))
        sc        = scores.get(did)            # None if killed or guard-blocked before scoring
        summary   = M._clip(r3.get("assistant_summary") or "", 200)

        # Ledger verdict captures where the candidate ended up:
        #   scored → its tier (diamond/good/skip); grounding kill → "kill";
        #   grounded but guard-blocked before scoring → "blocked".
        if sc:
            ledger_verdict = sc["tier"]
            note = M._clip(sc.get("why") or summary, 200)
        elif g_verdict == "kill":
            ledger_verdict = "kill"
            note = summary
        else:
            ledger_verdict = "blocked"
            note = M._clip(r3.get("_block_reason") or summary, 200)

        M.record_outcome(
            mem, c.get("destination", ""), c.get("window", ""), c.get("type", ""),
            claimed_price=c.get("est_price_eur"),
            verdict=ledger_verdict,
            actual_price=actual_price,
            source=source3,
            note=note,
            llm_score=(sc["llm"] if sc else None),
            final_score=(sc["final"] if sc else None),
        )
        # Baseline whenever grounding is confirm/correct, high-confidence, in-window —
        # the live price is real regardless of the desirability tier (even a skip).
        conf3 = r3.get("confidence", "low")
        first_dates = options[0].get("dates", "") if options else ""
        if (g_verdict in ("confirm", "correct") and actual_price
                and conf3 == "high"
                and _dates_in_window(first_dates, c.get("window", ""))):
            season = M.season_key(first_dates or c.get("window", ""))
            M.record_baseline(mem, c.get("destination", ""), season, actual_price,
                              note=M._clip(summary, 300), source=source3)

    M.prune(mem)
    M.save(mem)
    _section("MEMORY + OUTPUTS")
    print(f"  memory written: {len(mem['baselines'])} baseline(s), {len(mem['ledger'])} ledger entry(s) (pruned)")

    # Write city_signals.json — hunt=False always; field kept for schema compatibility.
    # "anomaly" only for deals that reached a diamond/good pick. The full score breakdown
    # is attached to every candidate so the machine-readable log shows what the scorer did.
    pick_ids = {p.get("deal_id") for p in picks}
    signals = [
        {
            "deal_id": c.get("deal_id"),
            "city": c.get("destination", ""),
            "window": c.get("window", ""),
            "reason": c.get("reason", ""),
            "type": "anomaly" if c.get("deal_id") in pick_ids else "reminder",
            "confidence": c.get("confidence", "low"),
            "find_score": c.get("score"),
            "llm_score": (scores.get(c.get("deal_id")) or {}).get("llm"),
            "price_adj": (scores.get(c.get("deal_id")) or {}).get("price_adj"),
            "transit_adj": (scores.get(c.get("deal_id")) or {}).get("transit_adj"),
            "final_score": (scores.get(c.get("deal_id")) or {}).get("final"),
            "tier": (scores.get(c.get("deal_id")) or {}).get("tier"),
            "hunt": False,
        }
        for c in candidates
    ]
    X.save_json("city_signals.json", {"generated": today, "signals": signals})

    # Write markdown every run regardless of email outcome
    write_md(today, candidates, picks, grounding_results, scores)
    n_anom = sum(1 for s in signals if s["type"] == "anomaly")
    print(f"  wrote state/city_signals.json ({len(signals)} signal(s), {n_anom} anomaly) + city_signals.md")

    # "Seen & dropped" footer: gate survivors that reached grounding but were killed
    # (hallucination / no availability) or blocked (data-quality guard) before scoring.
    # Shown so the digest reflects everything the pipeline looked at, not just survivors.
    dropped = []
    for c in gate_survivors:
        did = c.get("deal_id")
        if did in scores:
            continue  # scored → shown in the digest body, not the footer
        r3 = grounding_results.get(did) or {}
        if r3.get("_block_reason"):
            kind, reason = "blocked", r3["_block_reason"]
        else:
            kind = "killed"
            reason = M._clip(r3.get("assistant_summary") or r3.get("grounding")
                             or "no supporting evidence found", 160)
        dropped.append({"destination": c.get("destination", "?"),
                        "window": c.get("window", ""), "kind": kind, "reason": reason})

    # Email — an honest daily digest of EVERY scored candidate (diamond/good/skip), so the
    # reader can see what the pipeline weighed and why. Anti-spam TTL (keyed
    # destination|window|tier) fires the digest on any genuinely new/changed scored item and
    # stays silent when there is nothing new. Only the actionable diamond/good picks are
    # capped by MAX_EMAILS_PER_RUN; skips are context and always shown in full.
    _section("EMAIL — anti-spam gate + send")
    seen_state = load_seen()
    seen_state = prune_seen(seen_state)

    scored_sorted = sorted(scored_all, key=lambda p: (TIER_RANK.get(p.get("tier"), 9),
                                                      -(p.get("final_score") or 0)))
    new_scored = [d for d in scored_sorted
                  if not is_already_seen(seen_state, d["destination"], d["window"], d.get("tier", ""))]
    suppressed = len(scored_all) - len(new_scored)
    emailed = 0

    if new_scored:
        actionable = [d for d in new_scored if d.get("tier") in ("diamond", "good")]
        skips      = [d for d in new_scored if d.get("tier") == "skip"]
        shown_actionable = actionable[:C.MAX_EMAILS_PER_RUN]
        capped = len(actionable) - len(shown_actionable)
        to_email = sorted(shown_actionable + skips,
                          key=lambda p: (TIER_RANK.get(p.get("tier"), 9), -(p.get("final_score") or 0)))
        month_count = monthly_email_count(seen_state)
        n_d = sum(1 for d in to_email if d.get("tier") == "diamond")
        n_g = sum(1 for d in to_email if d.get("tier") == "good")
        n_s = sum(1 for d in to_email if d.get("tier") == "skip")
        print(f"  {len(new_scored)} new/changed scored ({n_d} diamond · {n_g} good · {n_s} skip) · "
              f"{suppressed} suppressed by {C.SIGNAL_TTL_DAYS}d TTL · {capped} good/diamond over cap · "
              f"{len(dropped)} in dropped footer")
        if n_d:
            subject = f"Diamond Finder: {n_d} diamond + {len(to_email) - n_d} more — {today}"
        elif n_g:
            subject = f"Diamond Finder: {n_g} good find(s) + {n_s} logged — {today}"
        else:
            subject = f"Diamond Finder: {n_s} logged travel find(s) — {today}"
        html = build_email_html(to_email, dropped, month_count, prior_baselines)
        text = build_email_text(to_email, dropped, prior_baselines)
        try:
            X.send_email(subject, html, text)
            for d in to_email:
                mark_seen(seen_state, d["destination"], d["window"], d.get("tier", ""))
            increment_monthly(seen_state)
            emailed = len(to_email)
            print(f"  [EMAIL SENT] {', '.join(d.get('destination', '?') for d in to_email)}")
        except Exception as e:
            print(f"  [FAIL] email send error: {type(e).__name__}: {e} (state not marked seen)")
    elif scored_all:
        print(f"  {len(scored_all)} scored but all suppressed by {C.SIGNAL_TTL_DAYS}d anti-spam TTL — no email")
    else:
        print("  no email — nothing reached scoring today")

    X.save_json("signals_seen.json", seen_state)

    _section("RUN COMPLETE")
    print(f"  {len(candidates)} found -> {len(gate_survivors)} to grounding -> {len(grounded)} grounded "
          f"-> {len(scored_all)} scored ({len(picks)} pick(s)) -> {emailed} emailed")


if __name__ == "__main__":
    main()
