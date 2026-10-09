"""Builds one timeline of everything with a time attached: what Torn reports through the
API, plus the player's own reminders. Nothing here is typed in by hand except reminders."""
import html
import re
import time
from datetime import datetime, timedelta, timezone

from . import analytics, db

DAY = 86400

# kind -> (label shown in settings, notify by default, notify this many seconds before due)
KINDS = {
    "reminder":  ("Your reminders", True, 0),
    "rent":      ("Rent ending", True, DAY),
    "education": ("Course finished", True, 0),
    "bank":      ("Bank investment matured", True, 0),
    "travel":    ("Landed from travel", True, 0),
    "status":    ("Out of hospital or jail", True, 0),
    "event":     ("Torn events starting", True, 6 * 3600),
    "oc":        ("Organised crime ready", True, 0),
    "faction":   ("Faction timers", True, 0),
    "company":   ("Company timers", True, 0),
    "energy":    ("Energy full", False, 0),
    "nerve":     ("Nerve full", False, 0),
    "drug":      ("Drug cooldown over", False, 0),
    "booster":   ("Booster cooldown over", False, 0),
    "medical":   ("Medical cooldown over", False, 0),
    "mission":   ("Mission rewards expiring", False, 12 * 3600),
    "refill":    ("Daily refill unused", False, 0),
    "other":     ("Other Torn timers", True, 0),
}

_UNITS = {"day": DAY, "hour": 3600, "minute": 60, "second": 1}
_DUR = re.compile(r"(\d+)\s+(day|hour|minute|second)s?")
_CLOCK = re.compile(r"(\d+):(\d\d):(\d\d)\s*/\s*(\d+):(\d\d):(\d\d)")

# Icons whose timers come from a better, structured source.
_ICON_SKIP = ("education", "drug cooldown", "booster cooldown", "medical cooldown",
              "hospital", "jail", "traveling", "travelling")
_ICON_KIND = {"bank investment": "bank", "faction": "faction", "company": "company",
              "organized crime": "oc", "organised crime": "oc"}


def parse_countdown(text):
    """'4 days, 21 hours, 1 minute and 27 seconds' -> seconds, or None."""
    found = _DUR.findall(text or "")
    if not found:
        return None
    return sum(int(n) * _UNITS[u] for n, u in found)


def parse_icon(text):
    """'Bank Investment - Current bank investment worth $5,035 - 6 days, ...' ->
    (label, detail, seconds) or None when the icon has no countdown."""
    parts = [p.strip() for p in html.unescape(text or "").split(" - ")]
    if len(parts) < 2:
        return None
    secs = parse_countdown(parts[-1])
    if secs is None:
        return None
    return parts[0], " - ".join(parts[1:-1]), secs


def parse_clock(text):
    """'20:13:49 / 24:00:00' -> seconds left, or None."""
    m = _CLOCK.search(text or "")
    if not m:
        return None
    h, mi, se = (int(x) for x in m.groups()[:3])
    return h * 3600 + mi * 60 + se


def cooldowns(pid, now=None):
    """Drug, booster and medical cooldowns as {kind: {"until": ts, "detail": str|None}}.

    Uses the cooldowns endpoint and the profile icons, taking whichever reports longer,
    so a cooldown still shows if one source is missing or stale."""
    now = now or int(time.time())
    out = {}
    cds, cds_ts = db.uget(pid, "cooldowns")
    for k in ("drug", "booster", "medical"):
        secs = (cds or {}).get(k) or 0
        if secs > 0 and (cds_ts or now) + secs > now:
            out[k] = {"until": (cds_ts or now) + secs, "detail": None}
    it, it_ts = db.uget(pid, "icons_travel")
    for text in ((it or {}).get("icons") or {}).values():
        parts = [x.strip() for x in html.unescape(str(text)).split(" - ")]
        low = parts[0].lower()
        k = next((k for k in ("drug", "booster", "medical") if low.startswith(f"{k} cooldown")), None)
        if not k or len(parts) < 2:
            continue
        secs = parse_clock(parts[-1])
        if secs is None:
            secs = parse_countdown(parts[-1])
        detail = " - ".join(parts[1:-1]) or None
        if secs:
            until = (it_ts or now) + secs
            if until > now and until > out.get(k, {}).get("until", 0):
                out[k] = {"until": until, "detail": detail}
        if detail and k in out:
            out[k]["detail"] = detail
    return out


