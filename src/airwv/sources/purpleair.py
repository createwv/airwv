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
    "name",
    "latitude",
    "longitude",
    "last_seen",
    "pm1.0",
    "pm2.5",
    "pm2.5_a",
    "pm2.5_b",
    "pm10.0",
    "humidity",
    "temperature",
    "pressure",
    "voc",
    "confidence",
    "0.3_um_count",
    "0.5_um_count",
    "1.0_um_count",
    "2.5_um_count",
    "5.0_um_count",
    "10.0_um_count",
]

# Minimal fields for resolving our device names to PurpleAir sensor indices.
RESOLVE_FIELDS = ["name", "latitude", "longitude"]

# Fields requested from the per-sensor history endpoint. History uses suffixed
# names (e.g. pm2.5_atm) distinct from the realtime fields.
# NOTE: 'confidence' is a realtime-only field — NOT valid for /history (verified
# against the API). It stays in SENSOR_FIELDS but must not be requested here.
HISTORY_FIELDS = [
    "pm1.0_atm",
    "pm2.5_atm",
    "pm2.5_atm_a",
    "pm2.5_atm_b",
    "pm10.0_atm",
    "humidity",
    "temperature",
    "pressure",
    "voc",
    "0.3_um_count",
    "0.5_um_count",
    "1.0_um_count",
    "2.5_um_count",
    "5.0_um_count",
    "10.0_um_count",
]


def _first(record: dict, *keys: str):
    """Return the first present, non-null value among ``keys``."""
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return None


def _extended_fields(record: dict, a_keys: tuple[str, ...], b_keys: tuple[str, ...]) -> dict:
    """A/B channel PM2.5, confidence, and particle counts common to both endpoints."""
    return {
        "pm2_5_a": _first(record, *a_keys),
        "pm2_5_b": _first(record, *b_keys),
        "confidence": record.get("confidence"),
        "count_0_3": record.get("0.3_um_count"),
        "count_0_5": record.get("0.5_um_count"),
        "count_1_0": record.get("1.0_um_count"),
        "count_2_5": record.get("2.5_um_count"),
        "count_5_0": record.get("5.0_um_count"),
        "count_10_0": record.get("10.0_um_count"),
    }


def parse_history_payload(payload: dict, sensor_index: int, source: str = "purpleair") -> list[Reading]:
    """Turn a ``/sensors/{index}/history`` response into normalized readings.

    History rows carry ``time_stamp`` (not ``last_seen``) and no per-row sensor
    index, so the index is supplied by the caller. PM field names are tried in a
    few forms for resilience across API variants.
    """
    readings: list[Reading] = []
    for record in _records_from_payload(payload):
        ts_raw = record.get("time_stamp")
        ts = (
            datetime.fromtimestamp(ts_raw, tz=timezone.utc)
            if isinstance(ts_raw, (int, float))
            else datetime.now(tz=timezone.utc)
        )
        readings.append(
            Reading(
                source=source,
                sensor_id=str(sensor_index),
                ts=ts,
                pm1_0=_first(record, "pm1.0_atm", "pm1.0"),
                pm2_5=_first(record, "pm2.5_atm", "pm2.5"),
                pm10=_first(record, "pm10.0_atm", "pm10.0"),
                voc=record.get("voc"),
                temperature=record.get("temperature"),
                humidity=record.get("humidity"),
                pressure=record.get("pressure"),
                raw=record,
                **_extended_fields(record, ("pm2.5_atm_a", "pm2.5_a"), ("pm2.5_atm_b", "pm2.5_b")),
            )
        )
    return readings


def _records_from_payload(payload: dict) -> list[dict]:
    """PurpleAir returns column-oriented data (``fields`` + rows). Zip to dicts."""
    fields: list[str] = payload.get("fields", [])
    rows: list[list] = payload.get("data", [])
    return [dict(zip(fields, row)) for row in rows]


def parse_sensor_payload(payload: dict, source: str = "purpleair") -> list[Reading]:
    """Turn a ``/sensors`` (or group members) response into normalized readings."""
    readings: list[Reading] = []
    for record in _records_from_payload(payload):
        last_seen = record.get("last_seen")
        ts = (
            datetime.fromtimestamp(last_seen, tz=timezone.utc)
            if isinstance(last_seen, (int, float))
            else datetime.now(tz=timezone.utc)
        )
        readings.append(
            Reading(
                source=source,
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
                **_extended_fields(record, ("pm2.5_a",), ("pm2.5_b",)),
            )
        )
    return readings


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
        payload = self._get("/sensors", params)
        return parse_sensor_payload(payload, self.name)

    def list_sensors(
        self,
        nw_lat: float,
        nw_lng: float,
        se_lat: float,
        se_lng: float,
        max_age: int = 0,
    ) -> list[dict]:
        """List sensors within a bounding box (name + index + location).

        Used to resolve our device names to PurpleAir ``sensor_index`` values,
        since public sensors are read by index. Returns raw record dicts.

        ``max_age`` filters to sensors seen within that many seconds; PurpleAir
        defaults to one week, which hides offline sensors. We pass ``0`` (any age)
        so we can still resolve — and later backfill — sensors that are currently
        down but were online in the past.
        """
        params = {
            "fields": ",".join(RESOLVE_FIELDS),
            "nwlng": nw_lng,
            "nwlat": nw_lat,
            "selng": se_lng,
            "selat": se_lat,
            "max_age": max_age,
        }
        payload = self._get("/sensors", params)
        return _records_from_payload(payload)

    def fetch_sensor_history(
        self,
        sensor_index: int,
        start: datetime,
        end: datetime,
        average_minutes: int = 60,
    ) -> list[Reading]:
        """Fetch historical readings for one sensor in ``[start, end]``.

        ``average_minutes`` is a PurpleAir averaging bucket (10, 30, 60, 360, or
        1440). Returns normalized readings; the caller handles windowing/storage.
        """
        params = {
            "start_timestamp": int(start.timestamp()),
            "end_timestamp": int(end.timestamp()),
            "average": average_minutes,
            "fields": ",".join(HISTORY_FIELDS),
        }
        payload = self._get(f"/sensors/{sensor_index}/history", params)
        return parse_history_payload(payload, sensor_index, self.name)

    def _get(self, path: str, params: dict) -> dict:
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                f"{PURPLEAIR_API_BASE}{path}",
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    # Kept for backwards compatibility with existing callers/tests.
    def _parse_sensors_response(self, payload: dict) -> list[Reading]:
        return parse_sensor_payload(payload, self.name)
