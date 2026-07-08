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
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from airwv.analysis import detect_spikes, detect_stuck, score_health
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
    save_listing,
)
from airwv.sources.purpleair import PurpleAirSource
from airwv.storage import Store

log = logging.getLogger("airwv.ingest")


def _scoped_index_map(
    config: Config,
    org: str | None = None,
    names: list[str] | None = None,
    sensors=None,
    index_map: dict[str, int] | None = None,
) -> dict[str, int]:
    """Load the resolved index map, optionally narrowed to an org and/or sensor names.

    Lets us focus collection/backfill on a subset (e.g. only Create WV / Kanawha
    Valley sensors) — both to control API-point spend and to target investigations.
    Matching is case-insensitive substring on ``org`` and sensor ``name``.
    """
    index_map = index_map if index_map is not None else load_index_map(config.index_cache_path)
    if not (org or names):
        return index_map

    sensors = sensors if sensors is not None else load_wv_sensors()

    def matches(s) -> bool:
        if org and (not s.org or org.lower() not in s.org.lower()):
            return False
        if names and not any(n.lower() in s.name.lower() for n in names):
            return False
        return True

    selected = {s.device_id for s in sensors if matches(s)}
    scoped = {dev: idx for dev, idx in index_map.items() if dev in selected}
    log.info("scoped to %d resolved sensor(s) (org=%s, names=%s)", len(scoped), org, names)
    return scoped


def run_resolve(config: Config, source=None, sensors: list[SensorInfo] | None = None) -> ResolveResult:
    """Resolve device names -> sensor_index across WV and cache the result."""
    sensors = sensors if sensors is not None else load_wv_sensors()
    source = source or PurpleAirSource(config.purpleair_api_key)

    records = source.list_sensors(WV_NW_LAT, WV_NW_LNG, WV_SE_LAT, WV_SE_LNG)
    result = match_indices(sensors, records)

    # Merge hand-maintained overrides for sensors that can't be matched by name
    # (renamed or private). Overrides win. Both files sit in the gitignored dir.
    cache_dir = config.index_cache_path.parent
    overrides = load_index_map(cache_dir / "sensor_index_overrides.json")
    mapping = {**result.matched, **overrides}
    save_index_map(config.index_cache_path, mapping)

    # Dump the full public listing so unmatched devices can be reconciled offline.
    save_listing(cache_dir / "wv_public_sensors.json", records)

    matched_devices = set(result.matched) | set(overrides)
    unmatched = [s.device_id for s in sensors if s.device_id not in matched_devices]
    log.info(
        "resolved %d/%d sensors (%d by name, %d override, %d unmatched) -> %s",
        len(mapping),
        len(sensors),
        len(result.matched),
        len(overrides),
        len(unmatched),
        config.index_cache_path,
    )
    if unmatched:
        log.info("unmatched device ids: %s", ", ".join(unmatched))
    return result


def run_collect(
    config: Config,
    source=None,
    store=None,
    index_map: dict[str, int] | None = None,
    limit: int | None = None,
) -> int:
    """Pull current readings for resolved sensors and store them. Returns inserted count.

    ``limit`` caps how many sensors are polled — useful to conserve API points
    while testing before running the full fleet.
    """
    index_map = index_map if index_map is not None else load_index_map(config.index_cache_path)
    if not index_map:
        log.warning(
            "no resolved sensor indices at %s — run `python -m airwv.ingest resolve` first",
            config.index_cache_path,
        )
        return 0

    indices = sorted(set(index_map.values()))
    if limit is not None:
        indices = indices[:limit]
        log.info("limiting to %d sensor(s) this run (API-point conservation)", len(indices))
    source = source or PurpleAirSource(config.purpleair_api_key, sensor_ids=indices)
    store = store or Store.from_config(config)
    store.create_schema()

    readings = source.fetch_current()
    inserted = store.save_readings(readings)
    log.info("collected %d readings from %d sensors, inserted %d new", len(readings), len(indices), inserted)
    return inserted


