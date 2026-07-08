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
        """Persist readings, skipping duplicates. Returns the number inserted.

        Out-of-range values are flagged ``quality = "suspect"`` (never dropped).
        """
        rows = [self._to_row(r) for r in readings]
        if not rows:
            return 0

        suspect = sum(1 for row in rows if row["quality"] == "suspect")
        if suspect:
            log.warning("%d/%d readings flagged suspect (out-of-range values)", suspect, len(rows))

        stmt = self._insert_ignore_stmt(rows)
        if stmt is not None:
            with self._session_factory() as session:
                result = session.execute(stmt)
                session.commit()
                return result.rowcount

        # Portable fallback for dialects without native upsert: check-then-insert.
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

    def readings_for_sensor(self, sensor_id: str, since=None) -> list[ReadingRow]:
        """Return a sensor's readings in chronological order (optionally since a time)."""
        with self._session_factory() as session:
            stmt = select(ReadingRow).where(ReadingRow.sensor_id == sensor_id)
            if since is not None:
                stmt = stmt.where(ReadingRow.ts >= since)
            stmt = stmt.order_by(ReadingRow.ts.asc())
            return list(session.scalars(stmt))

    def latest_reading_per_sensor(self) -> dict[str, ReadingRow]:
        """Most recent stored reading for each sensor."""
        latest: dict[str, ReadingRow] = {}
        for sid in self.distinct_sensor_ids():
            rows = self.readings_for_sensor(sid)
            if rows:
                latest[sid] = rows[-1]
        return latest

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

    def _insert_ignore_stmt(self, rows: list[dict]):
        """Return a dialect-specific INSERT ... ON CONFLICT DO NOTHING, or None."""
        dialect = self._engine.dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            return pg_insert(ReadingRow).values(rows).on_conflict_do_nothing()
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            return sqlite_insert(ReadingRow).values(rows).on_conflict_do_nothing()
        return None

    def _save_readings_fallback(self, rows: list[dict]) -> int:
        inserted = 0
        with self._session_factory() as session:
            for row in rows:
                exists = session.scalar(
                    select(func.count())
                    .select_from(ReadingRow)
                    .where(
                        ReadingRow.source == row["source"],
                        ReadingRow.sensor_id == row["sensor_id"],
                        ReadingRow.ts == row["ts"],
                    )
                )
                if exists:
                    continue
                session.add(ReadingRow(**row))
                inserted += 1
            session.commit()
        return inserted
