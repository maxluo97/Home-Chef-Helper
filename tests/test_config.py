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


def test_mealie_url_trailing_slash_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """MEALIE_URL is normalised so paths can be appended safely."""
    monkeypatch.setenv("MEALIE_URL", "http://nas.local:30111/")

    settings = make_settings()

    assert settings.mealie_url == "http://nas.local:30111"
