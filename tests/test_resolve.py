"""Tests for name -> sensor_index resolution and the private index cache."""

from airwv.registry import SensorInfo
from airwv.resolve import load_index_map, match_indices, save_index_map


def _sensor(name: str, device_id: str) -> SensorInfo:
    return SensorInfo(name=name, device_id=device_id, source="purpleair")


def test_match_indices_by_name_is_case_insensitive():
    sensors = [
        _sensor("EWV Belle 1", "AA"),
        _sensor("Luna Park - Glenwood", "BB"),
        _sensor("Not Deployed Yet", "CC"),
    ]
    records = [
        {"name": "EWV Belle 1", "sensor_index": 101},
        {"name": "luna park - glenwood", "sensor_index": 202},  # different case
        {"name": "Some Stranger's Sensor", "sensor_index": 303},
    ]

    result = match_indices(sensors, records)

    assert result.matched == {"AA": 101, "BB": 202}
    assert result.unmatched == ["CC"]


def test_index_map_round_trip(tmp_path):
    path = tmp_path / "nested" / "map.json"
    save_index_map(path, {"AA": 101, "BB": 202})
    assert load_index_map(path) == {"AA": 101, "BB": 202}


def test_load_missing_map_returns_empty(tmp_path):
    assert load_index_map(tmp_path / "missing.json") == {}
