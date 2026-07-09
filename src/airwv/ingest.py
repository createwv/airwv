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
import json
import logging
import math
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from airwv.analysis import (
    compare_to_baseline,
    detect_spikes,
    detect_stuck,
    detrend_events,
    diurnal_amplitude,
    hour_of_day_profile,
    is_worsening,
    linear_trend,
    part_of_day_summary,
    score_health,
    sensor_medians,
)
from airwv.alerts import evaluate
from airwv.config import Config
from airwv.export_utils import readings_to_csv, readings_to_records
from airwv.notify import make_notifier
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
    refresh: bool = False,
    now: datetime | None = None,
) -> int:
    """Backfill historical readings for all resolved sensors. Returns rows written.

    History is fetched in windows (PurpleAir limits the span per request) and
    upserted (re-pulls enrich existing rows). **Windows we already have data for
    are skipped** to avoid re-spending API points — pass ``refresh=True`` to
    re-pull and overwrite them (e.g. to enrich old rows with new fields). A failed
    window is logged and skipped. ``limit`` caps the sensor count for cheap tests.
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
    skipped = 0
    for idx in indices:
        for w_start, w_end in _time_windows(start, end, window_days):
            if not refresh and store.count_readings(str(idx), w_start, w_end) > 0:
                skipped += 1
                continue  # already have data here — don't re-spend points
            try:
                readings = source.fetch_sensor_history(idx, w_start, w_end, average_minutes)
            except Exception as exc:  # one bad window shouldn't abort the run
                log.warning(
                    "history fetch failed for sensor %s %s..%s: %s",
                    idx, w_start.date(), w_end.date(), exc,
                )
                continue
            total += store.save_readings(readings)
    if skipped and not refresh:
        log.info("skipped %d window(s) already stored (use --refresh to re-pull/enrich)", skipped)

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


def run_subscribe(
    config: Config,
    channel: str,
    target: str,
    threshold: float,
    sensor_name: str | None = None,
    field: str = "pm2_5",
    kind: str = "threshold",
    min_interval_seconds: int = 21600,
    quiet_start: int | None = None,
    quiet_end: int | None = None,
    store=None,
) -> int:
    """Create an alert subscription. Returns its id."""
    store = store or Store.from_config(config)
    store.create_schema()
    sensor_id = None
    if sensor_name:
        scoped = _scoped_index_map(config, names=[sensor_name])
        if not scoped:
            log.warning("no resolved sensor matching %r — subscribing to ALL sensors", sensor_name)
        else:
            sensor_id = str(sorted(set(scoped.values()))[0])

    sub_id = store.add_subscription(
        channel=channel, target=target, sensor_id=sensor_id, field=field, kind=kind,
        threshold=threshold, min_interval_seconds=min_interval_seconds,
        quiet_start=quiet_start, quiet_end=quiet_end,
    )
    trigger = f">= {threshold}" if kind == "threshold" else f"rising +{threshold}%"
    log.info("subscription %d: %s -> %s when %s %s (%s)", sub_id, channel, target,
             field, trigger, f"sensor {sensor_id}" if sensor_id else "any sensor")
    return sub_id


def run_alerts(config: Config, send: bool = False, now: datetime | None = None,
               store=None, notifier_factory=make_notifier) -> list:
    """Evaluate subscriptions against the latest readings; dispatch if ``send``.

    Defaults to a dry run (prints what would fire) — sending is outward-facing.
    """
    store = store or Store.from_config(config)
    store.create_schema()
    now = now or datetime.now(tz=timezone.utc).replace(tzinfo=None)
    subs = store.list_subscriptions(active_only=True)
    latest = store.latest_reading_per_sensor()

    # Trend-kind subscriptions need a fitted trend per (sensor, field).
    trends = {}
    trend_pairs = set()
    for s in subs:
        if getattr(s, "kind", "threshold") == "trend":
            for sid in ([s.sensor_id] if s.sensor_id else list(latest)):
                trend_pairs.add((sid, s.field))
    for sid, field in trend_pairs:
        trends[(sid, field)] = linear_trend(store.readings_for_sensor(sid), field=field)

    alerts = evaluate(subs, latest, now, trends=trends)

    log.info("%d subscription(s), %d alert(s) %s", len(subs), len(alerts),
             "to send" if send else "(dry run — use --send to deliver)")
    for a in alerts:
        detail = (f"{a.field} rising +{a.value}% (>= +{a.threshold}%)" if a.kind == "trend"
                  else f"{a.field}={a.value} >= {a.threshold}")
        log.info("  ALERT sub %d %s->%s: %s at sensor %s",
                 a.subscription_id, a.channel, a.target, detail, a.sensor_id)
        if not send:
            continue
        try:
            notifier_factory(a.channel).send(a)
            store.mark_notified(a.subscription_id, now)
        except Exception as exc:  # one bad channel shouldn't block the rest
            log.warning("  failed to send alert %d via %s: %s", a.subscription_id, a.channel, exc)
    return alerts


def run_reference(config: Config, days: int = 7, source=None, store=None, now: datetime | None = None,
                  start: datetime | None = None, end: datetime | None = None) -> int:
    """Pull WV reference-monitor PM2.5 from OpenAQ into storage (source='openaq').

    Enables validating community sensors against regulatory-grade data. Needs
    OPENAQ_API_KEY (free at explore.openaq.org). Pass --start/--end (ISO dates) to
    pull a historical window matching your sensor data; otherwise the last N days.
    """
    if source is None and not config.openaq_api_key:
        log.warning("OPENAQ_API_KEY not set — get a free key at https://explore.openaq.org/register")
        return 0

    from airwv.sources.openaq import OpenAQSource

    source = source or OpenAQSource(config.openaq_api_key)
    store = store or Store.from_config(config)
    store.create_schema()
    now = now or datetime.now(tz=timezone.utc)
    end = end or now
    start = start or (end - timedelta(days=days))

    locations = source.fetch_locations(WV_NW_LAT, WV_NW_LNG, WV_SE_LAT, WV_SE_LNG)
    total = 0
    for loc in locations:
        for sensor_id in loc.get("pm25_sensor_ids", []):
            try:
                total += store.save_readings(source.fetch_measurements(
                    sensor_id, start, end, lat=loc.get("lat"), lon=loc.get("lon")))
            except Exception as exc:  # one bad sensor shouldn't abort the pull
                log.warning("openaq fetch failed for sensor %s: %s", sensor_id, exc)
    log.info("openaq reference: %d readings from %d monitor location(s), %s..%s",
             total, len(locations), start.date(), end.date())
    return total


def run_validate(config: Config, field: str = "pm2_5", min_days: int = 5, store=None) -> list[dict]:
    """Validate community sensors against the nearest OpenAQ reference monitor.

    For each community (PurpleAir) sensor, find the closest regulatory monitor and
    correlate their daily-median PM2.5 over the overlapping days: Pearson r + mean
    bias (sensor − reference). High r + small bias = the community network tracks
    reference-grade data. Read-only. Requires prior `ingest reference` data.
    """
    import statistics

    from airwv.analysis.trends import daily_medians

    store = store or Store.from_config(config)
    community = store.sensor_ids_by_source("purpleair")
    reference = store.sensor_ids_by_source("openaq")
    if not reference:
        log.warning("no reference data yet — run `ingest reference` first")
        return []

    # Community sensor coords come from the resolved public-sensor listing.
    coords: dict[str, tuple] = {}
    listing_path = config.index_cache_path.parent / "wv_public_sensors.json"
    if listing_path.exists():
        for rec in json.loads(listing_path.read_text()):
            idx = str(rec.get("sensor_index") or rec.get("index") or "")
            if idx and rec.get("latitude") is not None:
                coords[idx] = (rec["latitude"], rec["longitude"])

    # Reference monitors: coords + daily medians (coords stored on the readings).
    monitors = []
    for rid in reference:
        rows = store.readings_for_sensor(rid)
        lat = next((r.lat for r in rows if r.lat is not None), None)
        lon = next((r.lon for r in rows if r.lon is not None), None)
        if lat is None:
            continue
        monitors.append((rid, lat, lon, dict(daily_medians(rows, field))))
    if not monitors:
        log.warning("reference readings have no coordinates — re-pull with `ingest reference`")
        return []

    def haversine(a_lat, a_lon, b_lat, b_lon) -> float:
        r = 6371.0
        p1, p2 = math.radians(a_lat), math.radians(b_lat)
        dphi, dl = math.radians(b_lat - a_lat), math.radians(b_lon - a_lon)
        h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(h))

    results = []
    for cid in community:
        if cid not in coords:
            continue
        clat, clon = coords[cid]
        cdaily = dict(daily_medians(store.readings_for_sensor(cid), field))
        if not cdaily:
            continue
        rid, rlat, rlon, rdaily = min(monitors, key=lambda m: haversine(clat, clon, m[1], m[2]))
        common = sorted(set(cdaily) & set(rdaily))
        if len(common) < min_days:
            continue
        cs = [cdaily[d] for d in common]
        rs = [rdaily[d] for d in common]
        try:
            r = statistics.correlation(cs, rs) if len(common) >= 2 else None
        except statistics.StatisticsError:
            r = None  # a channel was flat over the window
        results.append({
            "sensor": cid, "monitor": rid, "distance_km": round(haversine(clat, clon, rlat, rlon), 1),
            "days": len(common), "r": None if r is None else round(r, 2),
            "bias": round(statistics.mean(c - s for c, s in zip(cs, rs)), 2),
        })

    results.sort(key=lambda x: (x["r"] is None, -(x["r"] or 0)))
    log.info("sensor-vs-reference validation (%s, ≥%d overlapping days):", field, min_days)
    for v in results:
        rtxt = "n/a " if v["r"] is None else f"{v['r']:+.2f}"
        log.info("  sensor %-8s ↔ monitor %-8s  %4.0f km  %2dd  r=%s  bias=%+.2f",
                 v["sensor"], v["monitor"], v["distance_km"], v["days"], rtxt, v["bias"])
    if not results:
        log.info("  (no sensor/monitor pairs with enough overlapping days — pull a matching window with `ingest reference --start/--end`)")
    return results


def run_baseline(config: Config, field: str = "pm2_5", min_pct: float = 25.0, store=None) -> dict:
    """Compare each sensor's median to the network baseline for a field. Read-only."""
    store = store or Store.from_config(config)
    readings_by_sensor = {sid: store.readings_for_sensor(sid) for sid in store.distinct_sensor_ids()}
    result = compare_to_baseline(sensor_medians(readings_by_sensor, field))

    log.info("%s network baseline (median across sensors): %s", field, result["baseline"])
    for sid, s in sorted(result["sensors"].items(), key=lambda kv: -(kv[1]["median"])):
        flag = "  ↑ above network" if (s["pct_vs_baseline"] or 0) >= min_pct else ""
        log.info("  sensor %-8s median=%-6s  %+.1f%% vs baseline%s", sid, s["median"], s["pct_vs_baseline"] or 0, flag)
    return result


