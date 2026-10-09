"""Turns stored snapshots into things worth acting on."""
import re
import statistics
import time
from datetime import datetime, timezone

from . import config, db

DAY = 86400
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

CONSUMABLE_TYPES = {
    "Drug", "Medical", "Candy", "Alcohol", "Energy Drink", "Booster",
    "Supply Pack", "Enhancer", "Temporary",
}
EVENT_WORDS = (
    "halloween", "pumpkin", "christmas", "xmas", "santa", "easter", "egg",
    "valentine", "spooky", "ghost", "trick", "treat", "event", "anniversary",
)

# From the Halloween guide Bluemojo shared (Romarocky's 2025 guide).
SPOOKY_CLOTHES = {
    "Kabuki Mask", "Medieval Helmet", "Witch's Hat", "Straitjacket", "Marauder Face Mask",
    "Duke's Gimp Mask", "Ginger Kid Mask", "Scarred Man Mask", "Psycho Clown Mask", "Nun Mask",
    "Hook Hand", "Torso Bandage", "Head Bandage", "Bloody Apron", "Devil Horn", "Peg Leg",
}
# Yearly event masks are named like "Pennywise Mask '20"; all of those count too.
SPOOKY_MASK_RE = re.compile(r"mask '\d{2}$", re.I)
SPOOKY_WEAPONS = {
    "Axe", "Blood Spattered Sickle", "Chainsaw", "Devil's Pitchfork", "Golden Broomstick",
    "Petrified Humerus", "Wand of Destruction", "Meat Hook", "Cleaver", "Bone Saw",
    "Crystalline Falcata",
}

# What each calendar event tends to mean for prices. Plain-language hints, not guarantees.
EVENT_HINTS = {
    "trick or treat": "Halloween. Energy items and spooky gear are in demand before it; "
                      "Halloween candy floods the market after it.",
    "caffeinecon": "Energy drinks work twice as well for a day, so demand for them rises.",
    "diabetes": "Candy works three times as well for a day. Candy prices tend to rise before it.",
    "christmas": "Christmas items enter the game. Energy drinks and boosters are usually "
                 "cheapest just after it.",
    "easter": "Easter eggs appear. Some (like the green egg) are valuable energy items.",
    "black friday": "Many bazaars list goods for $1. Be online and quick.",
    "valentine": "Love Juice lowers the energy cost of attacking.",
    "st patrick": "Alcohol effects doubled. Alcohol demand rises.",
    "population": "Double experience from attacking. Great for levelling.",
    "employee appreciation": "Triple job points and training effects. Save energy for the gym.",
    "tourism": "Double travel capacity. Best day for overseas trading.",
    "museum": "10% bonus on museum point rewards (plushie and flower sets).",
}


# ---------------- helpers ----------------
def _now():
    return int(time.time())


def month_now():
    return datetime.now(timezone.utc).month


def parse_months(s):
    if not s:
        return []
    out = []
    for part in str(s).split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= 12:
            out.append(int(part))
    return sorted(set(out))


def months_label(ms):
    return ", ".join(MONTHS[m - 1] for m in ms)


def value_series(item_id, days=90):
    """Daily market value history we've recorded for an item: [[ts, value], ...]."""
    since = _now() - days * DAY
    return [[r["ts"], r["market_value"]] for r in db.query(
        "SELECT ts, market_value FROM item_snapshots WHERE item_id=? AND ts>=? ORDER BY ts",
        (item_id, since))]


def _pct(new, old):
    if not new or not old:
        return None
    return (new - old) / old * 100


def value_change(item_id, days):
    series = value_series(item_id, days + 1)
    if len(series) < 2:
        return None
    return _pct(series[-1][1], series[0][1])


def spendable(money):
    m = money or {}
    return (m.get("wallet") or 0) + (m.get("vault") or 0)


def season_status(buy_months, sell_months, month=None):
    month = month or month_now()
    if month in sell_months:
        return "sell"
    if month in buy_months:
        return "buy"
    return None


