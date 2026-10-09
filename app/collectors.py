"""Shared market collectors. They use the server's key (TORN_KEY, or the owner's key if
that isn't set) and build the price history every player sees."""
import asyncio
import logging
import sqlite3
import time
from pathlib import Path

from . import config, db, users
from .analytics import refresh_auto_watchlists
from .torn import TornClient, TornError

log = logging.getLogger("collectors")

COLLECTORS = ["items", "prices", "sweep", "watchlist"]


def server_key():
    if config.TORN_KEY:
        return config.TORN_KEY
    oid = users.owner_id()
    return users.torn_key(oid) if oid else None


async def _loop(name, every, fn, get_client):
    while True:
        t = get_client()
        if t is None:
            db.put(f"status:{name}", {"ok": False, "error": "No server key yet. Set TORN_KEY or sign in as the owner."})
        else:
            try:
                await fn(t)
                db.put(f"status:{name}", {"ok": True, "error": None})
            except TornError as e:
                log.warning("%s: %s", name, e)
                db.put(f"status:{name}", {"ok": False, "error": str(e), "code": e.code})
            except Exception as e:  # keep the loop alive on network blips
                log.exception("%s failed", name)
                db.put(f"status:{name}", {"ok": False, "error": repr(e)})
        await asyncio.sleep(every if t else 60)


def prune(now):
    """Keep detail for PRUNE_AFTER_DAYS, then one point per item per day, so the
    database stays small on the Pi (daily snapshots already cover the long term)."""
    cutoff = now - config.PRUNE_AFTER_DAYS * 86400
    db.execute("DELETE FROM item_prices WHERE ts < ?", (cutoff,))
    db.execute("""DELETE FROM listings WHERE ts < ? AND ts NOT IN
                  (SELECT MAX(l2.ts) FROM listings l2 WHERE l2.item_id = listings.item_id
                   GROUP BY l2.ts / 86400)""", (cutoff,))


async def record_listing(t, item_id):
    m = await t.itemmarket(item_id, limit=20)
    lst = m.get("listings") or []
    lowest = lst[0] if lst else {}
    db.execute(
        "INSERT OR REPLACE INTO listings VALUES (?,?,?,?,?,?)",
        (item_id, int(time.time()), lowest.get("price"), lowest.get("amount"),
         (m.get("item") or {}).get("average_price"), len(lst)),
    )


async def items(t):
    catalogue = await t.all_items()
    abroad = await t.abroad_item_ids()
    now = int(time.time())
    db.executemany(
        "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?,?)",
        [(int(i), it.get("name"), it.get("type"), it.get("description"),
          int(bool(it.get("tradeable"))), it.get("buy_price") or 0, int(int(i) in abroad))
         for i, it in catalogue.items()],
    )
    db.executemany(
        "INSERT OR REPLACE INTO item_snapshots VALUES (?,?,?,?)",
        [(int(i), now, it.get("circulation") or 0, it.get("market_value") or 0)
         for i, it in catalogue.items()],
    )
    # One snapshot per day: restarts take a fresh one, so keep only the latest of each UTC day.
    db.execute("""DELETE FROM item_snapshots WHERE ts NOT IN
                  (SELECT MAX(ts) FROM item_snapshots GROUP BY ts / 86400)""")
    prune(now)
    refresh_auto_watchlists()
    db.put("calendar", await t.calendar())


async def prices(t):
    """Market value of every item, hourly. One call covers the whole catalogue;
    only values that changed since the last check are stored."""
    catalogue = await t.all_items()
    now = int(time.time())
    last, _ = db.get("last_market_values", {})
    last = last or {}
    rows = []
    for i, it in catalogue.items():
        mv = it.get("market_value") or 0
        if mv and last.get(i) != mv:
            rows.append((int(i), now, mv))
            last[i] = mv
    db.executemany("INSERT OR REPLACE INTO item_prices VALUES (?,?,?)", rows)
    db.put("last_market_values", last)
    db.put("prices_logged", {"changed": len(rows), "items": len(catalogue)})


async def sweep(t):
    """Cheapest item-market listing for one item per tick, cycling through every
    tradeable item worth at least SWEEP_MIN_VALUE."""
    ids = [r["item_id"] for r in db.query(
        """SELECT i.item_id FROM items i JOIN item_snapshots s ON s.item_id=i.item_id
           WHERE i.tradeable=1 AND s.market_value>=?
             AND s.ts=(SELECT MAX(ts) FROM item_snapshots WHERE item_id=i.item_id)
           ORDER BY i.item_id""", (config.SWEEP_MIN_VALUE,))]
    if not ids:
        return
    state, _ = db.get("sweep", {})
    state = state or {}
    nxt = next((i for i in ids if i > state.get("cursor", 0)), None)
    if nxt is None:                              # finished a full pass; start again
        state["last_full_pass"] = int(time.time())
        state["passes"] = state.get("passes", 0) + 1
        nxt = ids[0]
    await record_listing(t, nxt)
    state.update(cursor=nxt, position=ids.index(nxt) + 1, total=len(ids))
    db.put("sweep", state)


async def watchlist(t):
    """Every item on anyone's watchlist, every 10 minutes."""
    for w in db.query("SELECT DISTINCT item_id FROM watchlist"):
        await record_listing(t, w["item_id"])


def seed_from(path):
    """Start an empty server from someone's exported market history."""
    p = Path(path)
    if not p.exists() or (db.one("SELECT COUNT(*) AS n FROM item_snapshots") or {}).get("n"):
        return False
    src = sqlite3.connect(p)
    try:
        for table, cols in (("items", 7), ("item_snapshots", 4), ("item_prices", 3), ("listings", 6)):
            try:
                rows = src.execute(f"SELECT * FROM {table}").fetchall()
            except sqlite3.OperationalError:
                continue
            db.executemany(f"INSERT OR IGNORE INTO {table} VALUES ({','.join('?' * cols)})", rows)
            log.info("seeded %s rows into %s from %s", len(rows), table, p)
    finally:
        src.close()
    return True


async def start():
    seed_from(config.SEED_DB)
    holder = {"key": None, "client": None}

    def get_client():
        key = server_key()
        if not key:
            return None
        if holder["key"] != key:
            holder.update(key=key, client=TornClient(key, config.REQUESTS_PER_MIN))
        return holder["client"]

    sweep_every = 60 / config.MARKET_SWEEP_RPM if config.MARKET_SWEEP_RPM > 0 else 10 ** 9
    plan = [("items", config.ITEMS_EVERY, items), ("prices", config.PRICES_EVERY, prices),
            ("sweep", sweep_every, sweep), ("watchlist", config.WATCHLIST_EVERY, watchlist)]
    tasks = []
    for i, (name, every, fn) in enumerate(plan):
        async def runner(name=name, every=every, fn=fn, delay=i * 3):
            await asyncio.sleep(delay)  # stagger start-up
            await _loop(name, every, fn, get_client)
        tasks.append(asyncio.create_task(runner()))
    return tasks
