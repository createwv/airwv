"""Tests for the PurpleAir source parser and HTTP listing."""

import httpx
import respx

from airwv.sources.purpleair import (
    PURPLEAIR_API_BASE,
    PurpleAirSource,
    parse_history_payload,
    parse_sensor_payload,
)


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


@respx.mock
def test_list_sensors_hits_api_and_parses_records():
    route = respx.get(f"{PURPLEAIR_API_BASE}/sensors").mock(
        return_value=httpx.Response(
            200,
            json={
                "fields": ["sensor_index", "name", "latitude", "longitude"],
                "data": [[101, "EWV Belle 1", 38.23, -81.53]],
            },
        )
    )
    source = PurpleAirSource(api_key="test-key")

    records = source.list_sensors(40.64, -82.65, 37.20, -77.72)

    assert route.called
    assert records[0]["name"] == "EWV Belle 1"
    assert records[0]["sensor_index"] == 101


def test_parse_history_payload_maps_rows():
    payload = {
        "fields": ["time_stamp", "pm2.5_atm", "temperature", "humidity"],
        "data": [
            [1_700_000_000, 6.1, 70.0, 40.0],
            [1_700_003_600, 7.9, 71.0, 41.0],
        ],
    }

    readings = parse_history_payload(payload, sensor_index=101)

    assert len(readings) == 2
    assert readings[0].sensor_id == "101"
    assert readings[0].pm2_5 == 6.1
    assert readings[1].temperature == 71.0


def test_realtime_parses_channels_confidence_and_counts():
    payload = {
        "fields": ["sensor_index", "pm2.5", "pm2.5_a", "pm2.5_b", "confidence", "0.3_um_count"],
        "data": [[1, 8.0, 7.5, 8.5, 98, 1234]],
    }
    r = parse_sensor_payload(payload)[0]
    assert r.pm2_5_a == 7.5 and r.pm2_5_b == 8.5
    assert r.confidence == 98
    assert r.count_0_3 == 1234


def test_history_parses_channels_confidence_and_counts():
    payload = {
        "fields": ["time_stamp", "pm2.5_atm", "pm2.5_atm_a", "pm2.5_atm_b", "confidence", "2.5_um_count"],
        "data": [[1_700_000_000, 6.0, 5.8, 6.2, 95, 42]],
    }
    r = parse_history_payload(payload, sensor_index=5)[0]
    assert r.pm2_5_a == 5.8 and r.pm2_5_b == 6.2
    assert r.confidence == 95
    assert r.count_2_5 == 42
