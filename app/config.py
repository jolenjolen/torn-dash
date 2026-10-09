"""Settings, read from environment variables (or a .env file next to the app)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Load a simple KEY=VALUE .env file if present (no extra dependency needed).
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_bool = lambda name, default: os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")

DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = os.environ.get("TORN_DB", str(DATA_DIR / "torn.db"))
if not os.environ.get("TORN_DB") and (ROOT / "torn.db").exists() and not Path(DB_PATH).exists():
    DB_PATH = str(ROOT / "torn.db")   # keep using the database from the single-user version

# ---------- server ----------
# The server's own Torn key. It collects the shared market history everyone sees,
# so it's used for catalogue, price and listing checks only - never for anyone's personal data.
TORN_KEY = os.environ.get("TORN_KEY", "")

# Used to encrypt stored API keys and sign sessions. Generated on first run if not set.
SECRET_KEY = os.environ.get("SECRET_KEY", "")

# Who can sign in. ALLOW_SIGNUPS=false limits it to existing users and ALLOWED_PLAYERS.
ALLOW_SIGNUPS = _bool("ALLOW_SIGNUPS", "true")
ALLOWED_PLAYERS = {int(x) for x in os.environ.get("ALLOWED_PLAYERS", "").replace(" ", "").split(",") if x.isdigit()}
MAX_USERS = int(os.environ.get("MAX_USERS", "0"))  # 0 = no limit
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "30"))
# Send cookies only over HTTPS. "auto" turns it on when the request came in over HTTPS.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "auto").lower()

# Optional: a market-history database to start from (see scripts/export_market.py).
SEED_DB = os.environ.get("SEED_DB", str(ROOT / "seed" / "market.db"))

# ---------- Gemini ----------
# Each player can add their own Gemini key in Settings. The server owner's key below is
# only used for the owner (and for player ids listed in SHARE_GEMINI_WITH, if any).
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
SHARE_GEMINI_WITH = {int(x) for x in os.environ.get("SHARE_GEMINI_WITH", "").replace(" ", "").split(",") if x.isdigit()}
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
# Advice calls per player per day (0 = no limit). Protects whoever's key is paying.
AI_DAILY_LIMIT = int(os.environ.get("AI_DAILY_LIMIT", "20"))

# ---------- Torn request budget ----------
# Torn allows 100 requests/min per player across ALL their tools. Stay well under.
REQUESTS_PER_MIN = int(os.environ.get("TORN_RPM", "50"))      # server key
USER_RPM = int(os.environ.get("USER_RPM", "30"))               # each player's key

# How old a player's data may get before it's fetched again (seconds). Data is only
# fetched while that player has the dashboard open, so idle players cost nothing.
PERSONAL_EVERY = int(os.environ.get("PERSONAL_EVERY", "60"))
LOGS_EVERY = int(os.environ.get("LOGS_EVERY", "300"))
FACTION_EVERY = int(os.environ.get("FACTION_EVERY", "120"))
INVENTORY_EVERY = int(os.environ.get("INVENTORY_EVERY", "3600"))
TIMERS_EVERY = int(os.environ.get("TIMERS_EVERY", "300"))

# Browser notifications. With notifications on, a player's data is also refreshed in the
# background (every NOTIFY_REFRESH seconds) so reminders can arrive with the dashboard closed.
NOTIFY_REFRESH = int(os.environ.get("NOTIFY_REFRESH", "600"))
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")

# Shared market collectors (server key).
WATCHLIST_EVERY = int(os.environ.get("WATCHLIST_EVERY", "600"))
ITEMS_EVERY = int(os.environ.get("ITEMS_EVERY", str(24 * 3600)))
PRICES_EVERY = int(os.environ.get("PRICES_EVERY", "3600"))        # market value of all items (1 call)
MARKET_SWEEP_RPM = float(os.environ.get("MARKET_SWEEP_RPM", "4"))  # cheapest-listing checks per minute
SWEEP_MIN_VALUE = int(os.environ.get("SWEEP_MIN_VALUE", "1000"))   # skip near-worthless items
PRUNE_AFTER_DAYS = int(os.environ.get("PRUNE_AFTER_DAYS", "90"))   # then keep one point per day

# ---------- trade ----------
WATCH_TOP_CANDIDATES = int(os.environ.get("WATCH_TOP_CANDIDATES", "10"))
MIN_CANDIDATE_VALUE = int(os.environ.get("MIN_CANDIDATE_VALUE", "10000"))
DEAL_THRESHOLD = float(os.environ.get("DEAL_THRESHOLD", "0.90"))  # lowest < 90% of 7d median
WALLET_WARN = int(os.environ.get("WALLET_WARN", "5000"))  # cash in hand above this = mug risk
