"""Cook log and per-person ratings (plan §3 `cook_events`, `ratings`).

Both are household-owned: every row carries `household_id`, and every query
filters by the caller's active household (plan §9.4).
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, utc_now

# Ratings are whole stars from 1 to 5 (plan §9.2).
MIN_STARS = 1
MAX_STARS = 5


class CookEvent(Base):
    """One time a recipe was cooked."""

    __tablename__ = "cook_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), index=True)

    # When it was cooked (may be back-dated) and by whom. The cook is kept
    # as NULL if their account is ever deleted, so the history survives.
    cooked_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    cooked_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    servings: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)

    # Who entered it, and when (can differ from the cook).
    logged_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class Rating(Base):
    """One person's star rating of a recipe at one point in time.

    Ratings are never overwritten: each new rating is a new row, and the
    person's latest row is the one that counts (plan §3).
    """

    __tablename__ = "ratings"
    __table_args__ = (CheckConstraint(f"stars BETWEEN {MIN_STARS} AND {MAX_STARS}", name="stars_range"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    stars: Mapped[int] = mapped_column(Integer)
    # Set when the rating was given while logging a cook. Deleting that cook
    # keeps the rating (the opinion still stands).
    cook_event_id: Mapped[int | None] = mapped_column(ForeignKey("cook_events.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