def icon_title(kind, label, detail):
    """Turn an icon's label and text into a plain title and detail."""
    if kind == "bank":
        return "Bank investment matures", detail.replace("Current bank investment worth", "Worth").strip()
    if kind == "faction" and detail.lower().startswith("recruit of"):
        return "Faction recruit period ends", detail[11:].strip() or None
    if detail:
        return f"{label}: {detail}", None
    return label, None


def _item(key, kind, title, due, detail=None, url=None, source="torn", **extra):
    return {"key": key, "kind": kind, "title": title, "detail": detail, "due": int(due) if due else None,
            "url": url, "source": source, **extra}


def next_utc_midnight(now):
    d = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((d + timedelta(days=1)).timestamp())


def _next_repeat(due, repeat, every_days):
    if repeat == "daily":
        return due + DAY
    if repeat == "weekly":
        return due + 7 * DAY
    if repeat == "monthly":
        d = datetime.fromtimestamp(due, timezone.utc)
        month = d.month % 12 + 1
        year = d.year + (d.month == 12)
        day = min(d.day, 28)
        return int(d.replace(year=year, month=month, day=day).timestamp())
    if repeat == "every" and every_days:
        return due + int(every_days) * DAY
    return None


def advance_reminder(r):
    """A finished repeating reminder rolls forward to its next date after now."""
    nxt = _next_repeat(r["due"], r.get("repeat"), r.get("every_days"))
    now = time.time()
    while nxt and nxt <= now:
        nxt = _next_repeat(nxt, r.get("repeat"), r.get("every_days"))
    return nxt


def is_hidden(item, hidden):
    """Hidden entries are 'key@due', so hiding one cooldown or refill doesn't hide the next one.
    Plain keys (from older versions) hide that key for good."""
    for h in hidden or []:
        key, _, due = h.partition("@")
        if key != item["key"]:
            continue
        if not due or not item.get("due") or abs(int(due) - item["due"]) < 2 * 3600:
            return True
    return False