def run_export(
    config: Config,
    out_path: str,
    sensor_name: str | None = None,
    org: str | None = None,
    fmt: str = "csv",
    store=None,
) -> int:
    """Export readings to CSV/JSON. Returns row count."""
    from pathlib import Path

    store = store or Store.from_config(config)
    if sensor_name or org:
        scoped = _scoped_index_map(config, org=org, names=[sensor_name] if sensor_name else None)
        sids = sorted({str(i) for i in scoped.values()})
    else:
        sids = sorted(store.distinct_sensor_ids())

    rows = []
    for sid in sids:
        rows.extend(store.readings_for_sensor(sid))

    path = Path(out_path)
    if fmt == "json":
        import json

        path.write_text(json.dumps(readings_to_records(rows), indent=2), encoding="utf-8")
    else:
        path.write_text(readings_to_csv(rows), encoding="utf-8")
    log.info("exported %d readings from %d sensor(s) -> %s", len(rows), len(sids), out_path)
    return len(rows)


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
    send_alerts: bool = True,
    alerts=run_alerts,
) -> None:
    """Collect on a fixed interval forever (or ``iterations`` times, for tests).

    The store is created once and reused; each cycle reloads the index cache and
    rebuilds the source, so newly-resolved sensors are picked up without restart.
    After each collection, subscriptions are evaluated and alerts delivered
    (unless ``send_alerts`` is False).
    """
    store = store or Store.from_config(config)
    store.create_schema()
    log.info("scheduler starting — collecting every %ds%s", config.poll_interval_seconds,
             "; alerts on" if send_alerts else "; alerts off")

    count = 0
    while iterations is None or count < iterations:
        collect(config, store=store)
        if send_alerts:
            try:
                alerts(config, send=True, store=store)
            except Exception as exc:  # a channel failure shouldn't kill the loop
                log.warning("alert evaluation failed: %s", exc)
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
    run_cmd = sub.add_parser("run", help="run the scheduler loop (collect + alerts on an interval)")
    run_cmd.add_argument("--no-alerts", action="store_true", help="collect only; don't send alerts")
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
    backfill.add_argument("--refresh", action="store_true",
                          help="re-pull windows already stored (enrich/overwrite; spends points)")
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
    baseline = sub.add_parser("baseline", help="each sensor vs the network baseline (no API cost)")
    baseline.add_argument("--field", default="pm2_5", help="field to compare (default pm2_5)")
    export = sub.add_parser("export", help="export readings to CSV/JSON (no API cost)")
    export.add_argument("--out", required=True, help="output file path")
    export.add_argument("--format", default="csv", choices=["csv", "json"], help="output format")
    export.add_argument("--sensor", help="only sensors whose name matches")
    export.add_argument("--org", help="only sensors whose org matches")
    subscribe = sub.add_parser("subscribe", help="create an alert subscription")
    subscribe.add_argument("--channel", required=True, choices=["email", "webhook", "log"])
    subscribe.add_argument("--target", required=True, help="email address / webhook URL")
    subscribe.add_argument("--threshold", type=float, required=True,
                           help="threshold value, or for --kind trend the min %% rise")
    subscribe.add_argument("--kind", default="threshold", choices=["threshold", "trend"],
                           help="threshold (value crosses) or trend (rising over time)")
    subscribe.add_argument("--sensor", help="sensor name (default: any sensor)")
    subscribe.add_argument("--field", default="pm2_5", help="field to watch (default pm2_5)")
    subscribe.add_argument("--min-interval", type=int, default=21600, help="min seconds between alerts")
    subscribe.add_argument("--quiet-start", type=int, help="quiet hours start (local hour 0-23)")
    subscribe.add_argument("--quiet-end", type=int, help="quiet hours end (local hour 0-23)")
    alerts = sub.add_parser("alerts", help="evaluate subscriptions (dry run unless --send)")
    alerts.add_argument("--send", action="store_true", help="actually deliver notifications")
    reference = sub.add_parser("reference", help="pull EPA/OpenAQ reference monitors (needs OPENAQ_API_KEY)")
    reference.add_argument("--days", type=int, default=7, help="how many days back to pull (default 7)")
    reference.add_argument("--start", help="ISO start date for a historical window (e.g. 2024-01-01)")
    reference.add_argument("--end", help="ISO end date (default now)")
    validate = sub.add_parser("validate", help="community sensors vs nearest reference monitor (no API cost)")
    validate.add_argument("--field", default="pm2_5", help="field to validate (default pm2_5)")
    validate.add_argument("--min-days", type=int, default=5, help="min overlapping days to report a pair")
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
            refresh=args.refresh,
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
    elif command == "baseline":
        run_baseline(config, field=args.field)
    elif command == "export":
        run_export(config, args.out, sensor_name=args.sensor, org=args.org, fmt=args.format)
    elif command == "subscribe":
        run_subscribe(config, args.channel, args.target, args.threshold, sensor_name=args.sensor,
                      field=args.field, kind=args.kind, min_interval_seconds=args.min_interval,
                      quiet_start=args.quiet_start, quiet_end=args.quiet_end)
    elif command == "alerts":
        run_alerts(config, send=args.send)
    elif command == "reference":
        def _parse_dt(s):
            if not s:
                return None
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        run_reference(config, days=args.days, start=_parse_dt(args.start), end=_parse_dt(args.end))
    elif command == "validate":
        run_validate(config, field=args.field, min_days=args.min_days)
    elif command == "run":
        try:
            run_scheduler(config, send_alerts=not getattr(args, "no_alerts", False))
        except KeyboardInterrupt:
            log.info("scheduler stopped")
    else:
        index_map = _scoped_index_map(config, org=getattr(args, "org", None), names=getattr(args, "sensor", None))
        run_collect(config, index_map=index_map, limit=getattr(args, "limit", None))


if __name__ == "__main__":
    main()
