"""Tests for latest-rating-wins and household averages."""

from datetime import UTC, datetime, timedelta

from app.domain.ratings import RatingRecord, average_stars, latest_rating_per_user

START_TIME = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
ALICE_ID = 1
BOB_ID = 2


def rating(rating_id: int, user_id: int, stars: int, days_later: int = 0) -> RatingRecord:
    """A rating made `days_later` days after START_TIME."""
    return RatingRecord(rating_id, user_id, stars, START_TIME + timedelta(days=days_later))


def test_latest_rating_wins_per_person() -> None:
    """Each person's newest rating counts, regardless of input order."""
    rating_records = [
        rating(3, ALICE_ID, 2, days_later=5),  # Alice changed her mind
        rating(1, ALICE_ID, 5, days_later=0),
        rating(2, BOB_ID, 4, days_later=1),
    ]

    latest = latest_rating_per_user(rating_records)

    assert {user_id: record.stars for user_id, record in latest.items()} == {ALICE_ID: 2, BOB_ID: 4}


def test_same_timestamp_later_save_wins() -> None:
    """Two ratings at the same instant: the one saved later wins."""
    latest = latest_rating_per_user([rating(8, ALICE_ID, 3), rating(7, ALICE_ID, 5)])

    assert latest[ALICE_ID].stars == 3


def test_no_ratings() -> None:
    """Nobody rated: no latest ratings and no average."""
    assert latest_rating_per_user([]) == {}
    assert average_stars([]) is None


def test_average_uses_only_latest_ratings() -> None:
    """Old ratings don't drag the average; each person counts once."""
    rating_records = [
        rating(1, ALICE_ID, 1, days_later=0),
        rating(2, ALICE_ID, 5, days_later=3),
        rating(3, BOB_ID, 4, days_later=1),
    ]

    latest = latest_rating_per_user(rating_records)

    assert average_stars(latest.values()) == 4.5