def _time_windows(start: datetime, end: datetime, window_days: int):
    """Yield (start, end) chunks no longer than ``window_days`` each."""
    step = timedelta(days=window_days)
    cursor = start
    while cursor < end:
        yield cursor, min(cursor + step, end)
        cursor += step


def run_backfill(
    config: Config,
    source=None,
    store=None,
    index_map: dict[str, int] | None = None,
    days: int = 30,
    average_minutes: int = 60,
    window_days: int = 14,
    limit: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    now: datetime | None = None,
) -> int:
    """Backfill historical readings for all resolved sensors. Returns rows inserted.

    History is fetched in windows (PurpleAir limits the span per request) and
    saved incrementally; dedupe makes overlaps harmless. A failed window is
    logged and skipped so one bad sensor/range doesn't abort the whole backfill.
    ``limit`` caps the sensor count — test small before spending API points.
    """
    index_map = index_map if index_map is not None else load_index_map(config.index_cache_path)
    if not index_map:
        log.warning("no resolved sensor indices — run `python -m airwv.ingest resolve` first")
        return 0

    now = now or datetime.now(tz=timezone.utc)
    end = end or now
    start = start or (end - timedelta(days=days))
    source = source or PurpleAirSource(config.purpleair_api_key)
    store = store or Store.from_config(config)
    store.create_schema()

    indices = sorted(set(index_map.values()))
    if limit is not None:
        indices = indices[:limit]
        log.info("limiting backfill to %d sensor(s) (API-point conservation)", len(indices))
    log.info(
        "backfilling %d sensors %s..%s (avg=%dmin)",
        len(indices), start.date(), end.date(), average_minutes,
    )

    total = 0
    for idx in indices:
        for w_start, w_end in _time_windows(start, end, window_days):
            try:
                readings = source.fetch_sensor_history(idx, w_start, w_end, average_minutes)
            except Exception as exc:  # one bad window shouldn't abort the run
                log.warning(
                    "history fetch failed for sensor %s %s..%s: %s",
                    idx, w_start.date(), w_end.date(), exc,
                )
                continue
            total += store.save_readings(readings)

    log.info("backfill complete: %d new rows across %d sensors", total, len(indices))
    return total


def run_analyze(
    config: Config,
    store=None,
    now: datetime | None = None,
    since_hours: int = 168,
    spike_threshold: float = 3.5,
) -> dict:
    """Analyze stored readings for anomalies and sensor health. Read-only.

    ``now`` is naive UTC (matches how SQLite returns timestamps). Returns
    ``{"anomalies": [...], "health": [...]}`` and logs a summary.
    """
    store = store or Store.from_config(config)
    now = now or datetime.now(tz=timezone.utc).replace(tzinfo=None)
    since = now - timedelta(hours=since_hours)

    anomalies = []
    healths = []
    for sensor_id in store.distinct_sensor_ids():
        readings = store.readings_for_sensor(sensor_id, since=since)
        anomalies.extend(detect_spikes(readings, threshold=spike_threshold))
        anomalies.extend(detect_stuck(readings))
        healths.append(score_health(sensor_id, readings, now))

    by_status = Counter(h.status for h in healths)
    log.info(
        "analyzed %d sensor(s) over %dh: %d anomalies; health %s",
        len(healths), since_hours, len(anomalies), dict(by_status),
    )
    for a in anomalies[:25]:
        log.info("  ANOMALY sensor %s %s=%s (%s: %s)", a.sensor_id, a.field, a.value, a.kind, a.detail)
    for h in healths:
        if h.status != "ok":
            log.info("  HEALTH sensor %s -> %s (%s)", h.sensor_id, h.status, "; ".join(h.issues))

    return {"anomalies": anomalies, "health": healths}


