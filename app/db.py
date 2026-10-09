"""SQLite storage.

Two kinds of data live here:
- Shared market data (items, prices, listings, circulation). Collected once with the
  server's key and visible to everyone. This is the history new users get on day one.
- Per-player data (bars, inventory, fights, watchlist, advice). Every row carries a
  player_id and is only ever read back for that player.
"""
import json
import sqlite3
import threading
import time

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
SCHEMA_VERSION = 2

SCHEMA = """
-- Server-level cache and collector status.
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts INTEGER);

-- ---------- shared market data ----------
CREATE TABLE IF NOT EXISTS items (
    item_id INTEGER PRIMARY KEY, name TEXT, type TEXT, description TEXT,
    tradeable INTEGER, city_buy_price INTEGER, abroad INTEGER
);
CREATE TABLE IF NOT EXISTS item_snapshots (
    item_id INTEGER, ts INTEGER, circulation INTEGER, market_value INTEGER,
    PRIMARY KEY (item_id, ts)
);
-- Market value for every item, logged hourly; a row is only added when the value changes.
CREATE TABLE IF NOT EXISTS item_prices (
    item_id INTEGER, ts INTEGER, market_value INTEGER,
    PRIMARY KEY (item_id, ts)
);
CREATE TABLE IF NOT EXISTS listings (
    item_id INTEGER, ts INTEGER, lowest INTEGER, lowest_qty INTEGER,
    avg_price INTEGER, total_listings INTEGER,
    PRIMARY KEY (item_id, ts)
);

-- ---------- players ----------
CREATE TABLE IF NOT EXISTS users (
    player_id INTEGER PRIMARY KEY, name TEXT, level INTEGER,
    torn_key TEXT,            -- encrypted
    gemini_key TEXT,          -- encrypted, optional
    key_ok INTEGER DEFAULT 1, -- 0 once Torn rejects the key
    extra_cash INTEGER DEFAULT 0, theme TEXT DEFAULT 'light',
    created INTEGER, last_seen INTEGER
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, player_id INTEGER, created INTEGER, expires INTEGER
);
CREATE TABLE IF NOT EXISTS user_kv (
    player_id INTEGER, k TEXT, v TEXT, ts INTEGER, PRIMARY KEY (player_id, k)
);
CREATE TABLE IF NOT EXISTS events (
    player_id INTEGER, id TEXT, ts INTEGER, text TEXT, PRIMARY KEY (player_id, id)
);
CREATE TABLE IF NOT EXISTS attacks (
    player_id INTEGER, id INTEGER, started INTEGER, ended INTEGER,
    direction TEXT,                       -- 'out' (you attacked) or 'in' (you were attacked)
    attacker_id INTEGER, attacker_name TEXT, attacker_level INTEGER,
    defender_id INTEGER, defender_name TEXT, defender_level INTEGER,
    result TEXT, respect REAL, ranked_war INTEGER, territory_war INTEGER,
    PRIMARY KEY (player_id, id)
);
CREATE TABLE IF NOT EXISTS watchlist (
    player_id INTEGER, item_id INTEGER, name TEXT, target_price INTEGER,
    source TEXT, added INTEGER,           -- source: 'manual' or 'auto'
    buy_months TEXT, sell_months TEXT, note TEXT,
    PRIMARY KEY (player_id, item_id)
);
CREATE TABLE IF NOT EXISTS inventory (
    player_id INTEGER, item_id INTEGER, name TEXT, type TEXT, amount INTEGER, equipped INTEGER,
    PRIMARY KEY (player_id, item_id, equipped)
);

-- ---------- planner ----------
-- scope 'me'      -> owner is the player id (private)
-- scope 'faction' -> owner is the faction id (shared with every member who uses the dashboard)
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, owner INTEGER, author INTEGER,
    title TEXT, body TEXT, tags TEXT, pinned INTEGER DEFAULT 0, created INTEGER, updated INTEGER
);
CREATE TABLE IF NOT EXISTS checklists (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, owner INTEGER, author INTEGER,
    title TEXT, position INTEGER DEFAULT 0, created INTEGER, updated INTEGER
);
CREATE TABLE IF NOT EXISTS checklist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, checklist_id INTEGER, text TEXT,
    done INTEGER DEFAULT 0, done_at INTEGER, auto TEXT, position INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, owner INTEGER, author INTEGER,
    title TEXT, note TEXT, link TEXT, due INTEGER, repeat TEXT, every_days INTEGER,
    done INTEGER DEFAULT 0, created INTEGER
);
CREATE TABLE IF NOT EXISTS people (
    scope TEXT, owner INTEGER, target_id INTEGER, name TEXT, level INTEGER,
    tags TEXT, note TEXT, updated_by INTEGER, updated INTEGER,
    PRIMARY KEY (scope, owner, target_id)
);
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, owner INTEGER, author INTEGER,
    title TEXT, url TEXT, category TEXT, created INTEGER
);
CREATE TABLE IF NOT EXISTS board_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, owner INTEGER, author INTEGER,
    col TEXT, title TEXT, body TEXT, color TEXT, position REAL, created INTEGER, updated INTEGER
);
CREATE TABLE IF NOT EXISTS canvas_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, owner INTEGER, author INTEGER,
    kind TEXT,                     -- 'note' or 'arrow'
    x REAL, y REAL, w REAL, h REAL, text TEXT, color TEXT,
    from_id INTEGER, to_id INTEGER, updated INTEGER
);
CREATE TABLE IF NOT EXISTS push_subs (
    endpoint TEXT PRIMARY KEY, player_id INTEGER, sub TEXT, created INTEGER
);
CREATE TABLE IF NOT EXISTS sent_notices (
    player_id INTEGER, key TEXT, ts INTEGER, PRIMARY KEY (player_id, key)
);
CREATE INDEX IF NOT EXISTS notes_owner ON notes(scope, owner);
CREATE INDEX IF NOT EXISTS reminders_owner ON reminders(scope, owner);
CREATE INDEX IF NOT EXISTS cards_owner ON board_cards(scope, owner);
CREATE INDEX IF NOT EXISTS canvas_owner ON canvas_items(scope, owner);
"""

