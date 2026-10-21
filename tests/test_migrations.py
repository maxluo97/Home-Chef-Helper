"""Tests that the Alembic migrations build, match, and unwind the schema."""

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.core.db import make_engine
from app.core.migrate import downgrade_to, upgrade_to_head
from app.models import Base

# Every table the migrations should create (besides Alembic's own bookkeeping).
EXPECTED_TABLES = {"households", "users"}


def sqlite_url(tmp_path: Path) -> str:
    """URL of a throwaway SQLite file in this test's temp folder."""
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


def table_names(database_url: str) -> set[str]:
    """Names of all tables currently in the database."""
    engine = make_engine(database_url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_creates_all_tables(tmp_path: Path) -> None:
    """Migrating an empty database creates every expected table."""
    database_url = sqlite_url(tmp_path)

    upgrade_to_head(database_url)

    assert table_names(database_url) == EXPECTED_TABLES | {"alembic_version"}


def test_upgrade_twice_is_harmless(tmp_path: Path) -> None:
    """Restarting the app (a second upgrade) changes nothing and doesn't fail."""
    database_url = sqlite_url(tmp_path)

    upgrade_to_head(database_url)
    upgrade_to_head(database_url)

    assert table_names(database_url) == EXPECTED_TABLES | {"alembic_version"}


def test_downgrade_to_base_removes_all_tables(tmp_path: Path) -> None:
    """Every migration can be undone, back to an empty database."""
    database_url = sqlite_url(tmp_path)
    upgrade_to_head(database_url)

    downgrade_to(database_url, "base")

    assert table_names(database_url) == {"alembic_version"}


def test_models_match_migrations(tmp_path: Path) -> None:
    """The models in app/models/ and the migrated schema agree exactly.

    Fails if someone changes a model but forgets to write a migration (or
    the reverse). Fix by generating one: see migrations/README.
    """
    database_url = sqlite_url(tmp_path)
    upgrade_to_head(database_url)

    engine = make_engine(database_url)
    try:
        with engine.connect() as connection:
            migration_context = MigrationContext.configure(connection)
            schema_differences = compare_metadata(migration_context, Base.metadata)
    finally:
        engine.dispose()

    assert schema_differences == []
