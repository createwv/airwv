"""Tests for readings export and the network-baseline comparison."""

from datetime import datetime, timedelta, timezone

from airwv.analysis.regional import compare_to_baseline, sensor_medians
from airwv.config import Config
from airwv.export_utils import readings_to_csv, readings_to_records
from airwv.ingest import run_baseline, run_export
from airwv.sources.base import Reading
from airwv.storage import Store

_T0 = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _config(tmp_path):
    return Config(
        purpleair_api_key="k",
        database_url=f"sqlite:///{tmp_path / 'x.sqlite'}",
        poll_interval_seconds=3600,
        index_cache_path=tmp_path / "m.json",
    )


def _store(tmp_path):
    store = Store(_config(tmp_path).database_url)
    store.create_schema()
    store.save_readings([
        Reading(source="purpleair", sensor_id="A", ts=_T0 + timedelta(hours=i), pm2_5=10.0)
        for i in range(5)
    ] + [
        Reading(source="purpleair", sensor_id="B", ts=_T0 + timedelta(hours=i), pm2_5=30.0)
        for i in range(5)
    ])
    return store


def test_readings_to_csv_and_records(tmp_path):
    rows = _store(tmp_path).readings_for_sensor("A")
    csv = readings_to_csv(rows)
    assert csv.splitlines()[0].startswith("ts,source,sensor_id")
    assert len(csv.strip().splitlines()) == 6  # header + 5
    recs = readings_to_records(rows)
    assert recs[0]["sensor_id"] == "A"
    assert recs[0]["pm2_5"] == 10.0


def test_run_export_writes_file(tmp_path):
    config = _config(tmp_path)
    store = _store(tmp_path)
    out = tmp_path / "out.csv"
    n = run_export(config, str(out), store=store)
    assert n == 10
    assert out.exists() and "sensor_id" in out.read_text()


def test_baseline_flags_elevated_sensor(tmp_path):
    medians = sensor_medians({"A": _store(tmp_path).readings_for_sensor("A"),
                              "B": _store(tmp_path).readings_for_sensor("B")}, "pm2_5")
    result = compare_to_baseline(medians)
    # baseline = median(10, 30) = 20; B is +50%
    assert result["baseline"] == 20.0
    assert result["sensors"]["B"]["pct_vs_baseline"] == 50.0


def test_run_baseline_runs(tmp_path):
    result = run_baseline(_config(tmp_path), field="pm2_5", store=_store(tmp_path))
    assert result["baseline"] == 20.0
