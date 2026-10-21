"""Database engine and session plumbing.

The app talks to the database through SQLAlchemy, so SQLite (the default) and
Postgres (later, if ever) differ only in DATABASE_URL. SQLite-specific tuning
is applied automatically when the URL is a SQLite one.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

# How long (ms) a SQLite connection waits for another writer to finish before
# failing with "database is locked". Generous; writes here are tiny.
SQLITE_BUSY_TIMEOUT_MS = 5000


def make_engine(database_url: str, enforce_foreign_keys: bool = True) -> Engine:
    """Create a SQLAlchemy engine, with SQLite tuning when applicable.

    `enforce_foreign_keys=False` exists for migrations only: Alembic rebuilds
    SQLite tables to alter them, which trips foreign-key checks mid-rebuild.
    """
    engine = create_engine(database_url)

    # --- SQLite per-connection settings ---
    if engine.dialect.name == "sqlite":
        # SQLite creates the database file on first connect, but not the
        # folder it lives in. Create it here so every entry point (app,
        # Alembic CLI, tests) works on a fresh clone. Skipped for in-memory
        # databases, which have no file.
        sqlite_file = engine.url.database
        if sqlite_file and sqlite_file != ":memory:":
            Path(sqlite_file).parent.mkdir(parents=True, exist_ok=True)

        @event.listens_for(engine, "connect")
        def configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any) -> None:
            """Run on every new SQLite connection, before SQLAlchemy uses it."""
            cursor = dbapi_connection.cursor()
            # WAL lets readers proceed while one writer writes (plan §2.4).
            # It is stored in the DB file, so repeating it is harmless.
            cursor.execute("PRAGMA journal_mode=WAL")
            # SQLite ignores FOREIGN KEY constraints unless told otherwise,
            # and the setting is per connection.
            foreign_keys_flag = "ON" if enforce_foreign_keys else "OFF"
            cursor.execute(f"PRAGMA foreign_keys={foreign_keys_flag}")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a factory that opens ORM sessions bound to `engine`."""
    # expire_on_commit=False keeps loaded objects readable after commit, so
    # a route can commit and then render what it just saved.
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request, closed afterwards.

    The factory is created at startup (see main.py) and kept on app.state.
    """
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as session:
        yield session
