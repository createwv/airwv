"""Tests for the OpenAQ reference source (parsing + wiring, no network)."""

from datetime import datetime, timezone

import httpx
import respx

from airwv.config import Config
from airwv.ingest import run_reference
from airwv.sources.base import Reading
from airwv.sources.openaq import OPENAQ_BASE, OpenAQSource, parse_measurements
from airwv.storage import Store


def test_parse_measurements_reads_value_and_time():
    payload = {"results": [
        {"value": 7.5, "period": {"datetimeFrom": {"utc": "2026-07-01T12:00:00Z"}}},
        {"value": 9.1, "date": {"utc": "2026-07-01T13:00:00Z"}},
        {"value": None, "period": {"datetimeFrom": {"utc": "2026-07-01T14:00:00Z"}}},  # skipped
    ]}
    readings = parse_measurements(payload, sensor_id=42)
    assert len(readings) == 2
    assert readings[0].source == "openaq"
    assert readings[0].sensor_id == "42"
    assert readings[0].pm2_5 == 7.5
    assert readings[0].ts == datetime(2026, 7, 1, 12, 0)  # naive UTC


@respx.mock
def test_fetch_locations_parses_pm25_sensors():
    respx.get(f"{OPENAQ_BASE}/locations").mock(return_value=httpx.Response(200, json={
        "results": [{
            "id": 100, "name": "Charleston Dixie St",
            "coordinates": {"latitude": 38.35, "longitude": -81.62},
            "sensors": [{"id": 555, "parameter": {"id": 2, "name": "pm25"}},
                        {"id": 556, "parameter": {"id": 1, "name": "pm10"}}],
        }]
    }))
    locs = OpenAQSource(api_key="k").fetch_locations(40.6, -82.6, 37.2, -77.7)
    assert locs[0]["name"] == "Charleston Dixie St"
    assert locs[0]["pm25_sensor_ids"] == [555]  # only the pm25 sensor


def _config(tmp_path):
    return Config(purpleair_api_key="k", database_url=f"sqlite:///{tmp_path / 'r.sqlite'}",
                  poll_interval_seconds=3600, index_cache_path=tmp_path / "m.json", openaq_api_key="oa")


class _FakeOpenAQ:
    def fetch_locations(self, *a, **k):
        return [{"id": 1, "name": "Ref", "lat": 38.3, "lon": -81.6, "pm25_sensor_ids": [9]}]

    def fetch_measurements(self, sensor_id, start, end):
        return [Reading(source="openaq", sensor_id="9",
                        ts=datetime(2026, 7, 1, 12, 0), pm2_5=7.0)]


def test_run_reference_stores_openaq_readings(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    n = run_reference(config, source=_FakeOpenAQ(), store=store, now=datetime(2026, 7, 2, tzinfo=timezone.utc))
    assert n == 1
    assert store.readings_for_sensor("9")[0].source == "openaq"


def test_run_reference_no_key_is_noop(tmp_path):
    config = Config(purpleair_api_key="k", database_url=f"sqlite:///{tmp_path / 'r.sqlite'}",
                    poll_interval_seconds=3600, index_cache_path=tmp_path / "m.json", openaq_api_key="")
    assert run_reference(config) == 0
