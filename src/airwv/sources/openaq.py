"""OpenAQ data source — regulatory/reference-monitor readings.

OpenAQ aggregates official monitoring networks (incl. EPA), with real-time AND
historical data. We use it to pull reference-grade PM2.5 for WV so community
sensors can be validated against it. Requires a free API key (``OPENAQ_API_KEY``).

API v3: https://docs.openaq.org/  (X-API-Key header)

Note: parsing is written to the documented v3 shape but should be verified
against the live API on first use (as we did with PurpleAir).
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import httpx

from airwv.sources.base import Reading

OPENAQ_BASE = "https://api.openaq.org/v3"


class OpenAQAuthError(RuntimeError):
    """OpenAQ rejected the key (401/403) — invalid or account suspended. Stop now."""


class OpenAQBudgetExceeded(RuntimeError):
    """Today's request budget is used up — stop until tomorrow (never exceed quota)."""
PM25_PARAMETER_ID = 2  # OpenAQ parameter id for pm25


def _measurement_ts(m: dict) -> datetime | None:
    """Pull a naive-UTC timestamp from a measurement (v3 shapes vary a little)."""
    for path in (("period", "datetimeFrom", "utc"), ("date", "utc"), ("datetime", "utc")):
        node = m
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, str):
            try:
                return datetime.fromisoformat(node.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                continue
    return None


def parse_measurements(payload: dict, sensor_id, source: str = "openaq", lat=None, lon=None) -> list[Reading]:
    readings: list[Reading] = []
    for m in payload.get("results", []):
        ts = _measurement_ts(m)
        value = m.get("value")
        if ts is None or value is None:
            continue
        readings.append(Reading(source=source, sensor_id=str(sensor_id), ts=ts,
                                pm2_5=float(value), lat=lat, lon=lon))
    return readings


class OpenAQSource:
    """Fetch reference-monitor PM2.5 from OpenAQ."""

    name = "openaq"

    def __init__(self, api_key: str, timeout: float = 30.0, min_interval: float = 2.0,
                 daily_cap: int = 1000, usage_path=None):
        # OpenAQ free tier = 60 req/min AND 2000 req/hour. min_interval 2.0s = 30/min
        # and ≤1800/hour — comfortably under BOTH. (The old 1.1s = ~3270/hour, which
        # blew the hourly limit and got us banned.) daily_cap is our own belt-and-
        # suspenders so we never "leave requests running in perpetuity" (their ToS).
        if not api_key:
            raise ValueError("OpenAQ API key is required (set OPENAQ_API_KEY)")
        self._api_key = api_key
        self._timeout = timeout
        self._min_interval = min_interval  # proactive throttle: free tier is ~60/min
        self._last_request = 0.0           # time.monotonic() of the last request
        # HARD daily request budget — persisted so it holds across runs (the timer
        # makes a fresh client each hour). We never send more than daily_cap/day.
        self._daily_cap = daily_cap
        self._usage_path = Path(usage_path) if usage_path else None
        self._usage = self._load_usage()

    def _load_usage(self) -> dict:
        if self._usage_path and self._usage_path.exists():
            try:
                return json.loads(self._usage_path.read_text())
            except Exception:
                return {}
        return {}

    def _used_today(self) -> int:
        return int(self._usage.get(date.today().isoformat(), 0))

    def remaining_today(self) -> int:
        return max(0, self._daily_cap - self._used_today()) if self._daily_cap else 1 << 30

    def _record_request(self) -> None:
        today = date.today().isoformat()
        self._usage = {today: self._usage.get(today, 0) + 1}  # keep only today's tally
        if self._usage_path:
            try:
                self._usage_path.write_text(json.dumps(self._usage))
            except Exception:
                pass

    def _throttle(self, sleeper) -> None:
        """Space requests ≥ min_interval apart so we stay under the rate limit."""
        if self._min_interval > 0:
            gap = self._min_interval - (time.monotonic() - self._last_request)
            if gap > 0:
                sleeper(gap)
        self._last_request = time.monotonic()

    def _get(self, path: str, params: dict, retries: int = 4, sleeper=time.sleep) -> dict:
        """GET with a hard daily budget, per-minute throttle, 429 retry, and a
        stop-immediately circuit breaker on 401/403 (invalid/suspended key)."""
        if self._daily_cap and self._used_today() >= self._daily_cap:
            raise OpenAQBudgetExceeded(
                f"daily OpenAQ request cap ({self._daily_cap}) reached — stopping until tomorrow")
        for attempt in range(retries):
            self._throttle(sleeper)
            self._record_request()  # count every request we actually send
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(f"{OPENAQ_BASE}{path}",
                                  headers={"X-API-Key": self._api_key}, params=params)
            if resp.status_code in (401, 403):
                raise OpenAQAuthError(
                    f"OpenAQ returned {resp.status_code} — key invalid or account suspended: {resp.text[:120]}")
            if resp.status_code == 429 and attempt < retries - 1:
                wait = float(resp.headers.get("Retry-After") or (5 * 2 ** attempt))
                sleeper(min(wait, 120))
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    def fetch_locations(self, nw_lat, nw_lng, se_lat, se_lng, parameter_id: int = PM25_PARAMETER_ID) -> list[dict]:
        """Reference-monitor locations in a bbox, with their PM2.5 sensor ids."""
        bbox = f"{min(nw_lng, se_lng)},{min(nw_lat, se_lat)},{max(nw_lng, se_lng)},{max(nw_lat, se_lat)}"
        payload = self._get("/locations", {"bbox": bbox, "parameters_id": parameter_id, "limit": 1000})
        out = []
        for loc in payload.get("results", []):
            coords = loc.get("coordinates") or {}
            sensor_ids = [
                s["id"] for s in loc.get("sensors", [])
                if (s.get("parameter") or {}).get("id") == parameter_id and s.get("id") is not None
            ]
            out.append({
                "id": loc.get("id"),
                "name": loc.get("name"),
                "lat": coords.get("latitude"),
                "lon": coords.get("longitude"),
                "pm25_sensor_ids": sensor_ids,
            })
        return out

    def fetch_measurements(self, sensor_id, start: datetime, end: datetime,
                           lat=None, lon=None) -> list[Reading]:
        """Hourly PM2.5 measurements for one sensor in [start, end]."""
        payload = self._get(
            f"/sensors/{sensor_id}/measurements",
            {"datetime_from": start.isoformat(), "datetime_to": end.isoformat(), "limit": 1000},
        )
        return parse_measurements(payload, sensor_id, self.name, lat=lat, lon=lon)
