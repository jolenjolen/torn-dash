"""Gemini advisor. Everything it says is grounded in data this dashboard has stored.

- run_advice(): structured buy / sell / hold advice, cached so the page loads instantly.
- ask(question): free-form questions answered against the same data.
"""
import json
import logging
import re
import time
from pathlib import Path

from google import genai
from google.genai import errors, types

from . import analytics, config, db, users

log = logging.getLogger("ai")
KNOWLEDGE = Path(__file__).parent / "knowledge.md"

RULES = """You are a careful trading advisor for the browser game Torn City, talking to a new player.
Use plain, friendly language and short sentences. Explain any game term you use.

Hard rules:
- Only use facts from DATA and KNOWLEDGE below. Never invent prices, dates or history.
- Every recommendation must cite evidence: actual numbers from DATA (prices, % changes, dates,
  circulation) or a line from KNOWLEDGE. If history is too short to judge, say so plainly.
- Respect the player's budget ("spendable_cash"). Don't suggest buying what they can't afford.
- Prefer items with limited supply (event-only, consumed over time). Avoid items with endless supply.
- Be cautious. "Hold" or "wait" is a fine answer. Never promise profit.
- A field marked "unknown" means there is no data. Say you can't judge it; never describe it as
  unchanged or stable. Use the "supply" field for where an item comes from; don't assume.
- "days_of_price_history" tells you how much history exists. Don't talk about trends longer than that.
"""

ADVICE_FORMAT = """Reply with JSON only, in exactly this shape:
{
  "summary": "2-3 sentence overview of the player's position and the single best move",
  "recommendations": [
    {
      "action": "buy" | "sell" | "hold" | "wait" | "avoid",
      "item": "item name",
      "item_id": 123,
      "reason": "one or two plain sentences",
      "evidence": ["specific fact with a number", "..."],
      "when": "timing in plain words, e.g. 'now', 'early January', 'before 25 Oct'",
      "confidence": "low" | "medium" | "high"
    }
  ],
  "watch_out": ["short risk or caveat", "..."]
}
Give 3 to 6 recommendations. Cover the player's own inventory (sell or hold) as well as buys."""


def _backend(pid):
    """Which Gemini access this player has: ("aistudio", key), ("cloud", None) or (None, None)."""
    key, _ = users.gemini_key(pid)
    if key:
        return "aistudio", key
    if config.GOOGLE_CLOUD_PROJECT and pid == users.owner_id():
        return "cloud", None
    return None, None


def enabled(pid) -> bool:
    return _backend(pid)[0] is not None


def source(pid):
    """Shown in Settings: whose key pays for this player's advice."""
    backend, _ = _backend(pid)
    if backend == "cloud":
        return "server"
    return users.gemini_key(pid)[1]


_clients = {}


def client(pid):
    backend, key = _backend(pid)
    cache_key = key or "cloud"
    if cache_key not in _clients:
        if backend == "cloud":
            _clients[cache_key] = genai.Client(vertexai=True, project=config.GOOGLE_CLOUD_PROJECT,
                                               location=config.GOOGLE_CLOUD_LOCATION)
        else:
            _clients[cache_key] = genai.Client(api_key=key)
    return _clients[cache_key], backend


def _within_limit(pid):
    """Count today's calls; False once AI_DAILY_LIMIT is reached."""
    if not config.AI_DAILY_LIMIT:
        return True
    today = time.strftime("%Y-%m-%d", time.gmtime())
    usage, _ = db.uget(pid, "ai_usage", {})
    usage = usage or {}
    n = usage.get(today, 0)
    if n >= config.AI_DAILY_LIMIT:
        return False
    db.uput(pid, "ai_usage", {today: n + 1})
    return True


def _knowledge():
    try:
        return KNOWLEDGE.read_text()
    except OSError:
        return ""


def _short_series(series, points=30):
    """Downsample [[ts, value], ...] so prompts stay small."""
    if len(series) <= points:
        pick = series
    else:
        step = len(series) / points
        pick = [series[int(i * step)] for i in range(points)] + [series[-1]]
    return [[time.strftime("%Y-%m-%d", time.gmtime(ts)), v] for ts, v in pick]


NO_HISTORY = "unknown - not enough history recorded yet"


def _ground(row, item_id):
    """Replace blanks with explicit statements so the model can't fill gaps with guesses."""
    series = analytics.value_series(item_id, 120)
    row["value_history"] = _short_series(series)
    row["days_of_price_history"] = len(series)
    for key in ("price_chg_30d", "price_chg_90d", "chg_7d", "chg_30d"):
        if key in row and row[key] is None:
            row[key] = NO_HISTORY
    src = db.one("SELECT city_buy_price, abroad, tradeable FROM items WHERE item_id=?", (item_id,)) or {}
    if (src.get("city_buy_price") or 0) > 0:
        row["supply"] = f"endless: sold in a Torn city shop for ${src['city_buy_price']:,}"
    elif src.get("abroad"):
        row["supply"] = "endless: sold in a foreign shop (travel)"
    else:
        row["supply"] = "not sold in any shop (comes from crimes, events, loot or players)"


