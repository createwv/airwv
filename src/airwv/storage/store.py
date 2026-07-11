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

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from airwv.config import Config
from airwv.sources.base import Reading
from airwv.storage.models import Base, ReadingRow, Subscription, utcnow
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
