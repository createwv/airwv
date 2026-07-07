"""Resolve our device names to PurpleAir ``sensor_index`` values.

Public PurpleAir sensors are read by numeric ``sensor_index``, but our registry
keys sensors by device MAC (``device_id``) and name. Since we intentionally don't
store owner emails (PII), we resolve indices by listing WV sensors within a
bounding box and matching on device name.

The resulting ``device_id -> sensor_index`` map is cached to a gitignored file so
we only resolve occasionally, not on every collection run. Unmatched devices are
reported so they can be handled manually (e.g. private sensors that need a read
key, or renamed devices).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from airwv.registry import SensorInfo

# Approximate bounding box covering West Virginia (NW corner -> SE corner).
WV_NW_LAT, WV_NW_LNG = 40.64, -82.65
WV_SE_LAT, WV_SE_LNG = 37.20, -77.72


@dataclass
class ResolveResult:
    matched: dict[str, int]  # device_id -> sensor_index
    unmatched: list[str]  # device_ids with no name match


def _normalize(name: str) -> str:
    return " ".join(name.strip().lower().split())


def match_indices(
    sensors: Iterable[SensorInfo],
    purpleair_records: Iterable[dict],
) -> ResolveResult:
    """Match registry sensors to PurpleAir records by normalized name."""
    index_by_name: dict[str, int] = {}
    for record in purpleair_records:
        name = record.get("name")
        idx = record.get("sensor_index")
        if name and idx is not None:
            index_by_name[_normalize(str(name))] = int(idx)

    matched: dict[str, int] = {}
    unmatched: list[str] = []
    for sensor in sensors:
        idx = index_by_name.get(_normalize(sensor.name))
        if idx is not None:
            matched[sensor.device_id] = idx
        else:
            unmatched.append(sensor.device_id)
    return ResolveResult(matched=matched, unmatched=unmatched)


def load_index_map(path: Path) -> dict[str, int]:
    """Load a cached device_id -> sensor_index map (empty if none yet)."""
    if path.is_file():
        return {k: int(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
    return {}


def save_index_map(path: Path, mapping: dict[str, int]) -> None:
    """Persist the device_id -> sensor_index map (creating parents as needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")
