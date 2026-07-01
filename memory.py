"""memory.py — self-improving price memory for the diamond finder.

state/memory.json structure:
  baselines: {destination|season → {realistic_price_eur, note, source, updated}}
  ledger:    [{date, destination, window, type, claimed_price, verdict,
               actual_price, source, note}]

Ledger is capped to MAX_LEDGER_ENTRIES entries and MAX_LEDGER_DAYS days.
summarize_for_prompt() produces a compact, bounded text block for prompt injection.
"""

import json, datetime as dt, os, re

STATE_DIR = "state"
_MEMORY_FILE = "memory.json"
_MEMORY_MD   = "memory.md"

_MONTH_NAMES = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

MAX_LEDGER_ENTRIES  = 200    # hard cap on ledger rows
MAX_LEDGER_DAYS     = 180    # TTL for ledger entries
MAX_PROMPT_BASELINES = 10    # baselines injected per prompt
MAX_PROMPT_OUTCOMES  = 10    # recent corrections/kills injected per prompt


def _path(name):
    return os.path.join(STATE_DIR, name)


def season_key(text):
    """Map a free-text window/dates string to a coarse 'YYYY-MM' key.

    Looks for the first month name (or number) paired with a 4-digit year.
    Falls back to the stripped input string if nothing is parseable."""
    t = text.strip()
    # Numeric YYYY-MM / YYYY/MM
    m = re.search(r'(20\d{2})[-/](\d{1,2})\b', t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    # Numeric MM-YYYY / MM/YYYY
    m = re.search(r'\b(\d{1,2})[-/](20\d{2})\b', t)
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    # Month name + 4-digit year (first month name found wins)
    yr = re.search(r'(20\d{2})', t)
    if yr:
        tl = t.lower()
        for name, num in _MONTH_NAMES.items():
            if re.search(rf'\b{re.escape(name)}\b', tl):
                return f"{yr.group(1)}-{num}"
    return t


def _clip(text, limit):
    """Clip text at the last word boundary before limit, appending an ellipsis."""
    if not text or len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _extract_price(text):
    """Best-effort extraction of the first EUR amount from a string.

    Handles €72, EUR 72, 72 EUR, and ranges like €72-95 (returns lower bound).
    Returns None when nothing parseable is found."""
    if not text:
        return None
    m = re.search(r'€(\d+(?:[.,]\d+)?)', text)
    if m:
        return float(m.group(1).replace(',', '.'))
    m = re.search(r'\bEUR\s+(\d+(?:[.,]\d+)?)', text, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(',', '.'))
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*EUR\b', text, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(',', '.'))
    return None


# ── load / save ────────────────────────────────────────────────────────────────

def load():
    """Load memory from state/memory.json. Returns a fresh dict on missing/corrupt file."""
    try:
        with open(_path(_MEMORY_FILE), encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("baselines", {})
        data.setdefault("ledger", [])
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"baselines": {}, "ledger": []}


def save(memory):
    """Save memory to state/memory.json and write state/memory.md digest."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_path(_MEMORY_FILE), "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2, ensure_ascii=False)
    _write_md(memory)


# ── write ──────────────────────────────────────────────────────────────────────

def record_baseline(memory, destination, season, realistic_price_eur, note="", source=""):
    """Upsert a realistic price baseline for a destination/season pair."""
    key = f"{destination}|{season}"
    memory["baselines"][key] = {
        "realistic_price_eur": realistic_price_eur,
        "note": note,
        "source": source,
        "updated": dt.date.today().isoformat(),
    }


def record_outcome(memory, destination, window, type_, claimed_price, verdict,
                   actual_price=None, source="", note="", llm_score=None, final_score=None):
    """Append one pipeline outcome to the rolling ledger.

    llm_score  = the scorer's raw desirability score (0-100), pre-modifiers.
    final_score = the pipeline's final score after deterministic price/transit modifiers.
    Both are kept so a candidate's score history survives across runs (e.g. the same hotel
    scoring 69 at €86 and 74 at €79) instead of being lost to a binary keep/kill."""
    memory["ledger"].append({
        "date":          dt.date.today().isoformat(),
        "destination":   destination,
        "window":        window,
        "type":          type_,
        "claimed_price": claimed_price,
        "verdict":       verdict,   # diamond | good | skip | kill | blocked | correct
        "actual_price":  actual_price,
        "llm_score":     llm_score,
        "final_score":   final_score,
        "source":        source,
        "note":          note,
    })


def prune(memory):
    """Drop ledger entries older than MAX_LEDGER_DAYS or beyond MAX_LEDGER_ENTRIES."""
    cutoff = (dt.date.today() - dt.timedelta(days=MAX_LEDGER_DAYS)).isoformat()
    memory["ledger"] = [e for e in memory["ledger"] if e.get("date", "") >= cutoff]
    if len(memory["ledger"]) > MAX_LEDGER_ENTRIES:
        memory["ledger"] = memory["ledger"][-MAX_LEDGER_ENTRIES:]
    return memory


# ── prompt summary ─────────────────────────────────────────────────────────────

def summarize_for_prompt(memory, cities=None):
    """Return a compact text block for injection into FIND/SKEPTIC/VERIFY prompts.

    cities: optional list of city/destination strings; when given, only baselines
    whose key contains one of these strings are included (case-insensitive).
    Result is intentionally capped so prompt size stays controlled."""
    lines = []

    # --- Baselines ---
    baselines = memory.get("baselines", {})
    if baselines:
        relevant = []
        for key, b in sorted(baselines.items(), key=lambda kv: kv[1].get("updated", ""), reverse=True):
            if cities:
                dest_part = key.split("|")[0]
                if not any(c.lower() in dest_part.lower() for c in cities):
                    continue
            price = b.get("realistic_price_eur")
            note  = b.get("note", "").strip()
            entry = f"  {key}: realistic ~€{price}/night"
            if note:
                entry += f" — {note}"
            relevant.append(entry)
            if len(relevant) >= MAX_PROMPT_BASELINES:
                break
        if relevant:
            lines.append("Known realistic prices (from past verifications):")
            lines.extend(relevant)

    # --- Recent outcomes that carry calibration signal (skip its confirms/diamonds — a
    # diamond needs no warning; the misses, corrections and mediocre scores teach the most).
    ledger = memory.get("ledger", [])
    recent_bad = sorted(
        [e for e in ledger if e.get("verdict") in
         ("correct", "kill", "hallucinated", "skeptic_kill", "skip", "blocked", "good")],
        key=lambda e: e.get("date", ""),
        reverse=True,
    )[:MAX_PROMPT_OUTCOMES]
    if recent_bad:
        if lines:
            lines.append("")
        lines.append("Recent outcomes (scores + corrections from past runs — calibrate to these):")
        for e in recent_bad:
            dest    = e.get("destination", "?")
            win     = e.get("window", "?")
            verdict = e.get("verdict", "?")
            actual  = e.get("actual_price")
            final   = e.get("final_score")
            llm     = e.get("llm_score")
            note    = e.get("note", "").strip()

            parts = [f"  {dest} ({win}): {verdict}"]
            if llm is not None and final is not None:
                parts.append(f"score {llm}->{final}")
            elif final is not None:
                parts.append(f"final {final}")
            if actual:
                parts.append(f"€{actual}/night")
            if note:
                parts.append(_clip(note, 120))
            lines.append(", ".join(parts))

    return "\n".join(lines) if lines else "(no prior memory)"


# ── human-readable digest ──────────────────────────────────────────────────────

def _write_md(memory):
    today = dt.date.today().isoformat()
    lines = [f"# Diamond Finder Memory — updated {today}", ""]

    baselines = memory.get("baselines", {})
    lines.append(f"## Price Baselines ({len(baselines)} entries)")
    lines.append("")
    if baselines:
        for key, b in sorted(baselines.items()):
            price   = b.get("realistic_price_eur")
            note    = b.get("note", "")
            updated = b.get("updated", "?")
            src     = b.get("source", "")
            lines.append(f"### {key}")
            lines.append(f"**Realistic:** ~€{price}/night &nbsp; **Updated:** {updated}")
            if note:
                lines.append(note)
            if src:
                lines.append(f"_Source: {src}_")
            lines.append("")
    else:
        lines.append("_No baselines recorded yet._")
        lines.append("")

    ledger = memory.get("ledger", [])
    lines.append(f"## Outcome Ledger ({len(ledger)} entries)")
    lines.append("")
    if ledger:
        recent = sorted(ledger, key=lambda e: e.get("date", ""), reverse=True)
        _icons = {"diamond": "💎", "good": "👍", "skip": "·", "confirm": "✅",
                  "correct": "🔧", "kill": "❌", "blocked": "🔒"}
        for e in recent[:50]:
            date    = e.get("date", "?")
            dest    = e.get("destination", "?")
            win     = e.get("window", "?")
            verdict = e.get("verdict", "?")
            claimed = e.get("claimed_price")
            actual  = e.get("actual_price")
            llm     = e.get("llm_score")
            final   = e.get("final_score")
            note    = e.get("note", "").strip()

            icon = _icons.get(verdict, "•")
            price_str = ""
            if claimed:
                price_str += f" claimed=€{claimed}"
            if actual:
                price_str += f" actual=€{actual}"
            score_str = ""
            if llm is not None and final is not None:
                score_str = f" score={llm}->{final}"
            elif final is not None:
                score_str = f" final={final}"
            suffix = f" — {_clip(note, 100)}" if note else ""
            lines.append(f"- {icon} {date} | {dest} | {win} | {verdict}{score_str}{price_str}{suffix}")
        if len(ledger) > 50:
            lines.append(f"_... and {len(ledger) - 50} earlier entries_")
    else:
        lines.append("_No outcomes recorded yet._")

    with open(_path(_MEMORY_MD), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
