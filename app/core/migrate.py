"""Run Alembic migrations from inside the app (plan §7.3: migrate on boot).

The same migrations can be run by hand with the Alembic CLI
(`uv run alembic upgrade head`); both paths go through migrations/env.py.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config

# migrations/ lives at the repo root, two levels above this file (app/core/).
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def make_alembic_config(database_url: str) -> Config:
    """Build an Alembic config in code, pointed at `database_url`.

    Doesn't read alembic.ini, so it works from any working directory and
    doesn't let the .ini's logging setup reconfigure the app's loggers.
    """
    alembic_config = Config()
    # Alembic treats "%" in option values as interpolation; escape it.
    alembic_config.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    # env.py reads the URL from here (see migrations/env.py).
    alembic_config.attributes["database_url"] = database_url
    return alembic_config


def upgrade_to_head(database_url: str) -> None:
    """Apply every migration not yet applied. A no-op if already current."""
    command.upgrade(make_alembic_config(database_url), "head")


def downgrade_to(database_url: str, revision: str) -> None:
    """Roll back to `revision` ("base" = empty database). Used by tests."""
    command.downgrade(make_alembic_config(database_url), revision)
