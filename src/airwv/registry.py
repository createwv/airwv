"""The registry of AirWV community sensors deployed across West Virginia.

The real registry (``data/wv_sensors.json``) is **kept private and gitignored**:
its ``device_id`` values are PurpleAir device MAC addresses, which are used to
register/claim a device — not something we want public. A committed example
(``data/wv_sensors.sample.json``) documents the schema and lets tests/CI run
without the private data. When the private file is present it is used; otherwise
the loader falls back to the example.

Every entry is operational only — no resident names, addresses, contact info, or
personal notes. To publish the registry later, drop the ignore rule (and consider
omitting ``device_id`` so MACs stay private even then).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

_REGISTRY_FILE = "wv_sensors.json"
_SAMPLE_FILE = "wv_sensors.sample.json"


@dataclass(frozen=True)
class SensorInfo:
    """Operational metadata for one deployed sensor (no PII)."""

    name: str
    device_id: str
    source: str
    model: str | None = None
    city: str | None = None
    county: str | None = None
    sub_region: str | None = None
    zip: str | None = None
    org: str | None = None
    pollution_source: str | None = None
    date_installed: str | None = None
    known_status: str | None = None


def _registry_resource():
    """The private registry if present, else the committed example."""
    data = resources.files("airwv.data")
    real = data.joinpath(_REGISTRY_FILE)
    return real if real.is_file() else data.joinpath(_SAMPLE_FILE)


def using_sample_registry() -> bool:
    """True when the private registry is absent and the example is in use."""
    return not resources.files("airwv.data").joinpath(_REGISTRY_FILE).is_file()


def load_wv_sensors() -> list[SensorInfo]:
    """Return all registered WV sensors."""
    payload = json.loads(_registry_resource().read_text(encoding="utf-8"))
    source = payload.get("source", "purpleair")
    model = payload.get("model")

    sensors: list[SensorInfo] = []
    for entry in payload.get("sensors", []):
        sensors.append(
            SensorInfo(
                name=entry["name"],
                device_id=entry["device_id"],
                source=source,
                model=model,
                city=entry.get("city"),
                county=entry.get("county"),
                sub_region=entry.get("sub_region"),
                zip=entry.get("zip"),
                org=entry.get("org"),
                pollution_source=entry.get("pollution_source"),
                date_installed=entry.get("date_installed"),
                known_status=entry.get("known_status"),
            )
        )
    return sensors


def device_ids() -> list[str]:
    """Convenience: just the device ids for all registered sensors."""
    return [s.device_id for s in load_wv_sensors()]
