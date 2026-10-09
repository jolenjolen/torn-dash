"""Per-player data, fetched with that player's own key while they have the dashboard open.

Nothing runs in the background for players: when a page asks for data and it's older than
its interval, it's fetched then. Idle players cost no API calls at all.
"""
import asyncio
import logging
import time

from . import config, db, users
from .torn import TornClient, TornError

log = logging.getLogger("players")

_clients: dict[int, TornClient] = {}
_locks: dict[tuple, asyncio.Lock] = {}

# Torn error codes that mean the key itself can't be used any more.
KEY_DEAD = {2, 10, 13, 18}


def client(player_id) -> TornClient | None:
    key = users.torn_key(player_id)
    if not key:
        return None
    c = _clients.get(player_id)
    if c is None or c.key != key:
        c = TornClient(key, config.USER_RPM)
        _clients[player_id] = c
    return c


def forget(player_id):
    c = _clients.pop(player_id, None)
    if c:
        asyncio.create_task(c.close())


async def close_all():
    for c in list(_clients.values()):
        await c.close()
    _clients.clear()


# ---------- what gets fetched ----------
async def _personal(pid, t):
    profile = await t.profile()
    db.uput(pid, "profile", profile)
    db.uput(pid, "bars", await t.bars())
    db.uput(pid, "cooldowns", await t.cooldowns())
    db.uput(pid, "money", await t.money())
    db.uput(pid, "education", await t.education())
    db.uput(pid, "battlestats", await t.battlestats())
    db.execute("UPDATE users SET name=?, level=? WHERE player_id=?",
               (profile.get("name"), profile.get("level"), pid))


async def _logs(pid, t):
    events = await t.events(limit=50)
    db.executemany("INSERT OR IGNORE INTO events VALUES (?,?,?,?)",
                   [(pid, e["id"], e["timestamp"], e["event"]) for e in events])
    rows = []
    for a in await t.attacks(limit=100):
        att, dfn = a.get("attacker") or {}, a.get("defender") or {}
        rows.append((
            pid, a["id"], a.get("started"), a.get("ended"), "out" if att.get("id") == pid else "in",
            att.get("id"), att.get("name"), att.get("level"),
            dfn.get("id"), dfn.get("name"), dfn.get("level"),
            a.get("result"), a.get("respect_gain") or 0,
            int(bool(a.get("is_ranked_war"))), int(bool(a.get("is_territory_war"))),
        ))
    db.executemany("INSERT OR REPLACE INTO attacks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.uput(pid, "equipment", await t.equipment())


async def _inventory(pid, t):
    # Torn needs one call per category (and caches each for an hour), so this runs hourly.
    types = [r["type"] for r in db.query(
        "SELECT DISTINCT type FROM items WHERE type IS NOT NULL AND type != ''")]
    if not types:
        return
    rows = []
    for cat in types:
        try:
            items = await t.inventory(cat)
        except TornError as e:
            if e.code == 21:          # a category Torn doesn't accept for inventories
                continue
            raise
        rows += [(pid, it["id"], it.get("name"), cat, it.get("amount") or 0, int(bool(it.get("equipped"))))
                 for it in items]
    with db._lock:
        db.execute("DELETE FROM inventory WHERE player_id=?", (pid,))
        db.executemany("INSERT OR REPLACE INTO inventory VALUES (?,?,?,?,?,?)", rows)
    db.uput(pid, "inventory_checked", True)


async def _faction(pid, t):
    profile, _ = db.uget(pid, "profile")
    if not ((profile or {}).get("faction") or {}).get("faction_id"):
        db.uput(pid, "faction", None)
        return
    db.uput(pid, "faction", await t.faction())


async def _timers(pid, t):
    """Everything with a countdown: icons (bank, faction, company...), travel, rent,
    mission rewards, refills and organised crime."""
    db.uput(pid, "icons_travel", await t.icons_travel())
    db.uput(pid, "properties", await t.properties())
    db.uput(pid, "missions", await t.missions())
    db.uput(pid, "refills", await t.refills())
    profile, _ = db.uget(pid, "profile")
    if ((profile or {}).get("faction") or {}).get("faction_id"):
        try:
            db.uput(pid, "organizedcrime", await t.organizedcrime())
        except TornError as e:
            if e.code not in (16, 7):     # not available to this key or player; not an error worth showing
                raise


PARTS = {
    "personal": (lambda: config.PERSONAL_EVERY, _personal),
    "logs": (lambda: config.LOGS_EVERY, _logs),
    "faction": (lambda: config.FACTION_EVERY, _faction),
    "inventory": (lambda: config.INVENTORY_EVERY, _inventory),
    "timers": (lambda: config.TIMERS_EVERY, _timers),
}


async def refresh(pid, part, force=False):
    every, fn = PARTS[part]
    _, ts = db.uget(pid, f"status:{part}")
    if not force and ts and time.time() - ts < every():
        return
    lock = _locks.setdefault((pid, part), asyncio.Lock())
    if lock.locked():                 # someone is already fetching this; don't double up
        return
    async with lock:
        t = client(pid)
        if not t:
            return
        try:
            await fn(pid, t)
            db.uput(pid, f"status:{part}", {"ok": True})
        except TornError as e:
            log.warning("player %s %s: %s", pid, part, e)
            db.uput(pid, f"status:{part}", {"ok": False, "error": str(e), "code": e.code})
            if e.code in KEY_DEAD:
                users.mark_key_bad(pid)
        except Exception as e:
            log.exception("player %s %s failed", pid, part)
            db.uput(pid, f"status:{part}", {"ok": False, "error": repr(e)})


async def refresh_for_page(pid, parts, wait=("personal",)):
    """Refresh what a page needs. Parts in `wait` are awaited (so the page shows fresh
    numbers); slower ones run in the background and appear on the next poll."""
    awaited = [refresh(pid, p) for p in parts if p in wait]
    for p in parts:
        if p not in wait:
            asyncio.create_task(refresh(pid, p))
    if awaited:
        await asyncio.gather(*awaited)


async def first_load(pid):
    """Everything, right after sign-in."""
    await refresh(pid, "personal", force=True)
    for p in ("logs", "faction", "inventory", "timers"):
        asyncio.create_task(refresh(pid, p, force=True))


def status(pid):
    return {p: db.uget(pid, f"status:{p}")[0] for p in PARTS}
