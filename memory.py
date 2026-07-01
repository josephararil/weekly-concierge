"""memory.py — self-improving memory for the weekend concierge.

state/memory.json structure:
  evergreen: {name → {location, area, description, tags, last_suggested, discovered, source}}
  ledger:    [{date, title, category, when, location, url, score, verdict, note}]

Ledger is capped to MAX_LEDGER_ENTRIES entries and MAX_LEDGER_DAYS days.
summarize_for_prompt() produces a compact, bounded text block for prompt injection.
"""

import json, datetime as dt, os

STATE_DIR = "state"
_MEMORY_FILE = "memory.json"
_MEMORY_MD   = "memory.md"

MAX_LEDGER_ENTRIES     = 200    # hard cap on ledger rows
MAX_LEDGER_DAYS         = 180   # TTL for ledger entries
MAX_PROMPT_EVERGREENS   = 10    # off-cooldown evergreens injected per prompt
MAX_PROMPT_SUGGESTIONS  = 10    # recent suggestions injected per prompt
EVERGREEN_COOLDOWN_DAYS = 70    # an evergreen is off-cooldown once this many days have passed


def _path(name):
    return os.path.join(STATE_DIR, name)


def _clip(text, limit):
    """Clip text at the last word boundary before limit, appending an ellipsis."""
    if not text or len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


# ── load / save ────────────────────────────────────────────────────────────────

def load():
    """Load memory from state/memory.json. Returns a fresh dict on missing/corrupt file."""
    try:
        with open(_path(_MEMORY_FILE), encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("evergreen", {})
        data.setdefault("ledger", [])
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"evergreen": {}, "ledger": []}


