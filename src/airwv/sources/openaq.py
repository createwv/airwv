"""OpenAQ data source — regulatory/reference-monitor readings.

OpenAQ aggregates official monitoring networks (incl. EPA), with real-time AND
historical data. We use it to pull reference-grade PM2.5 for WV so community
sensors can be validated against it. Requires a free API key (``OPENAQ_API_KEY``).

API v3: https://docs.openaq.org/  (X-API-Key header)

Note: parsing is written to the documented v3 shape but should be verified
against the live API on first use (as we did with PurpleAir).
"""

from __future__ import annotations

import time
from datetime import datetime

import httpx

from airwv.sources.base import Reading

OPENAQ_BASE = "https://api.openaq.org/v3"
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

    def __init__(self, api_key: str, timeout: float = 30.0):
        if not api_key:
            raise ValueError("OpenAQ API key is required (set OPENAQ_API_KEY)")
        self._api_key = api_key
        self._timeout = timeout

    def _get(self, path: str, params: dict, retries: int = 4, sleeper=time.sleep) -> dict:
        """GET with retry on 429 (free tier is ~60 req/min), honoring Retry-After."""
        for attempt in range(retries):
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(f"{OPENAQ_BASE}{path}",
                                  headers={"X-API-Key": self._api_key}, params=params)
            if resp.status_code == 429 and attempt < retries - 1:
                wait = float(resp.headers.get("Retry-After") or (2 ** attempt))
                sleeper(min(wait, 30))
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