# Personal values the single-user version kept in kv; moved to user_kv on upgrade.
_PERSONAL_KEYS = ("profile", "bars", "cooldowns", "money", "education", "battlestats",
                  "equipment", "faction", "ai_advice", "inventory_checked")


def _columns(c, table):
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _migrate_v1(c):
    """Upgrade a single-user database: personal tables gain player_id, and everything
    personal is handed to the owner's player id."""
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    personal = [t for t in ("events", "attacks", "watchlist", "inventory") if t in tables]
    if not personal or "player_id" in _columns(c, personal[0]):
        return None
    row = c.execute("SELECT v FROM kv WHERE k='profile'").fetchone()
    owner = (json.loads(row[0]) or {}).get("player_id") if row else None
    owner = owner or 0
    for t in personal:
        c.execute(f"ALTER TABLE {t} RENAME TO {t}_v1")
    if "watchlist_v1" in {f"{t}_v1" for t in personal}:
        for col in ("buy_months", "sell_months", "note"):
            if col not in _columns(c, "watchlist_v1"):
                c.execute(f"ALTER TABLE watchlist_v1 ADD COLUMN {col} TEXT")
    c.executescript(SCHEMA)
    copies = {
        "events": "INSERT OR IGNORE INTO events SELECT ?, id, ts, text FROM events_v1",
        "attacks": "INSERT OR IGNORE INTO attacks SELECT ?, * FROM attacks_v1",
        "watchlist": """INSERT OR IGNORE INTO watchlist SELECT ?, item_id, name, target_price, source,
                        added, buy_months, sell_months, note FROM watchlist_v1""",
        "inventory": "INSERT OR IGNORE INTO inventory SELECT ?, item_id, name, type, amount, equipped FROM inventory_v1",
    }
    for t in personal:
        c.execute(copies[t], (owner,))
        c.execute(f"DROP TABLE {t}_v1")
    for k in _PERSONAL_KEYS:
        r = c.execute("SELECT v, ts FROM kv WHERE k=?", (k,)).fetchone()
        if r:
            c.execute("INSERT OR REPLACE INTO user_kv VALUES (?,?,?,?)", (owner, k, r[0], r[1]))
            c.execute("DELETE FROM kv WHERE k=?", (k,))
    if owner:
        c.execute("INSERT OR REPLACE INTO kv VALUES ('owner_id', ?, ?)", (json.dumps(owner), int(time.time())))
    c.commit()
    return owner


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                c = sqlite3.connect(config.DB_PATH, check_same_thread=False)
                c.row_factory = sqlite3.Row
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA foreign_keys=OFF")
                c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts INTEGER)")
                _migrate_v1(c)
                c.executescript(SCHEMA)
                c.execute("CREATE INDEX IF NOT EXISTS listings_ts ON listings(ts)")
                c.execute("CREATE INDEX IF NOT EXISTS attacks_started ON attacks(player_id, started)")
                c.commit()
                _conn = c
    return _conn


def execute(sql, params=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur


def executemany(sql, rows):
    with _lock:
        c = conn()
        c.executemany(sql, rows)
        c.commit()


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


# ---- server-level key/value ----
def put(key, value):
    execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (key, json.dumps(value), int(time.time())))


def get(key, default=None):
    row = one("SELECT v, ts FROM kv WHERE k=?", (key,))
    if not row:
        return default, None
    return json.loads(row["v"]), row["ts"]


# ---- per-player key/value ----
def uput(player_id, key, value):
    execute("INSERT OR REPLACE INTO user_kv VALUES (?,?,?,?)",
            (player_id, key, json.dumps(value), int(time.time())))


def uget(player_id, key, default=None):
    row = one("SELECT v, ts FROM user_kv WHERE player_id=? AND k=?", (player_id, key))
    if not row:
        return default, None
    return json.loads(row["v"]), row["ts"]


def delete_player(player_id):
    with _lock:
        c = conn()
        for t in ("users", "sessions", "user_kv", "events", "attacks", "watchlist", "inventory",
                  "push_subs", "sent_notices"):
            c.execute(f"DELETE FROM {t} WHERE player_id=?", (player_id,))
        # Private planner data goes; anything they added to a faction space stays with the faction.
        c.execute("DELETE FROM checklist_items WHERE checklist_id IN "
                  "(SELECT id FROM checklists WHERE scope='me' AND owner=?)", (player_id,))
        for t in ("notes", "checklists", "reminders", "people", "links", "board_cards", "canvas_items"):
            c.execute(f"DELETE FROM {t} WHERE scope='me' AND owner=?", (player_id,))
        c.commit()