def collect_with_retry(
    config: Config,
    source=None,
    store=None,
    index_map: dict[str, int] | None = None,
    attempts: int = 3,
    base_delay: float = 2.0,
    sleeper=time.sleep,
) -> int:
    """Run one collection, retrying transient failures with exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return run_collect(config, source=source, store=store, index_map=index_map)
        except Exception as exc:  # network/API hiccups shouldn't kill the loop
            last_exc = exc
            if attempt < attempts:
                delay = base_delay * (2 ** (attempt - 1))
                log.warning(
                    "collect attempt %d/%d failed: %s — retrying in %.0fs",
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
                sleeper(delay)
    log.error("collect failed after %d attempts: %s", attempts, last_exc)
    return 0


def run_scheduler(
    config: Config,
    iterations: int | None = None,
    sleeper=time.sleep,
    collect=collect_with_retry,
    store=None,
) -> None:
    """Collect on a fixed interval forever (or ``iterations`` times, for tests).

    The store is created once and reused; each cycle reloads the index cache and
    rebuilds the source, so newly-resolved sensors are picked up without restart.
    """
    store = store or Store.from_config(config)
    store.create_schema()
    log.info("scheduler starting — collecting every %ds", config.poll_interval_seconds)

    count = 0
    while iterations is None or count < iterations:
        collect(config, store=store)
        count += 1
        if iterations is None or count < iterations:
            sleeper(config.poll_interval_seconds)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="airwv.ingest", description="AirWV data collector")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("resolve", help="match device names to PurpleAir sensor indices")
    collect = sub.add_parser("collect", help="collect current readings once (default)")
    collect.add_argument("--limit", type=int, help="cap sensors polled (saves API points)")
    collect.add_argument("--org", help="only sensors whose installing org matches (e.g. 'Create WV')")
    collect.add_argument("--sensor", action="append", help="only sensors whose name matches (repeatable)")
    sub.add_parser("run", help="run the scheduler loop (collect on an interval)")
    backfill = sub.add_parser("backfill", help="backfill historical readings")
    backfill.add_argument("--days", type=int, default=30, help="how far back to fetch (default 30)")
    backfill.add_argument("--start", help="start date YYYY-MM-DD (overrides --days)")
    backfill.add_argument("--end", help="end date YYYY-MM-DD (default: now)")
    backfill.add_argument(
        "--average",
        type=int,
        default=60,
        choices=[10, 30, 60, 360, 1440],
        help="PurpleAir averaging bucket in minutes (default 60)",
    )
    backfill.add_argument("--limit", type=int, help="cap sensors backfilled (saves API points)")
    backfill.add_argument("--org", help="only sensors whose installing org matches (e.g. 'Create WV')")
    backfill.add_argument("--sensor", action="append", help="only sensors whose name matches (repeatable)")
    analyze = sub.add_parser("analyze", help="detect anomalies + score sensor health (no API cost)")
    analyze.add_argument("--hours", type=int, default=168, help="lookback window (default 168 = 7d)")
    analyze.add_argument("--threshold", type=float, default=3.5, help="spike z-score threshold")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = Config.from_env()

    def _date(value: str | None) -> datetime | None:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc) if value else None

    command = args.command or "collect"
    if command == "resolve":
        run_resolve(config)
    elif command == "backfill":
        index_map = _scoped_index_map(config, org=args.org, names=args.sensor)
        run_backfill(
            config,
            index_map=index_map,
            days=args.days,
            average_minutes=args.average,
            limit=args.limit,
            start=_date(args.start),
            end=_date(args.end),
        )
    elif command == "analyze":
        run_analyze(config, since_hours=args.hours, spike_threshold=args.threshold)
    elif command == "run":
        try:
            run_scheduler(config)
        except KeyboardInterrupt:
            log.info("scheduler stopped")
    else:
        index_map = _scoped_index_map(config, org=getattr(args, "org", None), names=getattr(args, "sensor", None))
        run_collect(config, index_map=index_map, limit=getattr(args, "limit", None))


if __name__ == "__main__":
    main()