# ---------------- events ----------------
def upcoming_events(calendar, days_ahead=75):
    now = _now()
    out = []
    for e in (calendar or {}).get("events", []):
        start, end = e.get("start") or 0, e.get("end") or 0
        if end < start:            # Torn occasionally returns odd ranges for fixed events
            end = start + DAY
        if end < now or start > now + days_ahead * DAY:
            continue
        title = e.get("title") or ""
        hint = next((h for k, h in EVENT_HINTS.items() if k in title.lower()), None)
        out.append({
            "title": title, "description": e.get("description"),
            "start": start, "end": end, "ongoing": start <= now <= end,
            "days_until": max(0, (start - now) // DAY), "hint": hint,
        })
    out.sort(key=lambda e: e["start"])
    return out


def _find_event(events, word):
    return next((e for e in events if word in e["title"].lower()), None)


# ---------------- Personal ----------------
def urgent_actions(bars, cooldowns, money, education, profile, equipment=None, events=None):
    """Plain-language to-do list, most urgent first."""
    out = []
    now = _now()
    status = (profile or {}).get("status") or {}
    state = status.get("state")
    if state and state != "Okay":
        out.append({"level": "info", "text": f"You're {status.get('description') or state}.",
                    "why": "While in hospital, jail or travelling you can't train or attack."})

    if bars:
        e, n, h = bars.get("energy", {}), bars.get("nerve", {}), bars.get("happy", {})
        if e.get("current", 0) >= e.get("maximum", 1):
            out.append({"level": "high", "text": "Your energy is full. Use it at the gym or on attacks.",
                        "why": "Energy stops refilling once it's full, so every minute at full is wasted."})
        elif 0 < e.get("full_time", 99999) < 1800:
            out.append({"level": "med", "text": f"Your energy will be full in {e['full_time'] // 60} minutes.",
                        "why": "Plan to spend it soon so none goes to waste."})
        if n.get("current", 0) >= n.get("maximum", 1):
            out.append({"level": "high", "text": "Your nerve is full. Do some crimes.",
                        "why": "Nerve is what crimes cost. Like energy, it stops refilling when full."})
        if h.get("current", 0) > h.get("maximum", 1):
            out.append({"level": "med", "text": "Your happiness is above its normal maximum. Train now.",
                        "why": "Higher happiness gives bigger gym gains, and the bonus wears off."})

    if education is not None:
        cur = (education or {}).get("current")
        if not cur:
            out.append({"level": "high", "text": "You're not taking a course. Start one in Education.",
                        "why": "Courses unlock permanent bonuses and cost very little. Keep one running."})
        elif cur.get("until") and cur["until"] - now < 3600:
            out.append({"level": "med", "text": "Your course finishes within the hour. Pick the next one.",
                        "why": "Starting the next course straight away keeps your progress going."})

    if money:
        wallet = money.get("wallet") or 0
        if wallet > config.WALLET_WARN:
            out.append({"level": "high", "text": f"You're carrying ${wallet:,}. Put it somewhere safe.",
                        "why": "Other players can mug you and take a share of your cash in hand. "
                               "Money in the bank can't be stolen."})

    for piece in (equipment or {}).get("equipment", []):
        ammo = piece.get("ammo")
        if piece.get("slot") in (1, 2) and isinstance(ammo, dict) and not ammo.get("quantity"):
            out.append({"level": "med", "text": f"Your {piece['name']} has no ammo.",
                        "why": "A gun without ammo does nothing in a fight. Buy ammo or rely on your melee weapon."})

    events = events or []
    level = (profile or {}).get("level") or 0
    tot = _find_event(events, "trick or treat")
    if tot and not tot["ongoing"] and level < 5:
        out.append({"level": "med",
                    "text": f"Reach level 5 before Halloween starts in {tot['days_until']} days.",
                    "why": "You need level 5 to buy the Halloween basket, and without a basket you can't collect treats."})
    for e in events:
        if not e["ongoing"] and e["days_until"] <= 2 and e["hint"]:
            out.append({"level": "info", "text": f"{e['title']} starts in {e['days_until'] or 'under 1'} day(s).",
                        "why": e["hint"]})

    if cooldowns and cooldowns.get("booster", 1) == 0:
        out.append({"level": "low", "text": "Your booster cooldown is clear.",
                    "why": "You can use a booster item (like an energy drink or candy) without waiting."})
    order = {"high": 0, "med": 1, "info": 2, "low": 3}
    out.sort(key=lambda x: order[x["level"]])
    return out


def halloween_readiness(pid, profile, equipment, events):
    tot = _find_event(events or [], "trick or treat")
    level = (profile or {}).get("level") or 0
    names = {p.get("name") for p in (equipment or {}).get("equipment", [])}
    names |= {c.get("name") for c in (equipment or {}).get("clothing", [])}
    names |= {r["name"] for r in db.query("SELECT name FROM inventory WHERE player_id=?", (pid,))}
    names.discard(None)
    clothes = sorted(n for n in names if n in SPOOKY_CLOTHES or SPOOKY_MASK_RE.search(n))
    weapons = sorted(n for n in names if n in SPOOKY_WEAPONS)
    return {
        "event": tot,
        "checks": [
            {"done": level >= 5, "text": "Reach level 5",
             "why": "Level 5 is required to buy the Halloween basket."},
            {"done": bool(clothes), "text": "Own one scary clothing item" + (f" ({clothes[0]})" if clothes else ""),
             "why": "Wearing one gives +10% chance of a treat per win. Masks, the Witch's Hat and similar items count. Only one counts."},
            {"done": bool(weapons), "text": "Own a scary weapon" + (f" ({weapons[0]})" if weapons else ""),
             "why": "+10% treat chance when your final hit is with it. Cheap options include the Axe or Cleaver."},
            {"done": False, "text": "Buy the basket from Sally's Sweet Shop when the event opens ($35)",
             "why": "Without the basket you earn no treats. They restock 1,000 every 15 minutes if sold out."},
            {"done": False, "text": "Save energy for the event and check Torn time (TCT)",
             "why": "The event is all about attacking, so don't spend energy just before it starts."},
        ],
        "first_year_goal": "Aim for a Terrifying basket (440 treats). Don't exchange treats in your first year: upgrades pay off more later.",
    }


def next_move(urgent):
    if urgent:
        return urgent[0]["text"]
    return "You're all set. Nothing needs your attention right now."


def attack_log(pid, limit=50):
    return db.query("SELECT * FROM attacks WHERE player_id=? ORDER BY started DESC LIMIT ?", (pid, limit))


def attackers_summary(pid, days=30):
    """Who has attacked you recently, grouped."""
    since = _now() - days * DAY
    return db.query(
        """SELECT attacker_id AS id, attacker_name AS name, MAX(attacker_level) AS level,
                  COUNT(*) AS times, MAX(started) AS last
           FROM attacks WHERE player_id=? AND direction='in' AND started>=? AND attacker_id IS NOT NULL
           GROUP BY attacker_id ORDER BY times DESC, last DESC""",
        (pid, since),
    )


def recent_events(pid, limit=30):
    return db.query("SELECT * FROM events WHERE player_id=? ORDER BY ts DESC LIMIT ?", (pid, limit))


# ---------------- Faction ----------------
def faction_summary(f):
    if not f:
        return None
    members = f.get("members") or {}
    now = _now()
    online, idle, hospital, travelling, jail = [], [], [], [], []
    for mid, m in members.items():
        la = (m.get("last_action") or {}).get("status")
        st = (m.get("status") or {}).get("state")
        row = {"id": int(mid), "name": m.get("name"), "level": m.get("level"),
               "status": (m.get("status") or {}).get("description"),
               "until": (m.get("status") or {}).get("until"),
               "last": (m.get("last_action") or {}).get("timestamp")}
        if la == "Online":
            online.append(row)
        elif la == "Idle":
            idle.append(row)
        if st == "Hospital":
            hospital.append(row)
        elif st == "Traveling":
            travelling.append(row)
        elif st == "Jail":
            jail.append(row)

    chain = f.get("chain") or {}
    alerts = []
    if chain.get("current", 0) > 0 and 0 < chain.get("timeout", 0) <= 90:
        alerts.append(f"The chain at {chain['current']} hits ends in {chain['timeout']} seconds unless someone attacks.")
    if chain.get("cooldown", 0) > 0:
        alerts.append(f"Chain cooldown: {chain['cooldown'] // 60} minutes left.")

    return {
        "name": f.get("name"), "tag": f.get("tag"), "respect": f.get("respect"),
        "rank": f.get("rank"), "member_count": len(members),
        "best_chain": f.get("best_chain"),
        "chain": chain, "alerts": alerts,
        "online": sorted(online, key=lambda r: -(r["last"] or 0)),
        "idle": sorted(idle, key=lambda r: -(r["last"] or 0)),
        "hospital": sorted(hospital, key=lambda r: r["until"] or now),
        "travelling": travelling, "jail": jail,
    }


# ---------------- Trade ----------------
def _circ_change(item_id, days, now):
    old = db.one(
        "SELECT circulation FROM item_snapshots WHERE item_id=? AND ts<=? ORDER BY ts DESC LIMIT 1",
        (item_id, now - days * DAY + 3600),
    )
    cur = db.one(
        "SELECT circulation FROM item_snapshots WHERE item_id=? ORDER BY ts DESC LIMIT 1",
        (item_id,),
    )
    if not old or not cur or not old["circulation"]:
        return None
    return (cur["circulation"] - old["circulation"]) / old["circulation"] * 100


def is_event_item(name, desc):
    text = f"{name or ''} {desc or ''}".lower()
    return any(w in text for w in EVENT_WORDS)


def candidates(min_value=None, limit=50, budget=None):
    """Tradeable items with no known endless source, ranked by shrinking supply."""
    min_value = config.MIN_CANDIDATE_VALUE if min_value is None else min_value
    now = _now()
    rows = db.query(
        """SELECT i.item_id, i.name, i.type, i.description, s.circulation, s.market_value
           FROM items i JOIN item_snapshots s ON s.item_id=i.item_id
           WHERE i.tradeable=1 AND i.city_buy_price=0 AND i.abroad=0
             AND s.ts=(SELECT MAX(ts) FROM item_snapshots WHERE item_id=i.item_id)
             AND s.market_value>=?""",
        (min_value,),
    )
    out = []
    for r in rows:
        c7 = _circ_change(r["item_id"], 7, now)
        c30 = _circ_change(r["item_id"], 30, now)
        consumable = r["type"] in CONSUMABLE_TYPES
        event = is_event_item(r["name"], r["description"])
        score = 0.0
        trend = c30 if c30 is not None else c7
        if trend is not None:
            score += -trend * 10
        score += 15 if consumable else 0
        score += 10 if event else 0
        score += min(len(str(r["market_value"] or 0)), 10)
        out.append({
            "id": r["item_id"], "name": r["name"], "type": r["type"],
            "market_value": r["market_value"], "circulation": r["circulation"],
            "chg_7d": c7, "chg_30d": c30, "price_chg_30d": value_change(r["item_id"], 30),
            "consumable": consumable, "event": event, "score": round(score, 1),
            "affordable": None if budget is None else (r["market_value"] or 0) <= budget,
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


def refresh_auto_watchlists(only=None):
    """Keep the top-N candidates on every player's watchlist (their own entries are untouched)."""
    top = candidates(limit=config.WATCH_TOP_CANDIDATES)
    keep = [c["id"] for c in top]
    pids = [only] if only else [r["player_id"] for r in db.query("SELECT player_id FROM users")]
    now = _now()
    for pid in pids:
        if keep:
            db.execute("DELETE FROM watchlist WHERE player_id=? AND source='auto' AND item_id NOT IN (%s)"
                       % ",".join("?" * len(keep)), (pid, *keep))
        else:
            db.execute("DELETE FROM watchlist WHERE player_id=? AND source='auto'", (pid,))
        for c in top:
            db.execute(
                """INSERT OR IGNORE INTO watchlist (player_id, item_id, name, target_price, source, added)
                   VALUES (?,?,?,?,?,?)""", (pid, c["id"], c["name"], None, "auto", now))


def watchlist_with_prices(pid):
    now = _now()
    month = month_now()
    out = []
    for w in db.query("SELECT * FROM watchlist WHERE player_id=? ORDER BY source, name", (pid,)):
        hist = db.query(
            "SELECT ts, lowest FROM listings WHERE item_id=? AND ts>=? AND lowest IS NOT NULL ORDER BY ts",
            (w["item_id"], now - 7 * DAY),
        )
        latest = hist[-1]["lowest"] if hist else None
        median7 = statistics.median([h["lowest"] for h in hist]) if len(hist) >= 3 else None
        buy_m, sell_m = parse_months(w.get("buy_months")), parse_months(w.get("sell_months"))
        season = season_status(buy_m, sell_m, month)
        deal = None
        if latest and w["target_price"] and latest <= w["target_price"]:
            deal = "At or below your target price"
        elif latest and median7 and latest < median7 * config.DEAL_THRESHOLD:
            deal = f"{(1 - latest / median7) * 100:.0f}% cheaper than usual this week"
        out.append({
            **w, "lowest": latest, "median_7d": median7, "deal": deal,
            "buy_months": buy_m, "sell_months": sell_m,
            "buy_label": months_label(buy_m), "sell_label": months_label(sell_m),
            "season": season,
            "history": [[h["ts"], h["lowest"]] for h in hist[-200:]],
            "market_url": f"https://www.torn.com/page.php?sid=ItemMarket#/market/view=search&itemID={w['item_id']}",
        })
    out.sort(key=lambda x: (x["deal"] is None and x["season"] is None, x["name"] or ""))
    return out


def inventory_valued(pid):
    """What you own, valued at today's market price, with recent price movement."""
    rows = db.query(
        """SELECT v.item_id, v.name, v.type, SUM(v.amount) AS amount, MAX(v.equipped) AS equipped,
                  i.tradeable,
                  (SELECT market_value FROM item_snapshots s WHERE s.item_id=v.item_id
                     ORDER BY ts DESC LIMIT 1) AS market_value,
                  w.buy_months, w.sell_months, w.note
           FROM inventory v
           LEFT JOIN items i ON i.item_id=v.item_id
           LEFT JOIN watchlist w ON w.item_id=v.item_id AND w.player_id=v.player_id
           WHERE v.player_id=?
           GROUP BY v.item_id ORDER BY v.name""", (pid,))
    month = month_now()
    total = 0
    out = []
    for r in rows:
        value = (r["market_value"] or 0) * (r["amount"] or 0)
        total += value
        sell_m = parse_months(r.get("sell_months"))
        out.append({
            "item_id": r["item_id"], "name": r["name"], "type": r["type"],
            "amount": r["amount"], "equipped": bool(r["equipped"]),
            "tradeable": bool(r["tradeable"]), "unit_value": r["market_value"], "value": value,
            "price_chg_30d": value_change(r["item_id"], 30),
            "price_chg_90d": value_change(r["item_id"], 90),
            "in_sell_season": month in sell_m, "note": r.get("note"),
        })
    out.sort(key=lambda x: -(x["value"] or 0))
    return {"items": out, "total_value": total}


# ---------------- Price history ----------------
def _thin(points, max_points=400):
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    out = [points[int(i * step)] for i in range(max_points)]
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def history(item_id, days=30):
    """Everything recorded for one item: market value (daily + hourly logs), cheapest
    listing (watchlist checks + background sweep) and circulation."""
    since = 0 if days <= 0 else _now() - days * DAY
    item = db.one("SELECT item_id, name, type, city_buy_price, abroad, tradeable FROM items WHERE item_id=?",
                  (item_id,))
    mv = {}
    for r in db.query("SELECT ts, market_value FROM item_snapshots WHERE item_id=? AND ts>=?", (item_id, since)):
        if r["market_value"]:
            mv[r["ts"]] = r["market_value"]
    for r in db.query("SELECT ts, market_value FROM item_prices WHERE item_id=? AND ts>=?", (item_id, since)):
        mv[r["ts"]] = r["market_value"]
    value = sorted(mv.items())
    lowest = [(r["ts"], r["lowest"]) for r in db.query(
        "SELECT ts, lowest FROM listings WHERE item_id=? AND ts>=? AND lowest IS NOT NULL ORDER BY ts",
        (item_id, since))]
    circ = [(r["ts"], r["circulation"]) for r in db.query(
        "SELECT ts, circulation FROM item_snapshots WHERE item_id=? AND ts>=? ORDER BY ts", (item_id, since))]

    def stats(series):
        if not series:
            return None
        vals = [v for _, v in series]
        return {"first": vals[0], "last": vals[-1], "min": min(vals), "max": max(vals),
                "change_pct": _pct(vals[-1], vals[0]), "points": len(vals),
                "since": series[0][0]}

    return {
        "item": item,
        "value": [list(p) for p in _thin(value)],
        "lowest": [list(p) for p in _thin(lowest)],
        "circulation": [list(p) for p in _thin(circ, 200)],
        "value_stats": stats(value), "lowest_stats": stats(lowest), "circulation_stats": stats(circ),
    }


def logging_status():
    sweep, _ = db.get("sweep", {})
    prices, prices_ts = db.get("prices_logged", {})
    counts = db.one("""SELECT (SELECT COUNT(*) FROM item_prices) AS value_points,
                              (SELECT COUNT(*) FROM listings) AS listing_points,
                              (SELECT COUNT(DISTINCT item_id) FROM listings) AS items_with_listings,
                              (SELECT COUNT(DISTINCT ts / 86400) FROM item_snapshots) AS days""") or {}
    return {**counts, "sweep": sweep or {}, "prices_checked": prices_ts,
            "catalogue_items": (prices or {}).get("items")}
