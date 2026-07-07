"""Tests for the collector wiring (resolve + collect), using fakes — no network."""

from datetime import datetime, timezone

from airwv.config import Config
from airwv.ingest import run_collect, run_resolve
from airwv.registry import SensorInfo
from airwv.resolve import load_index_map
from airwv.sources.base import Reading
from airwv.storage import Store


def _config(tmp_path) -> Config:
    return Config(
        purpleair_api_key="test-key",
        database_url=f"sqlite:///{tmp_path / 'ingest.sqlite'}",
        poll_interval_seconds=300,
        index_cache_path=tmp_path / "map.json",
    )


class FakeSource:
    def __init__(self, readings=None, records=None):
        self._readings = readings or []
        self._records = records or []

    def fetch_current(self):
        return self._readings

    def list_sensors(self, *args, **kwargs):
        return self._records


def _reading(sensor_id: str) -> Reading:
    return Reading(
        source="purpleair",
        sensor_id=sensor_id,
        ts=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        pm2_5=8.0,
    )


def test_run_collect_stores_readings(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    source = FakeSource(readings=[_reading("1"), _reading("2")])

    inserted = run_collect(config, source=source, store=store, index_map={"AA": 1, "BB": 2})

    assert inserted == 2
    assert store.count() == 2


def test_run_collect_without_indices_is_noop(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)

    inserted = run_collect(config, source=FakeSource(), store=store, index_map={})

    assert inserted == 0


def test_run_resolve_matches_and_caches(tmp_path):
    config = _config(tmp_path)
    sensors = [SensorInfo(name="EWV Belle 1", device_id="AA", source="purpleair")]
    source = FakeSource(records=[{"name": "EWV Belle 1", "sensor_index": 101}])

    result = run_resolve(config, source=source, sensors=sensors)

    assert result.matched == {"AA": 101}
    assert load_index_map(config.index_cache_path) == {"AA": 101}
