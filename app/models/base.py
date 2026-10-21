"""Shared base class and column types for all ORM models."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

# Predictable constraint names (e.g. "fk_users_household_id_households").
# Alembic needs named constraints to alter or drop them later, which matters
# on SQLite where altering a table means rebuilding it.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    # column_0_N_name joins every column, so multi-column rules get full names
    # ("uq_recipes_household_id_mealie_recipe_id").
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Parent class of every model; holds the shared table metadata."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """A datetime column that always stores and returns UTC.

    SQLite has no timezone-aware datetime type, so values are stored as
    naive UTC and re-tagged as UTC on the way out. Naive datetimes are
    rejected on the way in, because their timezone would be a guess.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Python -> database: convert to UTC, then drop the tzinfo."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime given; use a timezone-aware one (e.g. utc_now()).")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Database -> Python: mark the stored naive value as UTC."""
        if value is None:
            return None
        return value.replace(tzinfo=UTC)
