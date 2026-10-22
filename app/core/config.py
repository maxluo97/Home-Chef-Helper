"""Application settings, read from environment variables only (plan §7.3).

Every setting has a dev-friendly default so `uv run` works on a fresh clone.
In Docker, the compose file sets the real values. A local `.env` file in the
repo root is also read, for convenience; it is git-ignored.
"""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Filename of the SQLite database inside the data directory.
DATABASE_FILENAME = "chef-helper.db"


class Settings(BaseSettings):
    """All runtime configuration. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # The .env file may also hold values for dev scripts (e.g.
        # MEALIE_TOKEN); ignore anything this class doesn't declare.
        extra="ignore",
    )

    # --- Storage ---
    # Single directory for the DB, uploads, and backups. Docker mounts /data;
    # local dev uses ./data in the repo (git-ignored).
    data_dir: Path = Path("data")

    # SQLAlchemy URL. Left empty, it is derived from data_dir (see below), so
    # switching to Postgres later is just setting this variable.
    database_url: str = ""

    # --- Login sessions ---
    # How long a login lasts before asking for the password again. Long,
    # because the app is mostly used on personal phones in the kitchen.
    session_lifetime_days: int = 90
    # Send the login cookie over HTTPS only. Off by default because the dev
    # server (and a plain-LAN NAS) use http; turn on behind HTTPS.
    cookie_secure: bool = False

    # --- Secrets ---
    # Installation-wide key that encrypts each household's Mealie token in
    # the database (plan §9.8). Optional so the app still starts without it;
    # connecting a household to Mealie then fails with a clear message.
    # SecretStr keeps it out of logs and error pages.
    chef_secret_key: SecretStr | None = None

    # --- Integrations ---
    # Base URL Chef Helper uses to call Mealie. Per-household API tokens live
    # in the database (plan §9.8), not here.
    mealie_url: str = "http://localhost:9925"
    # Base URL people's browsers use to open Mealie ("Open in Mealie" links).
    # Differs from mealie_url when the server reaches Mealie over a private
    # Docker network. Empty = same as mealie_url.
    mealie_public_url: str = ""

    # --- Display ---
    # Timezone for showing times and reading times typed into forms (the
    # standard TZ env var, also used by the containers). IANA name.
    tz: str = "America/New_York"

    @model_validator(mode="after")
    def derive_defaults(self) -> "Settings":
        """Fill in values derived from other settings, and normalise URLs."""
        if not self.database_url:
            database_path = self.data_dir / DATABASE_FILENAME
            self.database_url = f"sqlite:///{database_path.as_posix()}"
        # Normalise so callers can append paths without doubling slashes.
        self.mealie_url = self.mealie_url.rstrip("/")
        self.mealie_public_url = (self.mealie_public_url or self.mealie_url).rstrip("/")
        # Fail at startup, not on first page view, if TZ is misspelled.
        ZoneInfo(self.tz)
        return self

    @property
    def timezone(self) -> ZoneInfo:
        """The display timezone as a tzinfo object."""
        return ZoneInfo(self.tz)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, read once on first use.

    Used as a FastAPI dependency, so tests can swap it out with
    `app.dependency_overrides[get_settings]`.
    """
    return Settings()
