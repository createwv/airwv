"""Community-sensor validation against EPA reference monitors (run_validate, no network)."""

import json
from datetime import datetime

from airwv.config import Config
from airwv.ingest import run_validate
from airwv.sources.base import Reading
from airwv.storage import Store


def _config(tmp_path):
    return Config(purpleair_api_key="k", database_url=f"sqlite:///{tmp_path / 'r.sqlite'}",
                  poll_interval_seconds=3600, index_cache_path=tmp_path / "m.json")


def test_run_validate_correlates_sensor_to_nearest_monitor(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    store.create_schema()
    # community sensor coords come from the resolved public-sensor listing
    (tmp_path / "wv_public_sensors.json").write_text(json.dumps(
        [{"sensor_index": 111, "latitude": 38.35, "longitude": -81.63}]))

    # 8 days of daily readings: community tracks reference + a fixed +4 high bias.
    # Reference monitors are EPA (AirNow live / AirData history) — validate is source-agnostic.
    base = [5, 8, 12, 6, 20, 9, 7, 15]
    for i, v in enumerate(base):
        ts = datetime(2026, 6, 1 + i, 12, 0)
        store.save_readings([Reading(source="purpleair", sensor_id="111", ts=ts, pm2_5=v + 4)])
        store.save_readings([Reading(source="airnow", sensor_id="9", ts=ts, pm2_5=v,
                                     lat=38.36, lon=-81.62)])   # ~1 km away → chosen
        store.save_readings([Reading(source="airnow", sensor_id="99", ts=ts, pm2_5=v,
                                     lat=39.9, lon=-79.9)])     # far monitor → not chosen

    results = run_validate(config, min_days=5, store=store)
    assert len(results) == 1
    v = results[0]
    assert v["sensor"] == "111" and v["monitor"] == "9"   # nearest monitor picked
    assert v["r"] > 0.99                                    # tracks reference
    assert v["bias"] == 4.0                                 # recovers the +4 offset
