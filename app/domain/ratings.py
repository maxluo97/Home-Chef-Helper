"""Rules for combining per-person ratings (plan §5.1 `R(r)` inputs).

Every rating is kept as history; for scoring, each person's latest rating
of a recipe is the one that counts (plan §3, §9.2).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RatingRecord:
    """The parts of a stored rating these rules need."""

    rating_id: int  # breaks ties when two ratings share a timestamp
    user_id: int
    stars: int
    created_at: datetime


def latest_rating_per_user(rating_records: Iterable[RatingRecord]) -> dict[int, RatingRecord]:
    """Each person's most recent rating, keyed by user ID.

    "Most recent" = latest created_at; if two are identical, the one saved
    later (higher rating_id) wins.
    """
    latest_by_user: dict[int, RatingRecord] = {}
    for rating_record in rating_records:
        current_latest = latest_by_user.get(rating_record.user_id)
        newer_key = (rating_record.created_at, rating_record.rating_id)
        if current_latest is None or newer_key > (current_latest.created_at, current_latest.rating_id):
            latest_by_user[rating_record.user_id] = rating_record
    return latest_by_user


def average_stars(latest_ratings: Iterable[RatingRecord]) -> float | None:
    """Mean of the given (latest) ratings, or None if nobody has rated."""
    star_values = [rating_record.stars for rating_record in latest_ratings]
    if not star_values:
        return None
    return sum(star_values) / len(star_values)
