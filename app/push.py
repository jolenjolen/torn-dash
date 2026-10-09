"""Browser push notifications (Web Push with VAPID).

Players who turn notifications on get their timers refreshed in the background with their
own key, so reminders arrive even with the dashboard closed. Everyone else costs nothing.
"""
import asyncio
import base64
import json
import logging
import time

from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import config, db, players, timers
from .auth import current_player

log = logging.getLogger("push")
router = APIRouter(prefix="/api")
KEY_FILE = config.DATA_DIR / "vapid_private.pem"
MAX_AGE = 6 * 3600        # don't send notices for things that were due more than 6 hours ago

try:
    from py_vapid import Vapid02
    from pywebpush import WebPushException, webpush
    AVAILABLE = True
except ImportError:          # notifications are optional; the rest of the app works without them
    AVAILABLE = False

_vapid = None


def vapid():
    global _vapid
    if _vapid is None and AVAILABLE:
        if KEY_FILE.exists():
            _vapid = Vapid02.from_file(str(KEY_FILE))
        else:
            _vapid = Vapid02()
            _vapid.generate_keys()
            _vapid.save_key(str(KEY_FILE))
            KEY_FILE.chmod(0o600)
    return _vapid


def public_key():
    v = vapid()
    raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _send_sync(sub, payload):
    return webpush(sub, json.dumps(payload), vapid_private_key=vapid(),
                   vapid_claims={"sub": config.VAPID_SUBJECT}, ttl=3600, timeout=10)


async def send(pid, title, body=None, url="/", tag=None):
    """Send to every device the player subscribed. Returns how many got it."""
    if not AVAILABLE:
        return 0
    sent = 0
    for row in db.query("SELECT endpoint, sub FROM push_subs WHERE player_id=?", (pid,)):
        try:
            await asyncio.to_thread(_send_sync, json.loads(row["sub"]),
                                    {"title": title, "body": body or "", "url": url, "tag": tag})
            sent += 1
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):          # the browser unsubscribed; forget this device
                db.execute("DELETE FROM push_subs WHERE endpoint=?", (row["endpoint"],))
            else:
                log.warning("push to %s failed: %s", pid, e)
        except Exception:
            log.exception("push to %s failed", pid)
    return sent


# ---------------- routes ----------------
class Subscription(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000)
    keys: dict


class Prefs(BaseModel):
    prefs: dict[str, bool]


@router.get("/push/key")
def get_key(pid: int = Depends(current_player)):
    if not AVAILABLE:
        raise HTTPException(503, "Notifications aren't installed on this server (pip install pywebpush).")
    return {"key": public_key()}


@router.post("/push/subscribe")
async def subscribe(s: Subscription, pid: int = Depends(current_player)):
    if not s.endpoint.startswith("https://"):
        raise HTTPException(400, "That isn't a valid push subscription.")
    db.execute("INSERT OR REPLACE INTO push_subs VALUES (?,?,?,?)",
               (s.endpoint, pid, json.dumps({"endpoint": s.endpoint, "keys": s.keys}), int(time.time())))
    return {"ok": True}


@router.post("/push/unsubscribe")
def unsubscribe(s: Subscription, pid: int = Depends(current_player)):
    db.execute("DELETE FROM push_subs WHERE endpoint=? AND player_id=?", (s.endpoint, pid))
    return {"ok": True}


@router.post("/push/test")
async def test(pid: int = Depends(current_player)):
    n = await send(pid, "Notifications are on", "You'll hear from the dashboard when something is due.", tag="test")
    if not n:
        raise HTTPException(400, "No device is subscribed yet. Turn notifications on first.")
    return {"sent": n}


@router.get("/notify/prefs")
def get_prefs(pid: int = Depends(current_player)):
    devices = (db.one("SELECT COUNT(*) AS n FROM push_subs WHERE player_id=?", (pid,)) or {}).get("n", 0)
    return {"prefs": timers.prefs(pid), "labels": {k: v[0] for k, v in timers.KINDS.items()},
            "devices": devices, "available": AVAILABLE}


@router.put("/notify/prefs")
def set_prefs(p: Prefs, pid: int = Depends(current_player)):
    db.uput(pid, "notify_prefs", {k: bool(v) for k, v in p.prefs.items() if k in timers.KINDS})
    return {"ok": True}


# ---------------- background checker ----------------
def due_notices(pid, now=None):
    """Timeline items that should be announced now and haven't been yet."""
    now = now or int(time.time())
    enabled = timers.prefs(pid)
    out = []
    for it in timers.build(pid, include_tips=False):
        kind, due = it["kind"], it.get("due")
        if not due or not enabled.get(kind) or it.get("ongoing"):
            continue
        lead = timers.KINDS.get(kind, (None, None, 0))[2]
        if not (due - lead <= now <= due + MAX_AGE):
            continue
        key = f"{it['key']}:{due // 3600}:{lead}"
        if db.one("SELECT 1 FROM sent_notices WHERE player_id=? AND key=?", (pid, key)):
            continue
        out.append((key, it))
    return out


def _notice_text(it, now):
    due = it["due"]
    if due > now + 60:
        left = due - now
        when = f"in {left // 86400} day(s)" if left >= 86400 else f"in {max(1, left // 3600)} hour(s)" \
            if left >= 3600 else f"in {max(1, left // 60)} min"
        return it["title"], f"{when}. {it.get('detail') or ''}".strip()
    return it["title"], it.get("detail") or "Now."


async def check_once():
    now = int(time.time())
    pids = [r["player_id"] for r in db.query("SELECT DISTINCT player_id FROM push_subs")]
    for pid in pids:
        try:
            for part in ("personal", "timers"):
                _, ts = db.uget(pid, f"status:{part}")
                if not ts or now - ts >= config.NOTIFY_REFRESH:
                    await players.refresh(pid, part, force=True)
            for key, it in due_notices(pid, now):
                title, body = _notice_text(it, now)
                if await send(pid, title, body, url=it.get("url") or "/", tag=it["key"]):
                    db.execute("INSERT OR REPLACE INTO sent_notices VALUES (?,?,?)", (pid, key, now))
        except Exception:
            log.exception("notification check for %s failed", pid)
    db.execute("DELETE FROM sent_notices WHERE ts < ?", (now - 30 * 86400,))


async def start():
    async def loop():
        await asyncio.sleep(20)
        while True:
            await check_once()
            await asyncio.sleep(60)
    return [asyncio.create_task(loop())] if AVAILABLE else []
