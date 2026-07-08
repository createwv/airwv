"""Database models for AirWV storage.

One table, ``readings``, holds normalized air-quality readings. It mirrors the
:class:`~airwv.sources.base.Reading` schema plus bookkeeping columns:

- ``quality`` — set by the analysis stage (``ok`` / ``suspect`` / ``malfunction``).
  Defaults to ``ok`` at ingest; we flag, never delete (see ARCHITECTURE.md).
- ``raw`` — the original provider payload, so readings can be reprocessed later.
- ``ingested_at`` — when AirWV stored the row.

A uniqueness constraint on ``(source, sensor_id, ts)`` gives us idempotent writes:
re-ingesting the same reading is a no-op rather than a duplicate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Timezone-aware current UTC time (used as the ``ingested_at`` default)."""
    return datetime.now(tz=timezone.utc)


class Base(DeclarativeBase):
    pass


class ReadingRow(Base):
    """A stored, normalized air-quality reading."""

    __tablename__ = "readings"
    __table_args__ = (
        UniqueConstraint("source", "sensor_id", "ts", name="uq_reading_source_sensor_ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    source: Mapped[str] = mapped_column(String(64), index=True)
    sensor_id: Mapped[str] = mapped_column(String(128), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    pm1_0: Mapped[float | None] = mapped_column(Float, nullable=True)
    pm2_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    pm10: Mapped[float | None] = mapped_column(Float, nullable=True)
    aqi: Mapped[float | None] = mapped_column(Float, nullable=True)
    voc: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    pressure: Mapped[float | None] = mapped_column(Float, nullable=True)

    quality: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Subscription(Base):
    """A person/system asking to be alerted when a field crosses a threshold."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    channel: Mapped[str] = mapped_column(String(16))  # email | sms | webhook | log
    target: Mapped[str] = mapped_column(String(256))  # address / phone / URL

    # None sensor_id = any sensor. field/threshold define the trigger.
    sensor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    field: Mapped[str] = mapped_column(String(32), default="pm2_5")
    threshold: Mapped[float] = mapped_column(Float)

    active: Mapped[bool] = mapped_column(default=True)
    # Rate limiting + quiet hours (local) so people aren't spammed.
    min_interval_seconds: Mapped[int] = mapped_column(Integer, default=21600)  # 6h
    quiet_start: Mapped[int | None] = mapped_column(Integer, nullable=True)  # local hour
    quiet_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
