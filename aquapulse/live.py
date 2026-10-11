"""Live environmental feed for every AquaPulse site.

Uses Open-Meteo (free, no API key required):
  * weather + rainfall (forecast API)
  * river discharge (GloFAS flood API), where a river grid cell exists
The feed refreshes in a background thread and never blocks the dashboard.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
import urllib.parse
import urllib.request

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"


class LiveFeed:
    def __init__(self, sites: list[dict], ttl: int = 600):
        self.sites = sites
        self.ttl = ttl
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._updated: str | None = None
        self._error: str | None = None
        self._ok = False

    # ---- HTTP -----------------------------------------------------------
    @staticmethod
    def _get(url: str, params: dict):
        query = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "AquaPulse/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    # ---- refresh --------------------------------------------------------
    def refresh(self) -> bool:
        lat = ",".join(str(s["lat"]) for s in self.sites)
        lon = ",".join(str(s["lon"]) for s in self.sites)
        try:
            weather = self._get(WEATHER_URL, {
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,precipitation,rain",
                "hourly": "precipitation", "past_days": 1, "forecast_days": 1,
                "timezone": "Asia/Kolkata",
            })
        except Exception as exc:  # network down, rate limit, etc.
            with self._lock:
                self._error = f"weather feed unavailable: {exc}"
                self._ok = False
            return False

        items = weather if isinstance(weather, list) else [weather]
        fresh: dict[str, dict] = {}
        for site, item in zip(self.sites, items):
            cur = item.get("current", {})
            hourly = item.get("hourly", {})
            times, precip = hourly.get("time") or [], hourly.get("precipitation") or []
            idx = max((i for i, t in enumerate(times) if t[:13] == str(cur.get("time", ""))[:13]), default=len(times) - 1)
            window = [p or 0.0 for p in precip[max(0, idx - 23): idx + 1]]
            fresh[site["id"]] = {
                "temperature": cur.get("temperature_2m"),
                "rain_now": cur.get("precipitation"),
                "rain_24h": round(sum(window), 1) if window else None,
                "river_discharge": None, "river_trend": None,
                "observed_at": cur.get("time"),
            }

        # River discharge: per-site, best effort (None for non-river locations)
        for site in self.sites:
            try:
                flood = self._get(FLOOD_URL, {
                    "latitude": site["lat"], "longitude": site["lon"],
                    "daily": "river_discharge", "past_days": 5, "forecast_days": 1,
                })
                series = [v for v in (flood.get("daily", {}).get("river_discharge") or []) if v is not None]
                if series:
                    last = series[-1]
                    prev = series[-4] if len(series) >= 4 else series[0]
                    fresh[site["id"]]["river_discharge"] = round(last, 1)
                    fresh[site["id"]]["river_trend"] = (
                        "rising" if last > prev * 1.1 else "falling" if last < prev * 0.9 else "steady")
            except Exception:
                pass

        with self._lock:
            self._data = fresh
            self._updated = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).isoformat(timespec="minutes")
            self._error = None
            self._ok = True
        return True

    def loop(self) -> None:
        while True:
            self.refresh()
            time.sleep(self.ttl)

    # ---- read side ------------------------------------------------------
    def site(self, site_id: str) -> dict | None:
        with self._lock:
            return dict(self._data[site_id]) if site_id in self._data else None

    def status(self) -> dict:
        with self._lock:
            return {"source": "Open-Meteo (weather, rainfall, river discharge)", "ok": self._ok,
                    "updated": self._updated, "error": self._error, "sites": len(self._data)}
