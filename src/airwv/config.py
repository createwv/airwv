"""Application configuration, loaded from the environment.

Secrets (API keys, DB credentials) are read from environment variables. In
development they can be provided via a local ``.env`` file (see ``.env.example``);
in production supply them through your platform's secret manager. Nothing
sensitive should ever be committed to the repository.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # optional in prod where env is already populated
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass(frozen=True)
class Config:
    """Runtime configuration for AirWV."""

    purpleair_api_key: str
    database_url: str
    poll_interval_seconds: int
    index_cache_path: Path

    @classmethod
    def from_env(cls) -> "Config":
        api_key = os.environ.get("PURPLEAIR_API_KEY", "").strip()
        if not api_key:
            raise ConfigError(
                "PURPLEAIR_API_KEY is not set. Copy .env.example to .env and add "
                "your PurpleAir API key, or set it in your environment/secret manager."
            )

        # Default to a local SQLite file so development needs zero setup.
        database_url = os.environ.get("AIRWV_DATABASE_URL", "").strip() or "sqlite:///airwv.sqlite"

        # Default hourly: PurpleAir bills API points per sensor per call, so tight
        # intervals across many sensors are costly. Hourly is sustainable on the
        # free tier for ~30 sensors; lower it if you have points to spare.
        poll_interval = int(os.environ.get("AIRWV_POLL_INTERVAL_SECONDS", "3600"))

        # Private device -> sensor_index cache (gitignored data/ dir by default).
        index_cache = os.environ.get("AIRWV_INDEX_CACHE", "").strip() or "data/sensor_index_map.json"

        return cls(
            purpleair_api_key=api_key,
            database_url=database_url,
            poll_interval_seconds=poll_interval,
            index_cache_path=Path(index_cache),
        )
