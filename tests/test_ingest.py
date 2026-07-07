"""Tests for the collector wiring (resolve + collect), using fakes — no network."""

from datetime import datetime, timezone

from airwv.config import Config
from airwv.ingest import collect_with_retry, run_collect, run_resolve, run_scheduler
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


class FlakySource:
    """Fails ``fail_times`` then returns readings — to exercise retry/backoff."""

    def __init__(self, fail_times: int, readings):
        self._fail_times = fail_times
        self._readings = readings
        self.calls = 0

    def fetch_current(self):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("transient")
        return self._readings


def test_collect_with_retry_recovers(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    source = FlakySource(fail_times=2, readings=[_reading("1")])
    delays = []

    inserted = collect_with_retry(
        config, source=source, store=store, index_map={"AA": 1},
        sleeper=delays.append,
    )

    assert inserted == 1
    assert source.calls == 3  # 2 failures + 1 success
    assert delays == [2.0, 4.0]  # exponential backoff


def test_collect_with_retry_gives_up(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    source = FlakySource(fail_times=99, readings=[_reading("1")])

    inserted = collect_with_retry(
        config, source=source, store=store, index_map={"AA": 1},
        attempts=3, sleeper=lambda _: None,
    )

    assert inserted == 0  # never succeeded, but did not raise


def test_run_scheduler_runs_fixed_iterations(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    calls = {"n": 0}

    def fake_collect(cfg, store=None):
        calls["n"] += 1
        return 0

    sleeps = []
    run_scheduler(config, iterations=3, sleeper=sleeps.append, collect=fake_collect, store=store)

    assert calls["n"] == 3
    assert sleeps == [config.poll_interval_seconds, config.poll_interval_seconds]  # no sleep after last