def build_context(pid):
    money, _ = db.uget(pid, "money")
    profile, _ = db.uget(pid, "profile")
    calendar, _ = db.get("calendar")
    budget = analytics.spendable(money)
    inv = analytics.inventory_valued(pid)
    for it in inv["items"]:
        _ground(it, it["item_id"])
    cands = analytics.candidates(limit=20, budget=budget)
    for c in cands:
        _ground(c, c["id"])
    watch = []
    for w in analytics.watchlist_with_prices(pid):
        watch.append({k: w[k] for k in ("item_id", "name", "lowest", "median_7d", "target_price",
                                          "deal", "buy_label", "sell_label", "season", "note")})
    snaps = db.one("SELECT COUNT(DISTINCT ts) AS n, MIN(ts) AS first FROM item_snapshots") or {}
    return {
        "today": time.strftime("%Y-%m-%d", time.gmtime()),
        "player": {"level": (profile or {}).get("level"), "name": (profile or {}).get("name")},
        "spendable_cash": budget,
        "data_coverage": {
            "daily_snapshots": snaps.get("n") or 0,
            "since": time.strftime("%Y-%m-%d", time.gmtime(snaps["first"])) if snaps.get("first") else None,
        },
        "inventory": inv,
        "hoard_candidates": cands,
        "watchlist": watch,
        "upcoming_events": [
            {k: e[k] for k in ("title", "days_until", "ongoing", "hint")}
            for e in analytics.upcoming_events(calendar)
        ],
    }


async def _generate(pid, prompt: str, json_mode: bool) -> str:
    gclient, backend = client(pid)
    cfg = types.GenerateContentConfig(
        temperature=0.3,
        response_mime_type="application/json" if json_mode else None,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    last_err = None
    for model in dict.fromkeys([config.GEMINI_MODEL, config.GEMINI_FALLBACK_MODEL]):
        try:
            resp = await gclient.aio.models.generate_content(model=model, contents=prompt, config=cfg)
        except errors.APIError as e:
            last_err = _explain(backend, e.code, getattr(e, "message", None) or str(e), model)
            log.warning("Gemini %s: %s", model, last_err)
            if e.code in (402, 403, 404, 429, 500, 503):   # try the fallback model
                continue
            raise RuntimeError(last_err)
        except Exception as e:   # usually missing or broken Google Cloud credentials
            raise RuntimeError(_explain(backend, None, str(e), model))
        if resp.text:
            return resp.text
        last_err = f"Gemini returned no text (model {model})."
    raise RuntimeError(last_err or "Gemini request failed.")


CLOUD_HINTS = {
    400: "Google rejected the request. Check GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION.",
    401: "Google Cloud didn't accept the credentials. Check GOOGLE_APPLICATION_CREDENTIALS "
         "or run 'gcloud auth application-default login' on the Pi.",
    403: "This project or account isn't allowed to use Gemini. Make sure the Vertex AI API is "
         "enabled on the project and the service account has the 'Vertex AI User' role.",
    404: "This model isn't available in that location. Try GOOGLE_CLOUD_LOCATION=global or "
         "another GEMINI_MODEL.",
    429: "You've hit Gemini's rate limit. Wait a minute and try again.",
    None: "Couldn't connect to Google Cloud. Usually the Pi has no credentials set up: point "
          "GOOGLE_APPLICATION_CREDENTIALS at your service account file.",
}
STUDIO_HINTS = {
    400: "Google rejected the request. Check that GEMINI_API_KEY is copied correctly.",
    401: "The Gemini key isn't valid. Create a new one in Google AI Studio.",
    402: "This key's Google project has no Gemini credit. Gemini API keys are prepay only: "
         "add credit in Google AI Studio under Billing.",
    403: "This key isn't allowed to use the Gemini API. Check the key's project in Google AI Studio.",
    404: "This model name isn't available to your key. Set GEMINI_MODEL in .env to another one.",
    429: "You've hit Gemini's rate or daily limit. Wait a bit and try again.",
    None: "Couldn't reach Gemini.",
}


def _explain(backend, code, google_msg, model):
    hints = CLOUD_HINTS if backend == "cloud" else STUDIO_HINTS
    hint = hints.get(code, "Gemini returned an error.")
    where = f"model {model}" + (f", HTTP {code}" if code else "")
    return f"{hint} ({where}{': ' + str(google_msg)[:300] if google_msg else ''})"


def _parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    return json.loads(text)


NO_KEY = "Add your Gemini API key in Settings to turn on the advisor."
LIMIT = "You've used today's advisor limit. It resets at midnight UTC."


async def run_advice(pid):
    if not enabled(pid):
        return {"error": NO_KEY}
    if not _within_limit(pid):
        return {"error": LIMIT}
    ctx = build_context(pid)
    prompt = f"{RULES}\n{ADVICE_FORMAT}\n\nKNOWLEDGE:\n{_knowledge()}\n\nDATA:\n{json.dumps(ctx, default=str)}"
    try:
        result = _parse_json(await _generate(pid, prompt[:120000], json_mode=True))
    except Exception as e:
        log.exception("advice failed")
        return {"error": f"The advisor couldn't answer: {e}"}
    result["generated_at"] = int(time.time())
    result["data_coverage"] = ctx["data_coverage"]
    db.uput(pid, "ai_advice", result)
    return result


async def ask(pid, question: str):
    if not enabled(pid):
        return NO_KEY
    if not _within_limit(pid):
        return LIMIT
    ctx = build_context(pid)
    prompt = (f"{RULES}\nAnswer the player's question in plain text (no JSON, no tables), "
              f"under 200 words, citing numbers from DATA where relevant.\n\n"
              f"KNOWLEDGE:\n{_knowledge()}\n\nDATA:\n{json.dumps(ctx, default=str)}\n\n"
              f"QUESTION: {question}")
    try:
        return await _generate(pid, prompt[:120000], json_mode=False)
    except Exception as e:
        log.exception("ask failed")
        return f"The advisor couldn't answer: {e}"
