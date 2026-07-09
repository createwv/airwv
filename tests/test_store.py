"""Tests for the storage layer using a temporary SQLite database."""

from datetime import datetime, timezone

import pytest

from airwv.sources.base import Reading
from airwv.storage import Store


def _reading(sensor_id: str = "1", ts: datetime | None = None, pm2_5: float = 8.4) -> Reading:
    return Reading(
        source="purpleair",
        sensor_id=sensor_id,
        ts=ts or datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        lat=38.35,
        lon=-81.63,
        pm2_5=pm2_5,
        raw={"sensor_index": sensor_id},
    )


@pytest.fixture
def store(tmp_path):
    s = Store(f"sqlite:///{tmp_path / 'test.sqlite'}")
    s.create_schema()
    return s


def test_save_and_count(store):
    inserted = store.save_readings([_reading("1"), _reading("2")])
    assert inserted == 2
    assert store.count() == 2


def test_reingest_upserts_and_enriches(store):
    assert store.save_readings([_reading("1", pm2_5=8.0)]) == 1
    # Same source/sensor/ts -> no duplicate; the row is updated in place.
    store.save_readings([_reading("1", pm2_5=99.9)])
    assert store.count() == 1
    (row,) = store.recent(limit=1)
    assert row.pm2_5 == 99.9  # enriched/overwritten, not discarded


def test_values_round_trip(store):
    store.save_readings([_reading("42", pm2_5=12.5)])
    (row,) = store.recent(limit=1)
    assert row.source == "purpleair"
    assert row.sensor_id == "42"
    assert row.pm2_5 == 12.5
    assert row.quality == "ok"
    assert row.raw == {"sensor_index": "42"}
    assert row.ingested_at is not None


def test_save_empty_is_noop(store):
    assert store.save_readings([]) == 0
    assert store.count() == 0


def test_in_range_reading_stored_as_ok(store):
    store.save_readings([_reading("ok1", pm2_5=10.0)])
    (row,) = store.recent(limit=1)
    assert row.quality == "ok"


def test_out_of_range_reading_flagged_suspect_not_dropped(store):
    store.save_readings([_reading("bad1", pm2_5=-5.0)])
    (row,) = store.recent(limit=1)
    assert row.quality == "suspect"  # flagged, not dropped
    assert store.count() == 1
    assert row.pm2_5 == -5.0  # raw value preserved
