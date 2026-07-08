"""Integration test: run_analyze over stored readings (no network)."""

from datetime import datetime, timedelta

from airwv.config import Config
from airwv.ingest import run_analyze
from airwv.sources.base import Reading
from airwv.storage import Store

_NOW = datetime(2026, 7, 8, 12, 0)  # naive UTC, matching SQLite


def _config(tmp_path) -> Config:
    return Config(
        purpleair_api_key="k",
        database_url=f"sqlite:///{tmp_path / 'a.sqlite'}",
        poll_interval_seconds=3600,
        index_cache_path=tmp_path / "m.json",
    )


def test_run_analyze_flags_spike_and_degraded_sensor(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    store.create_schema()

    # Sensor A: steady PM with one wild spike; healthy channels.
    vals = [10, 11, 9, 10, 12, 8, 11, 10, 9, 11, 10, 12, 1678.6, 10, 9]
    a = [
        Reading(source="purpleair", sensor_id="A", ts=_NOW - timedelta(hours=len(vals) - i),
                pm2_5=v, temperature=70, humidity=50)
        for i, v in enumerate(vals)
    ]
    # Sensor B: reports PM but never temp/humidity -> degraded.
    b = [
        Reading(source="purpleair", sensor_id="B", ts=_NOW - timedelta(hours=i), pm2_5=8.0)
        for i in range(3)
    ]
    store.save_readings(a + b)

    result = run_analyze(config, store=store, now=_NOW, since_hours=168)

    assert any(x.value == 1678.6 and x.kind == "spike" for x in result["anomalies"])
    statuses = {h.sensor_id: h.status for h in result["health"]}
    assert statuses["A"] == "ok"
    assert statuses["B"] == "degraded"
