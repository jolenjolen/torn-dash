"""Torn Companion - FastAPI app.

Run:  uvicorn app.main:app --host 0.0.0.0 --port 8080 --proxy-headers
"""
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, analytics, auth, collectors, config, db, planner, players, push, timers, users
from .auth import current_player
from .torn import TornClient, TornError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
STATIC = Path(__file__).parent / "static"
COOKIE = auth.COOKIE


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.conn()
    tasks = await collectors.start()
    tasks += await push.start()
    yield
    for t in tasks:
        t.cancel()
    await players.close_all()


app = FastAPI(title="Torn Companion", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(planner.router)
app.include_router(push.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    path = request.url.path
    if path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    elif path == "/" or path.startswith("/static/"):
        # Always check for a newer copy, so an update never mixes old and new files.
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


# ---------- auth helpers ----------
_https, _ip, _set_cookie = auth.https, auth.client_ip, auth.set_cookie


async def _checked(key: str, request: Request):
    """_check_key with the per-IP limit on failed attempts."""
    ip = _ip(request)
    if users.throttled(ip):
        raise HTTPException(429, "Too many failed attempts. Wait ten minutes and try again.")
    try:
        return await _check_key(key)
    except HTTPException as e:
        if e.status_code == 400:
            users.record_failure(ip)
        raise


async def _check_key(key: str):
    """Ask Torn who owns this key and whether it has enough access. Returns the profile."""
    key = (key or "").strip()
    if len(key) != 16 or not key.isalnum():
        raise HTTPException(400, "A Torn API key is 16 letters and numbers. Copy it again from Settings → API Keys in Torn.")
    t = TornClient(key, config.USER_RPM)
    try:
        profile = await t.profile()
        await t.bars()
        await t.attacks(limit=1)
    except TornError as e:
        if e.code == 16:
            raise HTTPException(400, "This key's access level is too low. Create a key with Limited Access in Torn.")
        if e.code in (2, 13, 18):
            raise HTTPException(400, "Torn didn't accept that key. Check it's copied correctly and still active.")
        if e.code in (5, 8, 9):
            raise HTTPException(503, "Torn's API is busy right now. Try again in a minute.")
        raise HTTPException(400, f"Torn returned an error: {e}")
    finally:
        await t.close()
    return key, profile


# ---------- pages ----------
def _versioned_index():
    """index.html with ?v=<last change> on every script and stylesheet, so browsers
    fetch the new files after an update instead of using cached old ones."""
    v = int(max(f.stat().st_mtime for f in STATIC.iterdir() if f.suffix in (".js", ".css", ".html")))
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return re.sub(r'(/static/[\w.-]+\.(?:js|css))"', rf'\1?v={v}"', html)


@app.get("/")
def index():
    return HTMLResponse(_versioned_index())


@app.get("/sw.js")
def service_worker():
    # Served from the root so it can handle notifications for the whole site.
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/api/public")
def public():
    """What the sign-in screen needs to know. No personal data."""
    log_status = analytics.logging_status()
    return {
        "signups_open": config.ALLOW_SIGNUPS,
        "market": {k: log_status.get(k) for k in ("days", "value_points", "listing_points", "catalogue_items")},
        "community": community_stats(),
    }


# An open dashboard checks in every 30 seconds, so anyone seen in the last
# few minutes has it open right now.
ACTIVE_NOW = 5 * 60


def community_stats():
    """Counts only - never names - so anyone can see how much the dashboard is used."""
    now = int(time.time())
    torn_day = now - now % 86400   # midnight Torn time (UTC), when Torn's own day starts
    row = db.one("""SELECT COUNT(*) AS players,
                           SUM(last_seen >= ?) AS active_now,
                           SUM(last_seen >= ?) AS active_today,
                           SUM(last_seen >= ?) AS active_week,
                           SUM(created >= ?) AS new_week,
                           SUM(gemini_key IS NOT NULL) AS with_advisor
                    FROM users""", (now - ACTIVE_NOW, torn_day, now - 7 * 86400, now - 7 * 86400)) or {}
    watched = db.one("SELECT COUNT(DISTINCT item_id) AS n FROM watchlist WHERE source='manual'") or {}
    return {k: int(v or 0) for k, v in {**row, "items_watched": watched.get("n")}.items()}


# ---------- account ----------
class Login(BaseModel):
    torn_key: str = Field(min_length=1, max_length=64)


@app.post("/api/login")
async def login(body: Login, request: Request, response: Response):
    key, profile = await _checked(body.torn_key, request)
    pid = profile.get("player_id")
    reason = users.may_sign_in(pid)
    if reason:
        raise HTTPException(403, reason)
    users.upsert(pid, profile.get("name"), profile.get("level"), key)
    analytics.refresh_auto_watchlists(only=pid)
    _set_cookie(response, request, users.new_session(pid))
    await players.first_load(pid)
    return {"ok": True, "name": profile.get("name")}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    users.end_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


def _torn_image(url):
    """Only pass through profile pictures hosted by Torn."""
    return url if isinstance(url, str) and url.startswith("https://profileimages.torn.com/") else None


@app.get("/api/me")
def me(pid: int = Depends(current_player)):
    u = users.get(pid)
    return {
        "player_id": pid, "name": u["name"], "level": u["level"], "key_ok": bool(u["key_ok"]),
        "profile_image": _torn_image((db.uget(pid, "profile")[0] or {}).get("profile_image")),
        "theme": u["theme"] or "light",
        "is_owner": pid == users.owner_id(),
        "gemini": {"enabled": ai.enabled(pid), "source": ai.source(pid), "has_own_key": bool(u["gemini_key"]),
                   "daily_limit": config.AI_DAILY_LIMIT},
    }


class Settings(BaseModel):
    gemini_key: str | None = None        # "" removes it, None leaves it unchanged
    theme: str | None = Field(default=None, pattern="^(light|dark|auto)$")


@app.put("/api/settings")
def save_settings(s: Settings, pid: int = Depends(current_player)):
    fields = {}
    if s.gemini_key is not None:
        k = s.gemini_key.strip()
        if k and (len(k) < 20 or " " in k):
            raise HTTPException(400, "That doesn't look like a Gemini API key.")
        fields["gemini_key"] = k or None
    if s.theme is not None:
        fields["theme"] = s.theme
    users.update_settings(pid, **fields)
    return {"ok": True}


@app.put("/api/settings/torn_key")
async def replace_key(body: Login, request: Request, pid: int = Depends(current_player)):
    key, profile = await _checked(body.torn_key, request)
    if profile.get("player_id") != pid:
        raise HTTPException(400, "That key belongs to a different player.")
    users.upsert(pid, profile.get("name"), profile.get("level"), key)
    players.forget(pid)
    await players.first_load(pid)
    return {"ok": True}


@app.delete("/api/account")
async def delete_account(request: Request, response: Response, pid: int = Depends(current_player)):
    """Removes the player's key and every piece of their personal data. Shared market
    history isn't personal and stays."""
    players.forget(pid)
    db.delete_player(pid)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/admin/players")
def admin_players(pid: int = Depends(current_player)):
    """The server owner's view of who uses their dashboard."""
    if pid != users.owner_id():
        raise HTTPException(403, "Only the person running this server can see this.")
    return db.query("""SELECT player_id, name, level, created, last_seen, key_ok,
                              gemini_key IS NOT NULL AS has_advisor
                       FROM users ORDER BY last_seen DESC""")


# ---------- dashboard data ----------
@app.get("/api/status")
def status(pid: int = Depends(current_player)):
    return {
        "market": {n: dict(zip(("state", "ts"), db.get(f"status:{n}"))) for n in collectors.COLLECTORS},
        "player": players.status(pid),
        "server_time": int(time.time()),
    }


@app.get("/api/personal")
async def personal(pid: int = Depends(current_player)):
    await players.refresh_for_page(pid, ["personal", "logs", "inventory", "timers"])
    g = lambda k: db.uget(pid, k)[0]
    profile, p_ts = db.uget(pid, "profile")
    bars, cooldowns, money, education = g("bars"), g("cooldowns"), g("money"), g("education")
    equipment = g("equipment")
    calendar, _ = db.get("calendar")
    events = analytics.upcoming_events(calendar)
    urgent = analytics.urgent_actions(bars, cooldowns, money, education, profile, equipment, events)
    return {
        "updated": p_ts,
        "profile": profile, "bars": bars, "cooldowns": cooldowns,
        "cooldowns_until": timers.cooldowns(pid),
        "money": money, "education": education, "battlestats": g("battlestats"),
        "equipment": equipment,
        "spendable": analytics.spendable(money),
        "next_move": analytics.next_move(urgent),
        "urgent": urgent,
        "upcoming": events,
        "icons_travel": g("icons_travel"), "properties": g("properties"), "missions": g("missions"),
        "refills": g("refills"),
        "inventory_total": analytics.inventory_valued(pid)["total_value"],
        "attacks": analytics.attack_log(pid),
        "attackers": analytics.attackers_summary(pid),
        "events": analytics.recent_events(pid),
    }


@app.get("/api/faction")
async def faction(pid: int = Depends(current_player)):
    await players.refresh_for_page(pid, ["faction"], wait=("faction",))
    f, ts = db.uget(pid, "faction")
    return {"updated": ts, "faction": analytics.faction_summary(f)}


@app.get("/api/trade")
def trade(pid: int = Depends(current_player)):
    money, _ = db.uget(pid, "money")
    calendar, _ = db.get("calendar")
    budget = analytics.spendable(money)
    snaps = db.one("SELECT COUNT(DISTINCT ts) AS n FROM item_snapshots") or {"n": 0}
    advice, _ = db.uget(pid, "ai_advice")
    return {
        "snapshots": snaps["n"],
        "spendable": budget,
        "inventory": analytics.inventory_valued(pid),
        "inventory_updated": db.uget(pid, "inventory_checked")[1],
        "candidates": analytics.candidates(budget=budget),
        "watchlist": analytics.watchlist_with_prices(pid),
        "upcoming": analytics.upcoming_events(calendar),
        "advice": advice,
        "ai_enabled": ai.enabled(pid),
    }


class WatchItem(BaseModel):
    item_id: int
    target_price: int | None = None


class Season(BaseModel):
    buy_months: list[int] = Field(default_factory=list)
    sell_months: list[int] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=300)
    target_price: int | None = None


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@app.post("/api/trade/watchlist")
def add_watch(w: WatchItem, pid: int = Depends(current_player)):
    item = db.one("SELECT name FROM items WHERE item_id=?", (w.item_id,))
    if not item:
        raise HTTPException(404, "That item isn't in the catalogue yet. It loads a minute after the server starts.")
    if db.one("SELECT 1 FROM watchlist WHERE player_id=? AND item_id=?", (pid, w.item_id)):
        db.execute("""UPDATE watchlist SET source='manual', target_price=COALESCE(?, target_price)
                      WHERE player_id=? AND item_id=?""", (w.target_price, pid, w.item_id))
    else:
        db.execute("""INSERT INTO watchlist (player_id, item_id, name, target_price, source, added)
                      VALUES (?,?,?,?,?,?)""", (pid, w.item_id, item["name"], w.target_price, "manual", int(time.time())))
    return {"ok": True}


@app.put("/api/trade/watchlist/{item_id}/season")
def set_season(item_id: int, s: Season, pid: int = Depends(current_player)):
    if not db.one("SELECT 1 FROM watchlist WHERE player_id=? AND item_id=?", (pid, item_id)):
        raise HTTPException(404, "Watch the item first.")
    clean = lambda ms: ",".join(str(m) for m in sorted(set(ms)) if 1 <= m <= 12)
    db.execute(
        """UPDATE watchlist SET buy_months=?, sell_months=?, note=?, target_price=?, source='manual'
           WHERE player_id=? AND item_id=?""",
        (clean(s.buy_months), clean(s.sell_months), (s.note or "").strip() or None, s.target_price, pid, item_id),
    )
    return {"ok": True}


@app.delete("/api/trade/watchlist/{item_id}")
def remove_watch(item_id: int, pid: int = Depends(current_player)):
    db.execute("DELETE FROM watchlist WHERE player_id=? AND item_id=?", (pid, item_id))
    return {"ok": True}


@app.get("/api/items/search")
def search_items(q: str, pid: int = Depends(current_player)):
    return db.query(
        "SELECT item_id, name, type FROM items WHERE name LIKE ? AND tradeable=1 ORDER BY name LIMIT 15",
        (f"%{q[:40]}%",),
    )


@app.get("/api/items/{item_id}/history")
def item_history(item_id: int, days: int = 30, pid: int = Depends(current_player)):
    h = analytics.history(item_id, days)
    if not h["item"]:
        raise HTTPException(404, "Unknown item.")
    return h


@app.get("/api/logging")
def logging_status(pid: int = Depends(current_player)):
    return analytics.logging_status()


@app.post("/api/ai/advice")
async def advice(pid: int = Depends(current_player)):
    return await ai.run_advice(pid)


@app.post("/api/ai/ask")
async def ask(q: Question, pid: int = Depends(current_player)):
    return {"answer": await ai.ask(pid, q.question)}