def save(memory):
    """Save memory to state/memory.json and write state/memory.md digest."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_path(_MEMORY_FILE), "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2, ensure_ascii=False)
    _write_md(memory)


# ── write ──────────────────────────────────────────────────────────────────────

def record_evergreen(memory, name, location="", area="", description="", tags=None, source="", suggested=False):
    """Upsert an evergreen catalog entry, preserving fields not passed this call.

    Set suggested=True to bump last_suggested to today — call this when the item is
    actually included in an email, so the anti-repeat cooldown has something to check."""
    existing = memory["evergreen"].get(name, {})
    memory["evergreen"][name] = {
        "location":       location or existing.get("location", ""),
        "area":           area or existing.get("area", ""),
        "description":    description or existing.get("description", ""),
        "tags":           tags if tags is not None else existing.get("tags", []),
        "discovered":     existing.get("discovered", dt.date.today().isoformat()),
        "source":         source or existing.get("source", ""),
        "last_suggested": dt.date.today().isoformat() if suggested else existing.get("last_suggested"),
    }


def record_suggestion(memory, title, category, when, location="", url="", score=None, verdict="", note=""):
    """Append one candidate's outcome to the rolling ledger.

    category: event_this_weekend | event_lookahead | evergreen
    verdict:  sent | killed | corrected | skipped ..."""
    memory["ledger"].append({
        "date":     dt.date.today().isoformat(),
        "title":    title,
        "category": category,
        "when":     when,
        "location": location,
        "url":      url,
        "score":    score,
        "verdict":  verdict,
        "note":     note,
    })


def prune(memory):
    """Drop ledger entries older than MAX_LEDGER_DAYS or beyond MAX_LEDGER_ENTRIES."""
    cutoff = (dt.date.today() - dt.timedelta(days=MAX_LEDGER_DAYS)).isoformat()
    memory["ledger"] = [e for e in memory["ledger"] if e.get("date", "") >= cutoff]
    if len(memory["ledger"]) > MAX_LEDGER_ENTRIES:
        memory["ledger"] = memory["ledger"][-MAX_LEDGER_ENTRIES:]
    return memory


# ── prompt summary ─────────────────────────────────────────────────────────────

def summarize_for_prompt(memory):
    """Return a compact text block for injection into FIND/CONCIERGE prompts:
    off-cooldown evergreens (safe to propose again) plus recent suggestions for
    calibration. Result is intentionally capped so prompt size stays controlled."""
    lines = []
    cutoff = (dt.date.today() - dt.timedelta(days=EVERGREEN_COOLDOWN_DAYS)).isoformat()

    # --- Off-cooldown evergreens ---
    evergreen = memory.get("evergreen", {})
    if evergreen:
        off_cooldown = []
        for name, e in sorted(evergreen.items()):
            last = e.get("last_suggested")
            if last and last >= cutoff:
                continue
            area = e.get("area", "").strip()
            desc = e.get("description", "").strip()
            entry = f"  {name}" + (f" ({area})" if area else "")
            if desc:
                entry += f" — {_clip(desc, 140)}"
            off_cooldown.append(entry)
            if len(off_cooldown) >= MAX_PROMPT_EVERGREENS:
                break
        if off_cooldown:
            lines.append("Evergreen ideas off cooldown (safe to suggest again):")
            lines.extend(off_cooldown)

    # --- Recent suggestions ---
    ledger = memory.get("ledger", [])
    recent = sorted(ledger, key=lambda e: e.get("date", ""), reverse=True)[:MAX_PROMPT_SUGGESTIONS]
    if recent:
        if lines:
            lines.append("")
        lines.append("Recently suggested (avoid repeating unless still upcoming):")
        for e in recent:
            title   = e.get("title", "?")
            when    = e.get("when", "?")
            verdict = e.get("verdict", "?")
            note    = e.get("note", "").strip()
            entry = f"  {title} ({when}): {verdict}"
            if note:
                entry += f" — {_clip(note, 120)}"
            lines.append(entry)

    return "\n".join(lines) if lines else "(no prior memory)"


# ── human-readable digest ──────────────────────────────────────────────────────

def _write_md(memory):
    today = dt.date.today().isoformat()
    lines = [f"# Weekend Concierge Memory — updated {today}", ""]

    evergreen = memory.get("evergreen", {})
    lines.append(f"## Evergreen Catalog ({len(evergreen)} entries)")
    lines.append("")
    if evergreen:
        for name, e in sorted(evergreen.items()):
            location = e.get("location", "")
            area     = e.get("area", "")
            desc     = e.get("description", "")
            tags     = e.get("tags", [])
            discovered = e.get("discovered", "?")
            last_suggested = e.get("last_suggested") or "never"
            src      = e.get("source", "")
            lines.append(f"### {name}")
            loc_bits = [b for b in (location, area) if b]
            if loc_bits:
                lines.append(f"**Location:** {' / '.join(loc_bits)}")
            lines.append(f"**Discovered:** {discovered} &nbsp; **Last suggested:** {last_suggested}")
            if desc:
                lines.append(desc)
            if tags:
                lines.append(f"_Tags: {', '.join(tags)}_")
            if src:
                lines.append(f"_Source: {src}_")
            lines.append("")
    else:
        lines.append("_No evergreen ideas recorded yet._")
        lines.append("")

    ledger = memory.get("ledger", [])
    lines.append(f"## Suggestion Ledger ({len(ledger)} entries)")
    lines.append("")
    if ledger:
        recent = sorted(ledger, key=lambda e: e.get("date", ""), reverse=True)
        _icons = {"sent": "✅", "killed": "❌", "corrected": "🔧", "skipped": "·"}
        for e in recent[:50]:
            date     = e.get("date", "?")
            title    = e.get("title", "?")
            category = e.get("category", "?")
            when     = e.get("when", "?")
            verdict  = e.get("verdict", "?")
            score    = e.get("score")
            note     = e.get("note", "").strip()

            icon = _icons.get(verdict, "•")
            score_str = f" score={score}" if score is not None else ""
            suffix = f" — {_clip(note, 100)}" if note else ""
            lines.append(f"- {icon} {date} | {title} | {category} | {when} | {verdict}{score_str}{suffix}")
        if len(ledger) > 50:
            lines.append(f"_... and {len(ledger) - 50} earlier entries_")
    else:
        lines.append("_No suggestions recorded yet._")

    with open(_path(_MEMORY_MD), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
