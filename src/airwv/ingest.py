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
from zoneinfo import ZoneInfo

from airwv.analysis import (
    detect_spikes,
    detect_stuck,
    detrend_events,
    diurnal_amplitude,
    hour_of_day_profile,
    is_worsening,
    linear_trend,
    part_of_day_summary,
    score_health,
)
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


def run_patterns(
    config: Config,
    sensor_name: str,
    field: str = "voc",
    store=None,
    index_map: dict[str, int] | None = None,
    tzname: str = "America/New_York",
) -> dict:
    """Print a local-time-of-day profile of ``field`` for matching sensor(s).

    Read-only. Resolves ``sensor_name`` to its stored series and shows median by
    hour (local), plus a business/evening/overnight summary — the direct test of
    "does this run higher in the evening?".
    """
    store = store or Store.from_config(config)
    scoped = index_map if index_map is not None else _scoped_index_map(config, names=[sensor_name])
    if not scoped:
        log.warning("no resolved sensor matching %r (resolve first?)", sensor_name)
        return {}

    results = {}
    for idx in sorted(set(scoped.values())):
        readings = store.readings_for_sensor(str(idx))
        profile = hour_of_day_profile(readings, field=field, tzname=tzname)
        summary = part_of_day_summary(profile)
        results[idx] = {"profile": profile, "summary": summary}

        total = sum(s.count for s in profile)
        log.info("%s (index %s): %s by local hour (%s), %d readings", sensor_name, idx, field, tzname, total)
        peak = max((s.median or 0) for s in profile) or 1
        for s in profile:
            bar = "#" * int((s.median or 0) / peak * 40)
            log.info("  %02d:00  %7s  %s", s.hour, "-" if s.median is None else s.median, bar)
        log.info(
            "  business(9-17)=%s  evening(18-23)=%s  overnight(0-5)=%s  evening/business=%s",
            summary["business_9_17"], summary["evening_18_23"],
            summary["overnight_0_5"], summary["evening_vs_business_ratio"],
        )
    return results


def run_compare(
    config: Config,
    sensor_names: list[str],
    field: str = "pm2_5",
    store=None,
) -> list[dict]:
    """Compare day-vs-overnight amplitude of ``field`` across sensors. Read-only.

    A "control" (clean/rural) site should sit near ratio 1.0; a site with a local
    overnight source stands out. PM2.5 is calibrated and comparable across sensors.
    """
    store = store or Store.from_config(config)
    results = []
    log.info("diurnal %s comparison (night 0-5 vs day 9-17, local ET):", field)
    for name in sensor_names:
        scoped = _scoped_index_map(config, names=[name])
        for idx in sorted(set(scoped.values())):
            amp = diurnal_amplitude(store.readings_for_sensor(str(idx)), field=field)
            results.append({"name": name, "index": idx, **amp})
            log.info(
                "  %-16s idx %-7s day=%-6s night=%-6s  night/day=%-5s  (n=%d)",
                name, idx, amp["day"], amp["night"], amp["night_day_ratio"], amp["n"],
            )
    return results


def run_trends(
    config: Config,
    field: str = "pm2_5",
    sensor_name: str | None = None,
    min_days: int = 14,
    store=None,
) -> list:
    """Compute per-sensor long-term trends for a field; flag worsening sites. Read-only."""
    store = store or Store.from_config(config)
    scoped = _scoped_index_map(config, names=[sensor_name]) if sensor_name else None
    if sensor_name and not scoped:
        log.warning("no resolved sensor matching %r", sensor_name)
        return []

    wanted = {str(i) for i in scoped.values()} if scoped else None
    results = []
    for sid in store.distinct_sensor_ids():
        if wanted is not None and sid not in wanted:
            continue
        trend = linear_trend(store.readings_for_sensor(sid), field=field, min_days=min_days)
        results.append((sid, trend))

    # Worst-trending first.
    results.sort(key=lambda t: -(t[1].pct_change or -999))
    log.info("%s trends over daily medians (%d sensors):", field, len(results))
    for sid, t in results:
        watch = "  ⚠ WATCH" if is_worsening(t) else ""
        log.info(
            "  sensor %-8s %-12s %sd  first=%s last=%s  %s/30d  Δ=%s%%  r=%s%s",
            sid, t.direction, t.n_days, t.first, t.last, t.slope_per_30d,
            t.pct_change, t.r, watch,
        )
    watching = [sid for sid, t in results if is_worsening(t)]
    if watching:
        log.info("areas to watch (rising %s): %s", field, ", ".join(watching))
    return results


def run_events(
    config: Config,
    sensor_name: str,
    field: str = "pm2_5",
    z_threshold: float = 6.0,
    top: int = 15,
    store=None,
    tzname: str = "America/New_York",
) -> list:
    """Find de-trended episodic events for matching sensor(s). Read-only, no API cost."""
    store = store or Store.from_config(config)
    scoped = _scoped_index_map(config, names=[sensor_name])
    if not scoped:
        log.warning("no resolved sensor matching %r (resolve first?)", sensor_name)
        return []

    et = ZoneInfo(tzname)
    found = []
    for idx in sorted(set(scoped.values())):
        events = detrend_events(store.readings_for_sensor(str(idx)), field=field, z_threshold=z_threshold)
        found.extend(events)
        log.info("%s (idx %s): %d de-trended %s events (residual z>=%.1f)",
                 sensor_name, idx, len(events), field, z_threshold)
        for e in events[:top]:
            local = e.ts.replace(tzinfo=timezone.utc).astimezone(et)
            log.info("  %s %s  %s=%.1f  (+%.1f over %.1f baseline, z=%.0f)",
                     local.strftime("%Y-%m-%d %H:%M"), local.strftime("%a"),
                     field, e.value, e.residual, e.baseline, e.score)
    return found


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
    patterns = sub.add_parser("patterns", help="time-of-day profile for a sensor (no API cost)")
    patterns.add_argument("--sensor", required=True, help="sensor name substring (e.g. 'Glasgow')")
    patterns.add_argument("--field", default="voc", help="field to profile (default voc)")
    compare = sub.add_parser("compare", help="compare day/night amplitude across sensors (no API cost)")
    compare.add_argument("--sensor", action="append", required=True, help="sensor name (repeatable)")
    compare.add_argument("--field", default="pm2_5", help="field to compare (default pm2_5)")
    events = sub.add_parser("events", help="de-trended episodic events for a sensor (no API cost)")
    events.add_argument("--sensor", required=True, help="sensor name substring (e.g. 'Glasgow')")
    events.add_argument("--field", default="pm2_5", help="field to scan (default pm2_5)")
    events.add_argument("--threshold", type=float, default=6.0, help="residual robust-z threshold")
    events.add_argument("--top", type=int, default=15, help="how many top events to show")
    trends = sub.add_parser("trends", help="long-term trends + areas to watch (no API cost)")
    trends.add_argument("--field", default="pm2_5", help="field to trend (default pm2_5)")
    trends.add_argument("--sensor", help="limit to one sensor name (default: all)")
    trends.add_argument("--min-days", type=int, default=14, help="minimum days of data (default 14)")
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
    elif command == "patterns":
        run_patterns(config, args.sensor, field=args.field)
    elif command == "compare":
        run_compare(config, args.sensor, field=args.field)
    elif command == "events":
        run_events(config, args.sensor, field=args.field, z_threshold=args.threshold, top=args.top)
    elif command == "trends":
        run_trends(config, field=args.field, sensor_name=args.sensor, min_days=args.min_days)
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
