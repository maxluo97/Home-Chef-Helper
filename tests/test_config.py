"""Tests for environment-variable configuration."""

from pathlib import Path

import pytest

from app.core.config import Settings


def make_settings() -> Settings:
    """Build Settings from the environment only, ignoring any local .env."""
    return Settings(_env_file=None)


def test_defaults_derive_sqlite_url_from_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """With nothing set, the DB is a SQLite file in ./data."""
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = make_settings()

    assert settings.data_dir == Path("data")
    assert settings.database_url == "sqlite:///data/chef-helper.db"


def test_data_dir_env_moves_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATA_DIR=/data (as in Docker) puts the DB under /data."""
    monkeypatch.setenv("DATA_DIR", "/data")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = make_settings()

    assert settings.database_url == "sqlite:////data/chef-helper.db"


def test_explicit_database_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit DATABASE_URL is used as-is (e.g. Postgres later)."""
    postgres_url = "postgresql://chef:secret@db/chef"
    monkeypatch.setenv("DATABASE_URL", postgres_url)

    settings = make_settings()

    assert settings.database_url == postgres_url


def test_mealie_public_url_defaults_to_mealie_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without MEALIE_PUBLIC_URL, browser links use MEALIE_URL."""
    monkeypatch.setenv("MEALIE_URL", "http://mealie:9000")
    monkeypatch.delenv("MEALIE_PUBLIC_URL", raising=False)

    assert make_settings().mealie_public_url == "http://mealie:9000"

    monkeypatch.setenv("MEALIE_PUBLIC_URL", "http://nas.local:30111/")
    assert make_settings().mealie_public_url == "http://nas.local:30111"


def test_timezone_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """TZ picks the display timezone; a misspelled one fails at startup."""
    monkeypatch.setenv("TZ", "Europe/Berlin")
    assert make_settings().timezone.key == "Europe/Berlin"

    monkeypatch.setenv("TZ", "Mars/Olympus_Mons")
    with pytest.raises(Exception, match="Mars/Olympus_Mons"):
        make_settings()


def test_mealie_url_trailing_slash_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """MEALIE_URL is normalised so paths can be appended safely."""
    monkeypatch.setenv("MEALIE_URL", "http://nas.local:30111/")

    settings = make_settings()

    assert settings.mealie_url == "http://nas.local:30111"
