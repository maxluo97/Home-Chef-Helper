"""Alembic environment: connects Alembic to Chef Helper's models and database.

Runs for both entry points:
- the app at startup (app/core/migrate.py), which passes the database URL in
  `config.attributes["database_url"]`;
- the Alembic CLI (`uv run alembic ...`), which falls back to the app's
  Settings (env vars / .env), exactly like the running app.
"""

from logging.config import fileConfig
from typing import Any

from alembic import context
from alembic.autogenerate.api import AutogenContext

from app.core.config import get_settings
from app.core.db import make_engine
from app.models import Base
from app.models.base import UTCDateTime

config = context.config

# CLI runs read alembic.ini, which defines log output ("Running upgrade ...").
# The app builds its config in code with no .ini, so this is skipped there
# and the app's own logging is left alone.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Metadata of every model; lets `alembic revision --autogenerate` diff the
# models against the database.
target_metadata = Base.metadata


def resolve_database_url() -> str:
    """URL handed in by the app if present, else the app's configured one."""
    return config.attributes.get("database_url") or get_settings().database_url


def render_item(type_name: str, obj: Any, autogen_context: AutogenContext) -> str | bool:
    """Write our custom UTCDateTime as plain sa.DateTime() in migrations.

    Migration files should not import app code (the app changes; old
    migrations must keep working). The UTC handling is purely Python-side,
    so the stored column type is an ordinary DateTime.
    """
    if type_name == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime()"
    # False = let Alembic render everything else its default way.
    return False


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (`alembic upgrade --sql`)."""
    context.configure(
        url=resolve_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and apply migrations."""
    # Foreign keys off: SQLite table rebuilds during migrations would
    # otherwise trip them (see make_engine).
    engine = make_engine(resolve_database_url(), enforce_foreign_keys=False)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                # SQLite can't ALTER most things in place; "batch" mode makes
                # Alembic copy-and-rebuild the table instead.
                render_as_batch=True,
                render_item=render_item,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
