"""Players, encrypted keys and sign-in sessions."""
import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

from cryptography.fernet import Fernet, InvalidToken

from . import config, db

SECRET_FILE = config.DATA_DIR / "secret.key"


def _secret() -> bytes:
    """Server secret: SECRET_KEY from .env, else a random one saved next to the database."""
    if config.SECRET_KEY:
        return config.SECRET_KEY.encode()
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_urlsafe(48))
        os.chmod(SECRET_FILE, 0o600)
    return SECRET_FILE.read_text().strip().encode()


_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"torn-dash-keys:" + _secret()).digest()))


def encrypt(value: str | None) -> str | None:
    return _fernet.encrypt(value.encode()).decode() if value else None


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        return None


def owner_id():
    v, _ = db.get("owner_id")
    return v


# ---------- users ----------
def get(player_id):
    return db.one("SELECT * FROM users WHERE player_id=?", (player_id,))


def torn_key(player_id):
    u = get(player_id)
    return decrypt(u["torn_key"]) if u else None


def gemini_key(player_id):
    """The player's own key, or the server owner's key if this player may share it."""
    u = get(player_id) or {}
    own = decrypt(u.get("gemini_key"))
    if own:
        return own, "own"
    if config.GEMINI_API_KEY and (player_id == owner_id() or player_id in config.SHARE_GEMINI_WITH):
        return config.GEMINI_API_KEY, "server"
    return None, None


def may_sign_in(player_id) -> str | None:
    """None if allowed, otherwise the reason."""
    if get(player_id) or player_id in config.ALLOWED_PLAYERS or player_id == owner_id():
        return None
    if not config.ALLOW_SIGNUPS:
        return "This dashboard isn't open to new players."
    if config.MAX_USERS and (db.one("SELECT COUNT(*) AS n FROM users") or {}).get("n", 0) >= config.MAX_USERS:
        return "This dashboard is full right now."
    return None


def upsert(player_id, name, level, key):
    now = int(time.time())
    if get(player_id):
        db.execute("UPDATE users SET name=?, level=?, torn_key=?, key_ok=1, last_seen=? WHERE player_id=?",
                   (name, level, encrypt(key), now, player_id))
    else:
        db.execute("""INSERT INTO users (player_id, name, level, torn_key, key_ok, extra_cash, theme, created, last_seen)
                      VALUES (?,?,?,?,1,0,'light',?,?)""", (player_id, name, level, encrypt(key), now, now))
    if owner_id() is None:          # the first player to sign in owns the server
        db.put("owner_id", player_id)


def update_settings(player_id, **fields):
    allowed = {"gemini_key", "theme"}
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "gemini_key":
            v = encrypt(v) if v else None
        db.execute(f"UPDATE users SET {k}=? WHERE player_id=?", (v, player_id))


def mark_key_bad(player_id):
    db.execute("UPDATE users SET key_ok=0 WHERE player_id=?", (player_id,))


# ---------- sessions ----------
def _hash(token: str) -> str:
    return hmac.new(_secret(), token.encode(), hashlib.sha256).hexdigest()


def new_session(player_id) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    db.execute("INSERT INTO sessions VALUES (?,?,?,?)",
               (_hash(token), player_id, now, now + config.SESSION_DAYS * 86400))
    db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
    return token


def session_player(token: str | None):
    if not token:
        return None
    row = db.one("SELECT player_id, expires FROM sessions WHERE token_hash=?", (_hash(token),))
    if not row or row["expires"] < time.time():
        return None
    db.execute("UPDATE users SET last_seen=? WHERE player_id=?", (int(time.time()), row["player_id"]))
    return row["player_id"]


def end_session(token: str | None):
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (_hash(token),))


# ---------- sign-in throttle ----------
# Torn temporarily bans IPs that send many requests with invalid keys, and every
# sign-in attempt is checked against Torn from this server, so attempts are limited.
_attempts: dict[str, deque] = defaultdict(deque)
ATTEMPTS, WINDOW = 5, 600


def throttled(ip: str) -> bool:
    """True if this IP has had too many failed attempts recently."""
    q = _attempts[ip]
    now = time.time()
    while q and now - q[0] > WINDOW:
        q.popleft()
    return len(q) >= ATTEMPTS


def record_failure(ip: str):
    _attempts[ip].append(time.time())
