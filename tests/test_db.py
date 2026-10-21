"""Tests for engine setup (SQLite pragmas) and the UTC datetime column."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import StatementError

from app.core.db import SQLITE_BUSY_TIMEOUT_MS, make_engine, make_session_factory
from app.core.migrate import upgrade_to_head
from app.models import Household


def sqlite_url(tmp_path: Path) -> str:
    """URL of a throwaway SQLite file in this test's temp folder."""
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


def read_pragma(engine: Engine, pragma_name: str) -> object:
    """Return the current value of one SQLite PRAGMA on a fresh connection."""
    with engine.connect() as connection:
        return connection.execute(text(f"PRAGMA {pragma_name}")).scalar()


# --- Engine / pragmas ---


def test_sqlite_engine_applies_pragmas(tmp_path: Path) -> None:
    """New connections use WAL, enforce foreign keys, and wait on locks."""
    engine = make_engine(sqlite_url(tmp_path))
    try:
        assert read_pragma(engine, "journal_mode") == "wal"
        assert read_pragma(engine, "foreign_keys") == 1
        assert read_pragma(engine, "busy_timeout") == SQLITE_BUSY_TIMEOUT_MS
    finally:
        engine.dispose()


def test_engine_creates_missing_database_folder(tmp_path: Path) -> None:
    """A SQLite URL inside folders that don't exist yet still works.

    Regression: `uv run alembic upgrade head` on a fresh clone failed with
    "unable to open database file" because ./data/ didn't exist.
    """
    database_path = tmp_path / "not" / "yet" / "here" / "test.db"
    engine = make_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        assert read_pragma(engine, "journal_mode") == "wal"
        assert database_path.is_file()
    finally:
        engine.dispose()


def test_foreign_keys_can_be_disabled_for_migrations(tmp_path: Path) -> None:
    """The migration engine turns foreign-key enforcement off."""
    engine = make_engine(sqlite_url(tmp_path), enforce_foreign_keys=False)
    try:
        assert read_pragma(engine, "foreign_keys") == 0
    finally:
        engine.dispose()


# --- UTCDateTime column ---


@pytest.fixture
def migrated_engine(tmp_path: Path) -> Iterator[Engine]:
    """An engine on a fully migrated throwaway database."""
    database_url = sqlite_url(tmp_path)
    upgrade_to_head(database_url)
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


def test_datetimes_round_trip_as_utc(migrated_engine: Engine) -> None:
    """A non-UTC aware time is stored and comes back as the same instant in UTC."""
    session_factory = make_session_factory(migrated_engine)
    eastern_time = timezone(timedelta(hours=-4))
    created_in_eastern = datetime(2026, 10, 21, 18, 30, tzinfo=eastern_time)

    # Save with one session...
    with session_factory() as session:
        household = Household(name="Test kitchen", created_at=created_in_eastern)
        session.add(household)
        session.commit()
        household_id = household.id

    # ...and read back with a fresh one, so the value really comes from the DB.
    with session_factory() as session:
        reloaded = session.get(Household, household_id)
        assert reloaded.created_at == created_in_eastern  # same instant
        assert reloaded.created_at.tzinfo == UTC
        assert reloaded.created_at.hour == 22  # 18:30 EDT = 22:30 UTC


def test_created_at_defaults_to_now(migrated_engine: Engine) -> None:
    """created_at is filled in automatically when not given."""
    session_factory = make_session_factory(migrated_engine)
    before_insert = datetime.now(UTC)

    with session_factory() as session:
        household = Household(name="Test kitchen")
        session.add(household)
        session.commit()

        assert before_insert <= household.created_at <= datetime.now(UTC)


def test_naive_datetime_is_rejected(migrated_engine: Engine) -> None:
    """A datetime without a timezone can't be saved (its zone is ambiguous)."""
    session_factory = make_session_factory(migrated_engine)

    with session_factory() as session:
        session.add(Household(name="Test kitchen", created_at=datetime(2026, 10, 21, 18, 30)))
        with pytest.raises(StatementError, match="Naive datetime"):
            session.commit()
