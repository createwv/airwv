"""Tests for the WV sensor registry loader (and a PII guard).

These run against whichever registry is present — the private ``wv_sensors.json``
locally, or the committed ``wv_sensors.sample.json`` in CI — so they must not
assume the real sensor count.
"""

from dataclasses import fields

from airwv.registry import SensorInfo, device_ids, load_wv_sensors

# Fields that must never appear in the registry — this test fails loudly if
# anyone adds personal data to SensorInfo or the JSON schema.
PII_FIELDS = {"name_contact", "phone", "phone1", "phone2", "email", "email1", "email2", "address"}


def test_registry_loads_sensors():
    sensors = load_wv_sensors()
    assert len(sensors) >= 1
    assert all(isinstance(s, SensorInfo) for s in sensors)


def test_every_sensor_has_key_operational_fields():
    for s in load_wv_sensors():
        assert s.name
        assert s.device_id
        assert s.source == "purpleair"
        assert s.org  # installing org is retained for attribution


def test_no_pii_fields_in_schema():
    field_names = {f.name for f in fields(SensorInfo)}
    assert field_names.isdisjoint(PII_FIELDS), "SensorInfo must not carry PII"


def test_device_ids_match_sensor_count():
    assert len(device_ids()) == len(load_wv_sensors())
