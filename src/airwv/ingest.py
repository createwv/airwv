"""AirWV data collector.

Two commands, both reading the PurpleAir API key from the environment:

    python -m airwv.ingest resolve   # match our devices -> sensor_index, cache it
    python -m airwv.ingest collect   # pull current readings -> storage (default)

`resolve` is occasional (after deploying/renaming sensors); `collect` is what a
scheduler runs on an interval. Functions take injectable ``source``/``store``
arguments so they can be tested without network or a real database.
"""

from __future__ import annotations

import argparse
import logging

from airwv.config import Config
from airwv.registry import SensorInfo, load_wv_sensors
from airwv.resolve import (
    WV_NW_LAT,
    WV_NW_LNG,
    WV_SE_LAT,
    WV_SE_LNG,
    ResolveResult,
    load_index_map,
    match_indices,
    save_index_map,
)
from airwv.sources.purpleair import PurpleAirSource
from airwv.storage import Store

log = logging.getLogger("airwv.ingest")


def run_resolve(config: Config, source=None, sensors: list[SensorInfo] | None = None) -> ResolveResult:
    """Resolve device names -> sensor_index across WV and cache the result."""
    sensors = sensors if sensors is not None else load_wv_sensors()
    source = source or PurpleAirSource(config.purpleair_api_key)

    records = source.list_sensors(WV_NW_LAT, WV_NW_LNG, WV_SE_LAT, WV_SE_LNG)
    result = match_indices(sensors, records)
    save_index_map(config.index_cache_path, result.matched)

    log.info(
        "resolved %d/%d sensors (%d unmatched) -> %s",
        len(result.matched),
        len(sensors),
        len(result.unmatched),
        config.index_cache_path,
    )
    if result.unmatched:
        log.info("unmatched device ids: %s", ", ".join(result.unmatched))
    return result


def run_collect(config: Config, source=None, store=None, index_map: dict[str, int] | None = None) -> int:
    """Pull current readings for resolved sensors and store them. Returns inserted count."""
    index_map = index_map if index_map is not None else load_index_map(config.index_cache_path)
    if not index_map:
        log.warning(
            "no resolved sensor indices at %s — run `python -m airwv.ingest resolve` first",
            config.index_cache_path,
        )
        return 0

    indices = sorted(set(index_map.values()))
    source = source or PurpleAirSource(config.purpleair_api_key, sensor_ids=indices)
    store = store or Store.from_config(config)
    store.create_schema()

    readings = source.fetch_current()
    inserted = store.save_readings(readings)
    log.info("collected %d readings from %d sensors, inserted %d new", len(readings), len(indices), inserted)
    return inserted


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="airwv.ingest", description="AirWV data collector")
    parser.add_argument(
        "command",
        nargs="?",
        default="collect",
        choices=["collect", "resolve"],
        help="collect readings (default) or resolve device names to sensor indices",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = Config.from_env()

    if args.command == "resolve":
        run_resolve(config)
    else:
        run_collect(config)


if __name__ == "__main__":
    main()
