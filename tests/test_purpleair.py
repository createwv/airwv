"""Tests for the PurpleAir source parser (no network required)."""

from airwv.sources.purpleair import PurpleAirSource


def test_parse_sensors_response_maps_fields():
    source = PurpleAirSource(api_key="test-key")
    payload = {
        "fields": [
            "sensor_index",
            "latitude",
            "longitude",
            "last_seen",
            "pm2.5",
            "temperature",
        ],
        "data": [
            [12345, 38.35, -81.63, 1_700_000_000, 8.4, 72.0],
        ],
    }

    readings = source._parse_sensors_response(payload)

    assert len(readings) == 1
    r = readings[0]
    assert r.source == "purpleair"
    assert r.sensor_id == "12345"
    assert r.lat == 38.35
    assert r.pm2_5 == 8.4
    assert r.temperature == 72.0
    assert r.raw["sensor_index"] == 12345


def test_fetch_current_with_no_sensors_returns_empty():
    source = PurpleAirSource(api_key="test-key")
    assert source.fetch_current() == []
