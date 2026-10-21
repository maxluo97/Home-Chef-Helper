"""Households and users: the two tables everything else hangs off.

Deliberately minimal for now. Login fields, household membership, the join
code, and the encrypted Mealie token (plan §9.4, §9.8) arrive with the
accounts block, as a new migration.
"""

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, utc_now

# Maximum length for display names (households and people).
NAME_MAX_LENGTH = 100


class Household(Base):
    """A group of people sharing one kitchen, inventory, and Mealie household."""

    __tablename__ = "households"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class User(Base):
    """One person using Chef Helper (plan §3 `users`)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
