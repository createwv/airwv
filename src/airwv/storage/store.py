"""The storage API used by ingestion and analysis.

``Store`` wraps a SQLAlchemy engine. It supports SQLite (dev default) and
PostgreSQL (prod) through the same interface — the connection is chosen by
``AIRWV_DATABASE_URL``. Writes are idempotent: duplicate readings (same
``source``/``sensor_id``/``ts``) are ignored rather than raising or duplicating.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Iterable

from sqlalchemy import and_, create_engine, func, insert, select
from sqlalchemy.orm import Session, sessionmaker

from airwv.config import Config
from airwv.sources.base import Reading
from airwv.storage.models import (
    Base,
    Event,
    Feedback,
    FieldReading,
    ReadingRow,
    Report,
    Subscription,
    WaterReading,
    utcnow,
)
from airwv.validate import validate_reading

log = logging.getLogger("airwv.storage")

# Columns that come straight from a Reading (everything except bookkeeping).
_READING_FIELDS = (
    "source",
    "sensor_id",
    "ts",
    "lat",
    "lon",
    "pm1_0",
    "pm2_5",
    "pm10",
    "aqi",
    "voc",
    "ozone",
    "temperature",
    "humidity",
    "pressure",
    "pm2_5_a",
    "pm2_5_b",
    "confidence",
    "count_0_3",
    "count_0_5",
    "count_1_0",
    "count_2_5",
    "count_5_0",
    "count_10_0",
    "raw",
)


class Store:
    """Durable storage for normalized readings."""

    def __init__(self, database_url: str):
        self._engine = create_engine(database_url, future=True)
        self._session_factory: sessionmaker[Session] = sessionmaker(
            bind=self._engine, future=True
        )

    @classmethod
    def from_config(cls, config: Config) -> "Store":
        return cls(config.database_url)

    def create_schema(self) -> None:
        """Create tables if they don't exist.

        Fine for dev and initial deploys. A migration tool (e.g. Alembic) should
        take over once the schema starts to evolve.
        """
        Base.metadata.create_all(self._engine)

    def save_readings(self, readings: Iterable[Reading]) -> int:
        """Persist readings. Returns the number written (inserted or updated).

        Idempotent by ``(source, sensor_id, ts)``: re-ingesting the same reading
        **upserts** — it updates the row's fields in place (so a re-pull with a
        richer field set enriches existing rows), never duplicating. Out-of-range
        values are flagged ``quality = "suspect"`` (never dropped).
        """
        rows = [self._to_row(r) for r in readings]
        if not rows:
            return 0

        suspect = sum(1 for row in rows if row["quality"] == "suspect")
        if suspect:
            log.warning("%d/%d readings flagged suspect (out-of-range values)", suspect, len(rows))

        # Batch so we never exceed the driver's bind-parameter limit (SQLite/Postgres)
        # on large bulk imports (e.g. a year of EPA AirData is tens of thousands of rows).
        batch = 500
        if self._upsert_stmt(rows[:1]) is not None:
            written = 0
            with self._session_factory() as session:
                for i in range(0, len(rows), batch):
                    result = session.execute(self._upsert_stmt(rows[i:i + batch]))
                    written += result.rowcount
                session.commit()
            return written

        # Portable fallback for dialects without native upsert: check-then-write.
        return self._save_readings_fallback(rows)

    def count(self) -> int:
        with self._session_factory() as session:
            return session.scalar(select(func.count()).select_from(ReadingRow)) or 0

    def recent(self, limit: int = 100) -> list[ReadingRow]:
        with self._session_factory() as session:
            stmt = select(ReadingRow).order_by(ReadingRow.ts.desc()).limit(limit)
            return list(session.scalars(stmt))

    def distinct_sensor_ids(self) -> list[str]:
        with self._session_factory() as session:
            return list(session.scalars(select(ReadingRow.sensor_id).distinct()))

    def sensor_ids_by_source(self, source: str) -> list[str]:
        with self._session_factory() as session:
            return list(session.scalars(
                select(ReadingRow.sensor_id).where(ReadingRow.source == source).distinct()))

    def readings_for_sensor(self, sensor_id: str, since=None, until=None) -> list[ReadingRow]:
        """A sensor's readings in chronological order, optionally bounded to [since, until]."""
        with self._session_factory() as session:
            stmt = select(ReadingRow).where(ReadingRow.sensor_id == sensor_id)
            if since is not None:
                stmt = stmt.where(ReadingRow.ts >= since)
            if until is not None:
                stmt = stmt.where(ReadingRow.ts <= until)
            stmt = stmt.order_by(ReadingRow.ts.asc())
            return list(session.scalars(stmt))

    def last_ts_for_sensor(self, sensor_id: str):
        """Most recent timestamp for a sensor (indexed max — used to pick a default window)."""
        with self._session_factory() as session:
            return session.scalar(select(func.max(ReadingRow.ts)).where(ReadingRow.sensor_id == sensor_id))

    def latest_reading_per_sensor(self) -> dict[str, ReadingRow]:
        """Most recent stored reading for each sensor."""
        latest: dict[str, ReadingRow] = {}
        for sid in self.distinct_sensor_ids():
            rows = self.readings_for_sensor(sid)
            if rows:
                latest[sid] = rows[-1]
        return latest

    def sensor_coverage(self) -> dict[str, dict]:
        """Per-sensor row count + first/last timestamp via SQL aggregation (fast).

        Avoids loading every row just to summarize — one GROUP BY instead.
        """
        with self._session_factory() as session:
            rows = session.execute(
                select(ReadingRow.sensor_id, func.count(), func.min(ReadingRow.ts), func.max(ReadingRow.ts))
                .group_by(ReadingRow.sensor_id)
            ).all()
        return {sid: {"count": c, "first_ts": mn, "last_ts": mx} for sid, c, mn, mx in rows}

    def latest_value_per_sensor(self, field: str = "pm2_5") -> dict[str, float]:
        """Most recent non-null value of ``field`` per sensor.

        Per-sensor indexed LIMIT-1 lookups (fast with the (sensor_id, ts) index) —
        a single window function over millions of rows was far slower.
        """
        col = getattr(ReadingRow, field)
        out: dict[str, float] = {}
        with self._session_factory() as session:
            for sid in session.scalars(select(ReadingRow.sensor_id).distinct()):
                val = session.scalar(
                    select(col).where((ReadingRow.sensor_id == sid) & col.isnot(None))
                    .order_by(ReadingRow.ts.desc()).limit(1))
                if val is not None:
                    out[sid] = val
        return out

    def daily_avg(self, sensor_id: str, field: str = "pm2_5", since=None, until=None) -> dict:
        """Daily average of ``field`` for a sensor, aggregated in SQL (fast over years —
        avoids loading raw rows for validation). Returns {date: value}."""
        from datetime import date as _date

        col = getattr(ReadingRow, field)
        day = func.date(ReadingRow.ts)
        stmt = select(day, func.avg(col)).where((ReadingRow.sensor_id == sensor_id) & col.isnot(None))
        if since is not None:
            stmt = stmt.where(ReadingRow.ts >= since)
        if until is not None:
            stmt = stmt.where(ReadingRow.ts <= until)
        with self._session_factory() as session:
            rows = session.execute(stmt.group_by(day)).all()
        return {_date.fromisoformat(str(d)[:10]): float(v) for d, v in rows if v is not None}

    def daily_avg_corrected(self, sensor_id: str, since=None, until=None) -> dict:
        """Daily average of EPA-corrected PM2.5 (Barkjohn: 0.524·PA − 0.0862·RH + 5.75),
        computed in SQL. Only days with both PM2.5 and humidity."""
        from datetime import date as _date

        expr = 0.524 * ReadingRow.pm2_5 - 0.0862 * ReadingRow.humidity + 5.75
        day = func.date(ReadingRow.ts)
        stmt = select(day, func.avg(expr)).where(
            (ReadingRow.sensor_id == sensor_id) & ReadingRow.pm2_5.isnot(None) & ReadingRow.humidity.isnot(None))
        if since is not None:
            stmt = stmt.where(ReadingRow.ts >= since)
        if until is not None:
            stmt = stmt.where(ReadingRow.ts <= until)
        with self._session_factory() as session:
            rows = session.execute(stmt.group_by(day)).all()
        return {_date.fromisoformat(str(d)[:10]): max(0.0, float(v)) for d, v in rows if v is not None}

    def coverage_overall(self) -> dict:
        """Overall first/last timestamp + total rows across all sensors (fast)."""
        with self._session_factory() as session:
            c, mn, mx = session.execute(
                select(func.count(), func.min(ReadingRow.ts), func.max(ReadingRow.ts))
            ).one()
        return {"count": c or 0, "first_ts": mn, "last_ts": mx}

    def coords_from_readings(self, source: str) -> dict[str, tuple]:
        """One (lat, lon) per sensor for a source, from stored readings.

        Used for reference monitors (OpenAQ), whose coords live on the readings
        rather than in the community listing.
        """
        out: dict[str, tuple] = {}
        with self._session_factory() as session:
            sids = list(session.scalars(
                select(ReadingRow.sensor_id).where(ReadingRow.source == source).distinct()))
            for sid in sids:
                row = session.execute(
                    select(ReadingRow.lat, ReadingRow.lon).where(
                        (ReadingRow.sensor_id == sid) & (ReadingRow.source == source)
                        & ReadingRow.lat.isnot(None)).limit(1)).first()
                if row:
                    out[sid] = (row[0], row[1])
        return out

    # -- subscriptions -----------------------------------------------------

    def add_subscription(self, **fields) -> int:
        with self._session_factory() as session:
            sub = Subscription(**fields)
            session.add(sub)
            session.commit()
            return sub.id

    def list_subscriptions(self, active_only: bool = True) -> list[Subscription]:
        with self._session_factory() as session:
            stmt = select(Subscription)
            if active_only:
                stmt = stmt.where(Subscription.active.is_(True))
            return list(session.scalars(stmt))

    def mark_notified(self, subscription_id: int, when) -> None:
        with self._session_factory() as session:
            sub = session.get(Subscription, subscription_id)
            if sub is not None:
                sub.last_notified_at = when
                session.commit()

    # -- reports & feedback ------------------------------------------------

    def add_report(self, **fields) -> int:
        with self._session_factory() as session:
            r = Report(**fields)
            session.add(r)
            session.commit()
            return r.id

    def add_feedback(self, **fields) -> int:
        with self._session_factory() as session:
            f = Feedback(**fields)
            session.add(f)
            session.commit()
            return f.id

    # ---- curated events (see docs/ROADMAP Events page) ----
    def add_event(self, **fields) -> int:
        with self._session_factory() as session:
            e = Event(**fields)
            session.add(e)
            session.commit()
            return e.id

    def published_events(self, limit: int = 200) -> list[Event]:
        """Public events (published), newest first by start time."""
        with self._session_factory() as session:
            stmt = (select(Event).where(Event.status == "published")
                    .order_by(Event.start_ts.desc().nullslast(), Event.id.desc()).limit(limit))
            return list(session.scalars(stmt))

    def events_for_admin(self, limit: int = 300) -> list[Event]:
        with self._session_factory() as session:
            return list(session.scalars(
                select(Event).order_by(Event.id.desc()).limit(limit)))

    def get_event(self, event_id: int) -> Event | None:
        with self._session_factory() as session:
            return session.get(Event, event_id)

    def update_event(self, event_id: int, **fields) -> bool:
        with self._session_factory() as session:
            e = session.get(Event, event_id)
            if not e:
                return False
            for k, v in fields.items():
                if v is not None and hasattr(e, k):
                    setattr(e, k, v)
            session.commit()
            return True

    def delete_event(self, event_id: int) -> bool:
        with self._session_factory() as session:
            e = session.get(Event, event_id)
            if not e:
                return False
            session.delete(e)
            session.commit()
            return True

    def published_reports(self, domain: str | None = None, limit: int = 500) -> list[Report]:
        """Reports visible to the public (published-unverified or confirmed)."""
        with self._session_factory() as session:
            stmt = select(Report).where(Report.stage.in_(("published_unverified", "confirmed")))
            if domain:
                stmt = stmt.where(Report.domain == domain)
            return list(session.scalars(stmt.order_by(Report.created_at.desc()).limit(limit)))

    def flag_report(self, report_id: int, hide_at: int = 3) -> int:
        """Public flag; auto-hide (→ held) once ``hide_at`` flags accumulate."""
        with self._session_factory() as session:
            r = session.get(Report, report_id)
            if r is None:
                return -1
            r.flags_count += 1
            if r.flags_count >= hide_at and r.stage == "published_unverified":
                r.stage = "held"
                r.screen_reason = (r.screen_reason or "") and (r.screen_reason + "; ") or ""
                r.screen_reason = (r.screen_reason + "flagged").strip("; ")
            session.commit()
            return r.flags_count

    def count_reports_since(self, ip_hash: str, minutes: int = 60) -> int:
        from datetime import datetime, timedelta, timezone

        since = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)
        with self._session_factory() as session:
            return session.scalar(
                select(func.count()).select_from(Report)
                .where((Report.ip_hash == ip_hash) & (Report.created_at >= since))) or 0

    # -- admin / moderation (token-gated in the web layer) -----------------

    def reports_for_admin(self, status: str = "held", limit: int = 300) -> list[Report]:
        """Full report records for a moderation queue (includes private fields)."""
        with self._session_factory() as session:
            stmt = select(Report)
            if status == "held":
                stmt = stmt.where(Report.stage == "held")
            elif status == "unverified":
                stmt = stmt.where(Report.stage == "published_unverified")
            elif status == "flagged":
                stmt = stmt.where(Report.flags_count > 0)
            elif status == "confirmed":
                stmt = stmt.where(Report.stage == "confirmed")
            else:  # all but removed
                stmt = stmt.where(Report.stage != "removed")
            return list(session.scalars(stmt.order_by(Report.created_at.desc()).limit(limit)))

    def moderate_report(self, report_id: int, action: str,
                        mod_note: str | None = None, verified_by: str | None = None) -> bool:
        with self._session_factory() as session:
            r = session.get(Report, report_id)
            if r is None:
                return False
            if action == "confirm":
                r.stage = "confirmed"
                r.verified_by = verified_by or "maintainer"
            elif action in ("publish", "keep"):
                r.stage = "published_unverified"
            elif action == "remove":
                r.stage = "removed"
            elif action == "approve_org":
                r.org_public = True
            if mod_note:
                r.mod_note = ((r.mod_note + " | ") if r.mod_note else "") + mod_note
            session.commit()
            return True

    def feedback_for_admin(self, status: str | None = None, limit: int = 300) -> list[Feedback]:
        with self._session_factory() as session:
            stmt = select(Feedback)
            if status in ("new", "triaged", "done"):
                stmt = stmt.where(Feedback.status == status)
            return list(session.scalars(stmt.order_by(Feedback.created_at.desc()).limit(limit)))

    def update_feedback(self, feedback_id: int, status: str) -> bool:
        with self._session_factory() as session:
            f = session.get(Feedback, feedback_id)
            if f is None:
                return False
            f.status = status
            session.commit()
            return True

    # -- internals ---------------------------------------------------------

    def _to_row(self, reading: Reading) -> dict:
        data = asdict(reading)
        row = {k: data[k] for k in _READING_FIELDS}
        row["quality"] = "suspect" if validate_reading(reading) else "ok"
        row["ingested_at"] = utcnow()
        return row

    def _upsert_stmt(self, rows: list[dict]):
        """Dialect-specific INSERT ... ON CONFLICT DO UPDATE (enrich in place), or None."""
        dialect = self._engine.dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt = pg_insert(ReadingRow).values(rows)
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(ReadingRow).values(rows)
        else:
            return None

        # Update every non-key column on conflict, so re-pulls enrich existing rows.
        keys = {"source", "sensor_id", "ts"}
        updates = {c: stmt.excluded[c] for c in rows[0] if c not in keys}
        return stmt.on_conflict_do_update(
            index_elements=["source", "sensor_id", "ts"],
            set_=updates,
        )

    def _save_readings_fallback(self, rows: list[dict]) -> int:
        written = 0
        with self._session_factory() as session:
            for row in rows:
                existing = session.scalar(
                    select(ReadingRow).where(
                        ReadingRow.source == row["source"],
                        ReadingRow.sensor_id == row["sensor_id"],
                        ReadingRow.ts == row["ts"],
                    )
                )
                if existing is not None:  # enrich in place
                    for key, value in row.items():
                        if key not in ("source", "sensor_id", "ts"):
                            setattr(existing, key, value)
                else:
                    session.add(ReadingRow(**row))
                written += 1
            session.commit()
        return written

    def count_readings(self, sensor_id: str, start=None, end=None) -> int:
        """How many readings we already have for a sensor in [start, end)."""
        with self._session_factory() as session:
            stmt = select(func.count()).select_from(ReadingRow).where(
                ReadingRow.sensor_id == sensor_id
            )
            if start is not None:
                stmt = stmt.where(ReadingRow.ts >= start)
            if end is not None:
                stmt = stmt.where(ReadingRow.ts < end)
            return session.scalar(stmt) or 0

    # ---- water (USGS/WQP) — tall water_readings table ----
    def add_water_readings(self, rows: list[dict]) -> int:
        """Bulk-insert water readings; idempotent by (source, site_id, ts, parameter)."""
        if not rows:
            return 0
        batch = 500
        stmt = insert(WaterReading).prefix_with("OR IGNORE")
        with self._session_factory() as session:
            for i in range(0, len(rows), batch):
                session.execute(stmt, rows[i:i + batch])   # executemany, INSERT OR IGNORE
            session.commit()
        return len(rows)

    def water_sites(self) -> list[dict]:
        """Every water site with its most recent value per parameter (for the map)."""
        with self._session_factory() as session:
            sub = (select(WaterReading.site_id, WaterReading.parameter,
                          func.max(WaterReading.ts).label("mts"))
                   .group_by(WaterReading.site_id, WaterReading.parameter).subquery())
            q = select(WaterReading).join(
                sub, and_(WaterReading.site_id == sub.c.site_id,
                          WaterReading.parameter == sub.c.parameter,
                          WaterReading.ts == sub.c.mts))
            sites: dict[str, dict] = {}
            for w in session.scalars(q):
                s = sites.setdefault(w.site_id, {
                    "site_id": w.site_id, "name": w.site_name,
                    "lat": w.lat, "lon": w.lon, "latest": {}})
                s["latest"][w.parameter] = {"value": w.value, "unit": w.unit,
                                            "ts": w.ts.isoformat() if w.ts else None}
            return list(sites.values())

    def water_series(self, site_id: str, parameter: str, since=None) -> list[dict]:
        with self._session_factory() as session:
            stmt = (select(WaterReading.ts, WaterReading.value, WaterReading.unit)
                    .where((WaterReading.site_id == site_id)
                           & (WaterReading.parameter == parameter)))
            if since is not None:
                stmt = stmt.where(WaterReading.ts >= since)
            stmt = stmt.order_by(WaterReading.ts)
            return [{"ts": ts.isoformat(), "value": v, "unit": u}
                    for ts, v, u in session.execute(stmt)]

    # ---- field readings (trained-scientist spot checks) ----
    def add_field_reading(self, **fields) -> int:
        with self._session_factory() as session:
            fr = FieldReading(**fields)
            session.add(fr)
            session.commit()
            return fr.id

    def published_field_readings(self, limit: int = 500) -> list[FieldReading]:
        with self._session_factory() as session:
            return list(session.scalars(
                select(FieldReading).where(FieldReading.status == "published")
                .order_by(FieldReading.observed_at.desc().nullslast(), FieldReading.id.desc())
                .limit(limit)))

    def field_readings_for_admin(self, limit: int = 500) -> list[FieldReading]:
        with self._session_factory() as session:
            return list(session.scalars(
                select(FieldReading).order_by(FieldReading.id.desc()).limit(limit)))

    def get_field_reading(self, fid: int) -> FieldReading | None:
        with self._session_factory() as session:
            return session.get(FieldReading, fid)

    def set_field_reading(self, fid: int, **fields) -> bool:
        with self._session_factory() as session:
            fr = session.get(FieldReading, fid)
            if not fr:
                return False
            for k, v in fields.items():
                if hasattr(fr, k):
                    setattr(fr, k, v)
            session.commit()
            return True
