"""Neighbor cross-check + automatic event candidates.

A spike at one sensor is either a truly local release or a sensor fault; a spike
that nearby sensors also show at the same time is an area event. This module
turns de-trended outliers (``analysis/events.py``) into that call:

1. :func:`episodes` collapses a sensor's flagged readings into runs (one per spike).
2. :func:`cross_check` asks each neighbor within ``radius_km`` whether its own
   de-trended residual was elevated (robust z >= ``neighbor_z``) during the run,
   give or take ``window_h`` hours. Neighbors with no readings then don't count.
3. :func:`event_candidates` merges corroborated runs that overlap in time and
   share sensors into one candidate: a proposed Event for an admin to review.

Read-only and pure: callers pass readings/scores and coordinates in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from airwv.analysis.events import Event


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi, dl = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@dataclass
class Episode:
    """One spike at one sensor: consecutive flagged readings, peak kept."""

    sensor_id: str
    field: str
    start: datetime
    end: datetime
    peak: Event
    n: int


@dataclass
class CrossCheck:
    episode: Episode
    neighbors: list[str]          # within radius and reporting during the window
    agree: list[str]              # of those, elevated too
    peak_z: dict[str, float] = field(default_factory=dict)   # neighbor -> max residual z

    @property
    def status(self) -> str:
        if self.agree:
            return "corroborated"     # nearby sensors saw it too: an area event
        if self.neighbors:
            return "single_sensor"    # neighbors reporting, flat: local source or sensor fault
        return "no_neighbors"         # nothing nearby reporting: can't tell


@dataclass
class Candidate:
    field: str
    start: datetime
    end: datetime
    sensor_ids: list[str]
    peak_sensor: str
    peak_value: float
    peak_ts: datetime
    peak_score: float
    lat: float | None
    lon: float | None
    checks: list[CrossCheck]


def episodes(events: list[Event], gap_h: float = 3.0) -> list[Episode]:
    """Collapse flagged readings into runs per sensor (readings within ``gap_h`` join)."""
    out: list[Episode] = []
    gap = timedelta(hours=gap_h)
    by_sensor: dict[str, list[Event]] = {}
    for e in events:
        by_sensor.setdefault(e.sensor_id, []).append(e)
    for sid, evs in by_sensor.items():
        evs.sort(key=lambda e: e.ts)
        run = [evs[0]]
        for e in evs[1:]:
            if e.ts - run[-1].ts <= gap:
                run.append(e)
            else:
                out.append(_episode(sid, run))
                run = [e]
        out.append(_episode(sid, run))
    out.sort(key=lambda ep: ep.start)
    return out


def _episode(sid: str, run: list[Event]) -> Episode:
    peak = max(run, key=lambda e: e.score)
    return Episode(sid, peak.field, run[0].ts, run[-1].ts, peak, len(run))


def cross_check(
    eps: list[Episode],
    scores: dict[str, list[tuple]],
    coords: dict[str, tuple[float, float]],
    radius_km: float = 15.0,
    window_h: float = 3.0,
    neighbor_z: float = 3.0,
) -> list[CrossCheck]:
    """Check each episode against the neighbors' de-trended residuals.

    ``scores`` maps sensor id -> ``residual_scores(...)`` output (``(ts, value,
    baseline, residual, z)``); ``coords`` maps sensor id -> (lat, lon). A sensor
    without coords is never a neighbor and its own episodes come back ``no_neighbors``.
    """
    win = timedelta(hours=window_h)
    out = []
    for ep in eps:
        here = coords.get(ep.sensor_id)
        neighbors, agree, peak_z = [], [], {}
        if here is not None:
            lo, hi = ep.start - win, ep.end + win
            for sid, pts in scores.items():
                if sid == ep.sensor_id or sid not in coords:
                    continue
                if km_between(here, coords[sid]) > radius_km:
                    continue
                zs = [z for ts, _v, _b, _d, z in pts if lo <= ts <= hi]
                if not zs:
                    continue          # not reporting then: no evidence either way
                neighbors.append(sid)
                peak_z[sid] = round(max(zs), 1)
                if max(zs) >= neighbor_z:
                    agree.append(sid)
        out.append(CrossCheck(ep, sorted(neighbors), sorted(agree), peak_z))
    return out


def event_candidates(
    checks: list[CrossCheck],
    coords: dict[str, tuple[float, float]],
    window_h: float = 3.0,
    min_sensors: int = 2,
) -> list[Candidate]:
    """Merge corroborated episodes that overlap in time and share sensors.

    Two corroborated checks join when their windows (padded by ``window_h``)
    overlap and their sensor sets (the spiking sensor + agreeing neighbors)
    intersect. Biggest peak first.
    """
    pad = timedelta(hours=window_h)
    groups: list[dict] = []
    for c in sorted((c for c in checks if c.status == "corroborated"), key=lambda c: c.episode.start):
        sids = {c.episode.sensor_id, *c.agree}
        hit = None
        for g in groups:
            if c.episode.start - pad <= g["end"] + pad and c.episode.end + pad >= g["start"] - pad \
                    and g["sensors"] & sids:
                hit = g
                break
        if hit is None:
            groups.append({"start": c.episode.start, "end": c.episode.end, "sensors": sids, "checks": [c]})
        else:
            hit["start"] = min(hit["start"], c.episode.start)
            hit["end"] = max(hit["end"], c.episode.end)
            hit["sensors"] |= sids
            hit["checks"].append(c)

    out = []
    for g in groups:
        if len(g["sensors"]) < min_sensors:
            continue
        peak = max((c.episode.peak for c in g["checks"]), key=lambda e: e.value)
        pts = [coords[s] for s in g["sensors"] if s in coords]
        out.append(Candidate(
            field=peak.field, start=g["start"], end=g["end"], sensor_ids=sorted(g["sensors"]),
            peak_sensor=peak.sensor_id, peak_value=peak.value, peak_ts=peak.ts, peak_score=peak.score,
            lat=round(sum(p[0] for p in pts) / len(pts), 4) if pts else None,
            lon=round(sum(p[1] for p in pts) / len(pts), 4) if pts else None,
            checks=g["checks"]))
    out.sort(key=lambda c: -c.peak_value)
    return out


def matching_event(cand: Candidate, events, slack_h: float = 12.0, near_km: float = 50.0):
    """The curated event (``storage.models.Event``-like) this candidate already is, if any.

    A match overlaps in time (``slack_h`` either side; an open-ended event counts one
    day) and either shares a sensor or sits within ``near_km`` of the candidate.
    """
    slack = timedelta(hours=slack_h)
    for e in events:
        if e.start_ts is None:
            continue
        start = e.start_ts.replace(tzinfo=None)
        end = (e.end_ts or e.start_ts + timedelta(days=1)).replace(tzinfo=None)
        if not (cand.start <= end + slack and cand.end >= start - slack):
            continue
        if set(cand.sensor_ids) & {str(s) for s in (e.sensor_ids or [])}:
            return e
        if None not in (cand.lat, e.lat, e.lon) and km_between((cand.lat, cand.lon), (e.lat, e.lon)) <= near_km:
            return e
    return None
