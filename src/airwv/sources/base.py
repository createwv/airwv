"""Common interface every data source implements.

A :class:`Source` knows how to talk to one upstream provider (PurpleAir, EPA
AirNow, etc.) and return a list of normalized :class:`Reading` objects. Keeping
this interface small lets ingestion treat all sources uniformly and makes adding
new providers a matter of writing one adapter.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Reading:
    """A single normalized air-quality reading.

    Optional pollutant fields are ``None`` when a sensor does not report them.
    ``raw`` preserves the original provider payload so data can be reprocessed
    later without re-fetching.
    """

    source: str
    sensor_id: str
    ts: datetime
    lat: float | None = None
    lon: float | None = None
    pm1_0: float | None = None
    pm2_5: float | None = None
    pm10: float | None = None
    aqi: float | None = None
    voc: float | None = None
    ozone: float | None = None          # ppb — EPA reference monitors only (AirNow)
    temperature: float | None = None
    humidity: float | None = None
    pressure: float | None = None
    # Dual-channel PM2.5 (A/B laser counters) — divergence signals a malfunction.
    pm2_5_a: float | None = None
    pm2_5_b: float | None = None
    # PurpleAir channel-agreement confidence (0-100).
    confidence: float | None = None
    # Particle counts by size (per deciliter) — for source characterization.
    count_0_3: float | None = None
    count_0_5: float | None = None
    count_1_0: float | None = None
    count_2_5: float | None = None
    count_5_0: float | None = None
    count_10_0: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class Source(abc.ABC):
    """Base class for a data source adapter."""

    #: short, stable identifier stored on each reading, e.g. ``"purpleair"``.
    name: str

    @abc.abstractmethod
    def fetch_current(self) -> list[Reading]:
        """Return the most recent readings for this source's sensors."""

    def fetch_history(self, start: datetime, end: datetime) -> list[Reading]:
        """Return historical readings in ``[start, end]``.

        Optional — override in sources that support historical backfill.
        """
        raise NotImplementedError(f"{self.name} does not support history backfill yet")
