"""Async Torn API client with a shared rate limiter. Read-only by design (the API is read-only)."""
import asyncio
import time

import httpx

from . import config

V1 = "https://api.torn.com"
V2 = "https://api.torn.com/v2"
YATA_ABROAD = "https://yata.yt/api/v1/travel/export/"


class TornError(Exception):
    def __init__(self, code, message):
        super().__init__(f"Torn API error {code}: {message}")
        self.code = code


class TornClient:
    def __init__(self, key: str, per_min: int):
        self.key = key
        self.gap = 60.0 / per_min
        self._last = 0.0
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=20, headers={"User-Agent": "torn-dash/1.0"})

    async def _wait(self):
        async with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.gap:
                await asyncio.sleep(self.gap - delta)
            self._last = time.monotonic()

    async def _get(self, url: str, params: dict | None = None) -> dict:
        await self._wait()
        p = dict(params or {})
        p["key"] = self.key
        r = await self._http.get(url, params=p)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "error" in data:
            e = data["error"]
            raise TornError(e.get("code"), e.get("error") or e.get("message"))
        return data

    # ---- personal ----
    async def profile(self):
        return await self._get(f"{V1}/user/", {"selections": "profile"})

    async def bars(self):
        return (await self._get(f"{V2}/user/bars"))["bars"]

    async def cooldowns(self):
        return (await self._get(f"{V2}/user/cooldowns"))["cooldowns"]

    async def money(self):
        return (await self._get(f"{V2}/user/money"))["money"]

    async def education(self):
        return (await self._get(f"{V2}/user/education"))["education"]

    async def battlestats(self):
        return (await self._get(f"{V2}/user/battlestats"))["battlestats"]

    async def events(self, limit=50):
        return (await self._get(f"{V2}/user/events", {"limit": limit}))["events"]

    async def attacks(self, limit=100):
        return (await self._get(f"{V2}/user/attacks", {"limit": limit}))["attacks"]

    async def inventory(self, category: str, limit=250):
        """One inventory category (Torn requires a category; names match item types)."""
        return (await self._get(f"{V2}/user/inventory", {"cat": category, "limit": limit}))["inventory"]["items"]

    async def equipment(self):
        return await self._get(f"{V2}/user/equipment")

    # ---- timers ----
    async def icons_travel(self):
        """Status icons (their text carries countdowns: bank, faction, company...) and travel."""
        return await self._get(f"{V1}/user/", {"selections": "icons,travel"})

    async def properties(self):
        return (await self._get(f"{V2}/user/properties"))["properties"]

    async def missions(self):
        return (await self._get(f"{V2}/user/missions"))["missions"]

    async def refills(self):
        return (await self._get(f"{V2}/user/refills"))["refills"]

    async def organizedcrime(self):
        return (await self._get(f"{V2}/user/organizedcrime")).get("organizedCrime")

    async def player_basic(self, player_id: int):
        """Name, level and status of any player (for tagging people)."""
        return await self._get(f"{V1}/user/{int(player_id)}", {"selections": "basic"})

    async def calendar(self):
        return (await self._get(f"{V2}/torn/calendar"))["calendar"]

    # ---- faction (your own faction) ----
    async def faction(self):
        return await self._get(f"{V1}/faction/", {"selections": "basic,chain"})

    # ---- market ----
    async def all_items(self):
        return (await self._get(f"{V1}/torn/", {"selections": "items"}))["items"]

    async def itemmarket(self, item_id: int, limit=20):
        return (await self._get(f"{V2}/market/{item_id}/itemmarket", {"limit": limit}))["itemmarket"]

    async def abroad_item_ids(self) -> set[int]:
        """Items stocked in foreign shops (infinite supply). Uses YATA; empty set on failure."""
        try:
            r = await self._http.get(YATA_ABROAD)
            r.raise_for_status()
            ids = set()
            for country in r.json().get("stocks", {}).values():
                for s in country.get("stocks", []):
                    if "id" in s:
                        ids.add(int(s["id"]))
            return ids
        except Exception:
            return set()

    async def close(self):
        await self._http.aclose()
