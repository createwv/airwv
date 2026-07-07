"""PurpleAir data source.

Wraps the PurpleAir REST API (https://api.purpleair.com/). Requires a read API
key, supplied via configuration — never hard-coded. This is the first source
integration and currently sketches the ``fetch_current`` path; historical
backfill (Roadmap Phase 1) will build on the same client.

API reference: https://api.purpleair.com/
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from airwv.sources.base import Reading, Source

PURPLEAIR_API_BASE = "https://api.purpleair.com/v1"

# Fields we request from PurpleAir for each sensor. Extend as we map more.
SENSOR_FIELDS = [
    "sensor_index",
    "latitude",
    "longitude",
    "last_seen",
    "pm1.0",
    "pm2.5",
    "pm10.0",
    "humidity",
    "temperature",
    "pressure",
    "voc",
]


class PurpleAirSource(Source):
    """Fetch readings from PurpleAir sensors."""

    name = "purpleair"

    def __init__(self, api_key: str, sensor_ids: list[int] | None = None, timeout: float = 30.0):
        if not api_key:
            raise ValueError("PurpleAir API key is required")
        self._api_key = api_key
        # When None, ingestion will supply the statewide WV sensor list.
        self._sensor_ids = sensor_ids or []
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self._api_key}

    def fetch_current(self) -> list[Reading]:
        """Fetch the latest reading for each configured sensor.

        Uses the ``/sensors`` endpoint with a ``show_only`` filter. If no sensor
        ids are configured this returns an empty list — ingestion is responsible
        for providing the WV sensor set.
        """
        if not self._sensor_ids:
            return []

        params = {
            "fields": ",".join(SENSOR_FIELDS),
            "show_only": ",".join(str(s) for s in self._sensor_ids),
        }
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                f"{PURPLEAIR_API_BASE}/sensors",
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            payload = resp.json()

        return self._parse_sensors_response(payload)

    def _parse_sensors_response(self, payload: dict) -> list[Reading]:
        """Turn a ``/sensors`` response into normalized readings.

        PurpleAir returns column-oriented data: a ``fields`` list plus a ``data``
        list of rows. We zip each row back into a dict before mapping.
        """
        fields: list[str] = payload.get("fields", [])
        rows: list[list] = payload.get("data", [])
        readings: list[Reading] = []

        for row in rows:
            record = dict(zip(fields, row))
            last_seen = record.get("last_seen")
            ts = (
                datetime.fromtimestamp(last_seen, tz=timezone.utc)
                if isinstance(last_seen, (int, float))
                else datetime.now(tz=timezone.utc)
            )
            readings.append(
                Reading(
                    source=self.name,
                    sensor_id=str(record.get("sensor_index")),
                    ts=ts,
                    lat=record.get("latitude"),
                    lon=record.get("longitude"),
                    pm1_0=record.get("pm1.0"),
                    pm2_5=record.get("pm2.5"),
                    pm10=record.get("pm10.0"),
                    voc=record.get("voc"),
                    temperature=record.get("temperature"),
                    humidity=record.get("humidity"),
                    pressure=record.get("pressure"),
                    raw=record,
                )
            )
        return readings
