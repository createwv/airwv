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
    CommunityReading,
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


def _haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = (math.sin(dp / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dl / 2) ** 2)
    return 2 * 3959 * math.asin(math.sqrt(h))


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
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        """Idempotent, additive column back-fill for tables that predate a new field.

        ``create_all`` never alters existing tables, so a column added to a model
        won't appear on a database created by an older build. This adds any missing
        *nullable* columns via ``ALTER TABLE ... ADD COLUMN`` (supported by both
        SQLite and Postgres) so deploys self-heal without a manual migration step.
        Non-destructive: it only ever adds, never drops or retypes.
        """
        from sqlalchemy import inspect as _inspect, text
        wanted = {
            "reports": {"contact_name": "VARCHAR(160)"},
            "subscriptions": {"center_lat": "FLOAT", "center_lon": "FLOAT", "radius_km": "FLOAT"},
        }
        insp = _inspect(self._engine)
        with self._engine.begin() as conn:
            for table, cols in wanted.items():
                try:
                    have = {c["name"] for c in insp.get_columns(table)}
                except Exception:
                    continue
                for col, ddl in cols.items():
                    if col not in have:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))

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

    def airnow_vs_airdata(self, year: int | None = None, field: str = "pm2_5") -> dict:
        """Compare **preliminary** AirNow vs **finalized** AirData daily values for the
        same monitor+day — the QA audit. Reconciles AirNow's AQSID ('210130002') to
        AirData's dashed id ('21-013-0002'). Returns agreement stats plus how many
        monitor-days QA dropped (present in AirNow, gone from finalized) or added."""
        import statistics

        from sqlalchemy import text

        if field not in ("pm2_5", "ozone"):
            raise ValueError("field must be pm2_5 or ozone")
        yf = f"AND CAST(strftime('%Y', ts) AS INT) = {int(year)}" if year else ""
        ctes = (
            f"WITH an AS (SELECT substr(sensor_id,1,2)||'-'||substr(sensor_id,3,3)||'-'||"
            f"substr(sensor_id,6) AS mon, date(ts) AS d, avg({field}) AS v FROM readings "
            f"WHERE source='airnow' AND {field} IS NOT NULL AND length(sensor_id)=9 {yf} GROUP BY mon,d), "
            f"ad AS (SELECT sensor_id AS mon, date(ts) AS d, avg({field}) AS v FROM readings "
            f"WHERE source='epa_airdata' AND {field} IS NOT NULL {yf} GROUP BY mon,d)")
        with self._session_factory() as session:
            matched = session.execute(text(
                ctes + " SELECT an.mon, an.d, an.v, ad.v FROM an JOIN ad ON an.mon=ad.mon AND an.d=ad.d")).all()
            an_total = session.execute(text(ctes + " SELECT count(*) FROM an")).scalar() or 0
            ad_total = session.execute(text(ctes + " SELECT count(*) FROM ad")).scalar() or 0
        prelim = [float(r[2]) for r in matched]
        final = [float(r[3]) for r in matched]
        diffs = [f - p for p, f in zip(prelim, final)]
        adiffs = [abs(x) for x in diffs]
        n = len(matched)
        corr = None
        if n >= 2 and len(set(prelim)) > 1 and len(set(final)) > 1:
            try:
                corr = round(statistics.correlation(prelim, final), 4)
            except statistics.StatisticsError:
                corr = None
        top = sorted(matched, key=lambda r: abs(r[3] - r[2]), reverse=True)[:5]
        return {
            "field": field, "year": year,
            "matched_monitor_days": n,
            "mean_diff_final_minus_prelim": round(statistics.mean(diffs), 3) if diffs else None,
            "mean_abs_diff": round(statistics.mean(adiffs), 3) if adiffs else None,
            "median_abs_diff": round(statistics.median(adiffs), 3) if adiffs else None,
            "correlation": corr,
            "pct_within_1": round(100 * sum(x <= 1 for x in adiffs) / n, 1) if n else None,
            "pct_within_2": round(100 * sum(x <= 2 for x in adiffs) / n, 1) if n else None,
            "prelim_only_dropped_by_qa": an_total - n,   # in AirNow, not in finalized AirData
            "final_only": ad_total - n,                  # in finalized, not captured live
            "largest_diffs": [
                {"monitor": r[0], "date": str(r[1]), "prelim": round(float(r[2]), 1),
                 "final": round(float(r[3]), 1), "diff": round(float(r[3]) - float(r[2]), 1)}
                for r in top],
        }

    def coverage_overall(self) -> dict:
        """Overall first/last timestamp + total rows across all sensors (fast)."""
        with self._session_factory() as session:
            c, mn, mx = session.execute(
                select(func.count(), func.min(ReadingRow.ts), func.max(ReadingRow.ts))
            ).one()
        return {"count": c or 0, "first_ts": mn, "last_ts": mx}

    def coords_from_readings(self, source: str) -> dict[str, tuple]:
        """One (lat, lon) per sensor for a source, from stored readings.

        Used for reference monitors (EPA AirNow / AirData), whose coords live on the
        readings rather than in the community listing.
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

    def find_subscription(self, *, target: str, field: str, sensor_id: str | None):
        """Existing sub for this email+trigger, if any (to avoid duplicates)."""
        with self._session_factory() as session:
            stmt = select(Subscription).where(
                Subscription.target == target,
                Subscription.field == field,
                Subscription.sensor_id.is_(sensor_id) if sensor_id is None
                else Subscription.sensor_id == sensor_id,
            )
            return session.scalars(stmt).first()

    def update_subscription(self, subscription_id: int, **fields) -> bool:
        with self._session_factory() as session:
            sub = session.get(Subscription, subscription_id)
            if sub is None:
                return False
            for k, v in fields.items():
                if hasattr(sub, k):
                    setattr(sub, k, v)
            session.commit()
            return True

    def subscription_by_token(self, token: str):
        with self._session_factory() as session:
            return session.scalars(
                select(Subscription).where(Subscription.token == token)
            ).first()

    def confirm_subscription(self, token: str, when) -> Subscription | None:
        """Activate a pending sign-up. A single token can cover a *group* of
        subscriptions (one per chosen metric) — all are activated together.
        Returns a representative subscription (idempotent: already-confirmed
        links return unchanged)."""
        with self._session_factory() as session:
            subs = list(session.scalars(
                select(Subscription).where(Subscription.token == token)))
            if not subs:
                return None
            for sub in subs:
                if sub.confirmed_at is None:
                    sub.confirmed_at = when
                sub.active = True
            session.commit()
            first = subs[0]
            session.refresh(first)
            session.expunge(first)
            return first

    def deactivate_subscription(self, token: str) -> Subscription | None:
        """Turn off every subscription sharing this token (the whole group)."""
        with self._session_factory() as session:
            subs = list(session.scalars(
                select(Subscription).where(Subscription.token == token)))
            if not subs:
                return None
            for sub in subs:
                sub.active = False
            session.commit()
            first = subs[0]
            session.refresh(first)
            session.expunge(first)
            return first

    # -- reports & feedback ------------------------------------------------

    def add_report(self, **fields) -> int:
        with self._session_factory() as session:
            r = Report(**fields)
            session.add(r)
            session.commit()
            return r.id

    def set_report(self, report_id: int, **fields) -> bool:
        """Patch fields on a report (e.g. attach a photo_path after saving the image)."""
        with self._session_factory() as session:
            r = session.get(Report, report_id)
            if not r:
                return False
            for k, v in fields.items():
                if hasattr(r, k):
                    setattr(r, k, v)
            session.commit()
            return True

    def add_community_reading(self, **fields) -> int:
        """Store an optional structured measurement a resident attached to a report
        (e.g. VOC/conductivity/pH + value + unit). Clearly community-submitted and
        unverified until a maintainer confirms it — never mixed into the sensor network."""
        with self._session_factory() as session:
            cr = CommunityReading(**fields)
            session.add(cr)
            session.commit()
            return cr.id

    def community_readings_for_report(self, report_id: int) -> list[CommunityReading]:
        with self._session_factory() as session:
            return list(session.scalars(
                select(CommunityReading).where(CommunityReading.report_id == report_id)
                .order_by(CommunityReading.id)))

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

    def get_report(self, report_id: int) -> Report | None:
        with self._session_factory() as session:
            return session.get(Report, report_id)

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
            elif action == "approve_photo":
                r.photo_ok = True
            elif action == "reject_photo":
                r.photo_ok = False
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

    def water_near(self, lat: float, lon: float, km: float = 5.0,
                   limit: int = 6) -> list[dict]:
        """Water sample sites within ``km`` of a point, each with its latest value
        per parameter, nearest first. A cheap bounding-box query then a haversine
        filter (used to attach measured water to a coal discharger / facility)."""
        import math

        dlat = km / 111.0
        dlon = km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
        with self._session_factory() as session:
            sub = (select(WaterReading.site_id, WaterReading.parameter,
                          func.max(WaterReading.ts).label("mts"))
                   .where(WaterReading.lat.between(lat - dlat, lat + dlat),
                          WaterReading.lon.between(lon - dlon, lon + dlon))
                   .group_by(WaterReading.site_id, WaterReading.parameter).subquery())
            q = select(WaterReading).join(
                sub, and_(WaterReading.site_id == sub.c.site_id,
                          WaterReading.parameter == sub.c.parameter,
                          WaterReading.ts == sub.c.mts))
            sites: dict[str, dict] = {}
            for w in session.scalars(q):
                if w.lat is None or w.lon is None:
                    continue
                s = sites.setdefault(w.site_id, {
                    "site_id": w.site_id, "name": w.site_name,
                    "lat": w.lat, "lon": w.lon, "latest": {}})
                s["latest"][w.parameter] = {"value": w.value, "unit": w.unit,
                                            "ts": w.ts.isoformat() if w.ts else None}
        out = []
        for s in sites.values():
            d = _haversine_mi(lat, lon, s["lat"], s["lon"])
            if d <= km * 0.621371:
                out.append({**s, "mi": round(d, 1)})
        out.sort(key=lambda s: s["mi"])
        return out[:limit]

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