def build(pid, include_tips=True):
    now = int(time.time())
    g = lambda k: db.uget(pid, k)
    items = []

    profile, _ = g("profile")
    profile = profile or {}
    status = profile.get("status") or {}
    if status.get("state") in ("Hospital", "Jail") and status.get("until"):
        items.append(_item("status", "status", f"Out of {status['state'].lower()}", status["until"],
                           status.get("description")))

    bars, bars_ts = g("bars")
    for bar in ("energy", "nerve"):
        b = (bars or {}).get(bar) or {}
        if b and b.get("current", 0) < b.get("maximum", 0) and b.get("full_time"):
            items.append(_item(bar, bar, f"{bar.title()} full", (bars_ts or now) + b["full_time"],
                               f"{b['current']} of {b['maximum']} now"))

    for cd, c in cooldowns(pid, now).items():
        items.append(_item(cd, cd, f"{cd.title()} cooldown over", c["until"],
                           c["detail"] or f"You can use another {cd} item after this"))

    edu, _ = g("education")
    cur = (edu or {}).get("current")
    if cur and cur.get("until"):
        items.append(_item("education", "education", "Course finished", cur["until"],
                           "Start the next one straight away", "https://www.torn.com/page.php?sid=education"))

    it, it_ts = g("icons_travel")
    it = it or {}
    travel = it.get("travel") or {}
    if travel.get("time_left"):
        items.append(_item("travel", "travel", f"Land in {travel.get('destination', 'Torn')}",
                           (it_ts or now) + travel["time_left"], travel.get("method")))
    for icon_id, text in (it.get("icons") or {}).items():
        parsed = parse_icon(text)
        if not parsed:
            continue
        label, detail, secs = parsed
        low = label.lower()
        if any(low.startswith(s) for s in _ICON_SKIP):
            continue
        kind = next((k for name, k in _ICON_KIND.items() if low.startswith(name)), "other")
        title, info = icon_title(kind, label, detail)
        items.append(_item(f"icon:{icon_id}", kind, title, (it_ts or now) + secs, info))

    props, props_ts = g("properties")
    for p in props or []:
        if (p.get("rented_by") or {}).get("id") == pid and p.get("rental_period_remaining") is not None:
            name = (p.get("property") or {}).get("name", "Property")
            due = (props_ts or now) + int(p["rental_period_remaining"]) * DAY
            items.append(_item(f"rent:{p.get('id')}", "rent", f"Rent ends: {name}", due,
                               f"${p.get('cost_per_day', 0):,}/day rental", "https://www.torn.com/properties.php"))

    missions, _ = g("missions")
    if missions:
        credits = missions.get("credits") or 0
        for r in missions.get("rewards") or []:
            if r.get("expires_at") and (r.get("cost") or 0) <= credits:
                name = (r.get("details") or {}).get("name") or r.get("type", "Reward")
                items.append(_item(f"mission:{r.get('expires_at')}:{name}", "mission",
                                   f"Mission reward expires: {name}", r["expires_at"],
                                   f"{r.get('amount', 1)}× for {r.get('cost')} credits (you have {credits})",
                                   "https://www.torn.com/page.php?sid=missions"))

    refills, _ = g("refills")
    money, _ = g("money")
    if refills and (money or {}).get("points", 0) > 0:
        unused = [k for k in ("energy", "nerve") if refills.get(k) is False]
        if unused:
            items.append(_item("refill", "refill", f"Unused {' and '.join(unused)} refill today",
                               next_utc_midnight(now), "Resets at midnight Torn time",
                               "https://www.torn.com/page.php?sid=points"))

    oc, _ = g("organizedcrime")
    if isinstance(oc, dict):
        ready = oc.get("ready_at") or oc.get("planned_at")
        if ready:
            items.append(_item("oc", "oc", f"Organised crime: {oc.get('name', 'ready')}", ready,
                               oc.get("status"), "https://www.torn.com/factions.php?step=your#/tab=crimes"))

    calendar, _ = db.get("calendar")
    for e in analytics.upcoming_events(calendar, days_ahead=45):
        items.append(_item(f"event:{e['title']}:{e['start']}", "event", e["title"],
                           e["end"] if e["ongoing"] else e["start"], e.get("hint") or e.get("description"),
                           ongoing=e["ongoing"]))

    # The player's own reminders, plus faction reminders they can see.
    fid = ((profile.get("faction") or {}).get("faction_id")) or 0
    rows = db.query("""SELECT * FROM reminders WHERE done=0 AND
                       ((scope='me' AND owner=?) OR (scope='faction' AND owner=? AND owner<>0))""", (pid, fid))
    for r in rows:
        items.append(_item(f"rem:{r['id']}", "reminder", r["title"], r["due"], r.get("note"), r.get("link"),
                           source="you" if r["scope"] == "me" else "faction", id=r["id"],
                           repeat=r.get("repeat"), scope=r["scope"]))

    items = [i for i in items if not is_hidden(i, g("timeline_hidden")[0])]

    if include_tips:
        equipment, _ = g("equipment")
        cds, _ = g("cooldowns")
        tips = analytics.urgent_actions(bars, cds, money, edu, profile, equipment, [])
        for n, t in enumerate(tips):
            if t["level"] in ("high", "med"):
                items.append(_item(f"tip:{n}:{t['text']}", "tip", t["text"], now, t.get("why"), level=t["level"]))

    for i in items:
        i["now"] = bool(i["due"] and i["due"] <= now) or bool(i.get("ongoing"))
    items.sort(key=lambda i: (not i["now"] or i["kind"] != "tip", i["due"] or now))
    return items


def prefs(pid):
    saved, _ = db.uget(pid, "notify_prefs")
    saved = saved or {}
    return {k: bool(saved.get(k, default)) for k, (_, default, _) in KINDS.items()}
