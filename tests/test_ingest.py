"""Tests for the collector wiring (resolve + collect), using fakes — no network."""

from datetime import datetime, timezone

from airwv.config import Config
from airwv.ingest import (
    _scoped_index_map,
    _time_windows,
    collect_with_retry,
    run_backfill,
    run_collect,
    run_resolve,
    run_scheduler,
)
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
        self.history_calls = []

    def fetch_current(self):
        return self._readings

    def list_sensors(self, *args, **kwargs):
        return self._records

    def fetch_sensor_history(self, sensor_index, start, end, average_minutes=60):
        self.history_calls.append((sensor_index, start, end))
        # One distinct reading per window (ts = window start) so dedupe keeps all.
        return [Reading(source="purpleair", sensor_id=str(sensor_index), ts=start, pm2_5=5.0)]


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

    alert_calls = {"n": 0}

    def fake_alerts(cfg, send=False, store=None):
        alert_calls["n"] += 1
        return []

    sleeps = []
    run_scheduler(config, iterations=3, sleeper=sleeps.append, collect=fake_collect,
                  store=store, alerts=fake_alerts)

    assert calls["n"] == 3
    assert alert_calls["n"] == 3  # alerts evaluated after each collect
    assert sleeps == [config.poll_interval_seconds, config.poll_interval_seconds]  # no sleep after last


def test_run_scheduler_can_disable_alerts(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    alert_calls = {"n": 0}

    def fake_alerts(cfg, send=False, store=None):
        alert_calls["n"] += 1

    run_scheduler(config, iterations=2, sleeper=lambda _: None,
                  collect=lambda cfg, store=None: 0, store=store,
                  send_alerts=False, alerts=fake_alerts)
    assert alert_calls["n"] == 0


def test_time_windows_chunks_range():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 31, tzinfo=timezone.utc)

    windows = list(_time_windows(start, end, 14))

    assert len(windows) == 3  # 14 + 14 + 2 days
    assert windows[0][0] == start
    assert windows[-1][1] == end


def test_run_backfill_windows_and_stores(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    source = FakeSource()
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)

    total = run_backfill(
        config, source=source, store=store, index_map={"AA": 1, "BB": 2},
        days=30, window_days=14, now=now,
    )

    # 3 windows/sensor x 2 sensors, each returning 1 distinct reading
    assert len(source.history_calls) == 6
    assert total == 6
    assert store.count() == 6


def test_run_backfill_without_indices_is_noop(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)

    assert run_backfill(config, source=FakeSource(), store=store, index_map={}) == 0


def test_scoped_index_map_filters_by_org(tmp_path):
    config = _config(tmp_path)
    sensors = [
        SensorInfo(name="EWV Belle 1", device_id="AA", source="purpleair", org="Create WV"),
        SensorInfo(name="EWV Washington 1", device_id="BB", source="purpleair", org="WVCAG"),
    ]
    scoped = _scoped_index_map(
        config, org="Create WV", sensors=sensors, index_map={"AA": 1, "BB": 2}
    )
    assert scoped == {"AA": 1}


def test_scoped_index_map_filters_by_sensor_name(tmp_path):
    config = _config(tmp_path)
    sensors = [
        SensorInfo(name="EWV Glasgow 1", device_id="AA", source="purpleair", org="Create WV"),
        SensorInfo(name="EWV Belle 1", device_id="BB", source="purpleair", org="Create WV"),
    ]
    scoped = _scoped_index_map(
        config, names=["glasgow"], sensors=sensors, index_map={"AA": 1, "BB": 2}
    )
    assert scoped == {"AA": 1}


def test_run_backfill_with_explicit_date_range(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    source = FakeSource()
    start = datetime(2023, 11, 1, tzinfo=timezone.utc)
    end = datetime(2023, 11, 15, tzinfo=timezone.utc)

    run_backfill(config, source=source, store=store, index_map={"AA": 1},
                 start=start, end=end, window_days=14)

    assert len(source.history_calls) == 1  # one 14-day window
    _, w_start, w_end = source.history_calls[0]
    assert w_start == start and w_end == end


def test_run_backfill_skips_windows_already_stored(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    store.create_schema()
    # Pre-store a reading for sensor index 1 inside the first window.
    store.save_readings([Reading(source="purpleair", sensor_id="1",
                                 ts=datetime(2024, 11, 5, tzinfo=timezone.utc), pm2_5=5.0)])
    source = FakeSource()
    now = datetime(2024, 12, 1, tzinfo=timezone.utc)

    run_backfill(config, source=source, store=store, index_map={"AA": 1},
                 days=30, window_days=14, now=now)

    fetched = {c[1].date() for c in source.history_calls}
    from datetime import date
    assert date(2024, 11, 1) not in fetched  # window with existing data was skipped
    assert date(2024, 11, 15) in fetched      # empty window still fetched


def test_run_backfill_refresh_repulls_stored_windows(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    store.create_schema()
    store.save_readings([Reading(source="purpleair", sensor_id="1",
                                 ts=datetime(2024, 11, 5, tzinfo=timezone.utc), pm2_5=5.0)])
    source = FakeSource()
    now = datetime(2024, 12, 1, tzinfo=timezone.utc)

    run_backfill(config, source=source, store=store, index_map={"AA": 1},
                 days=30, window_days=14, now=now, refresh=True)

    from datetime import date
    fetched = {c[1].date() for c in source.history_calls}
    assert date(2024, 11, 1) in fetched  # --refresh re-pulls even stored windows


def test_run_backfill_limit_caps_sensors(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    source = FakeSource()
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)

    run_backfill(
        config, source=source, store=store, index_map={"AA": 1, "BB": 2, "CC": 3},
        days=14, window_days=14, limit=1, now=now,
    )

    # Only the first sensor (index 1) should be fetched.
    assert {call[0] for call in source.history_calls} == {1}
