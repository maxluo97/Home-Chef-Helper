"""Tests for page formatting helpers."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.web.formatting import (
    datetime_local_value,
    local_date,
    local_datetime,
    parse_datetime_local,
    relative_time,
    star_text,
)

EASTERN = ZoneInfo("America/New_York")
# 23:30 UTC on Oct 20 = 7:30 PM Eastern (EDT, UTC-4).
EVENING_UTC = datetime(2026, 10, 20, 23, 30, tzinfo=UTC)


def test_local_datetime_and_date_use_display_timezone() -> None:
    """UTC values are shown in the installation's timezone."""
    assert local_datetime(EVENING_UTC, EASTERN) == "Tue Oct 20, 7:30 PM"
    assert local_date(EVENING_UTC, EASTERN) == "Tue Oct 20"
    assert local_datetime(None, EASTERN) == ""


def test_midnight_and_noon_show_as_12() -> None:
    """12-hour clock edge cases."""
    assert local_datetime(datetime(2026, 10, 21, 4, 5, tzinfo=UTC), EASTERN) == "Wed Oct 21, 12:05 AM"
    assert local_datetime(datetime(2026, 10, 21, 16, 0, tzinfo=UTC), EASTERN) == "Wed Oct 21, 12:00 PM"


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(seconds=20), "just now"),
        (timedelta(minutes=5), "5 mins ago"),
        (timedelta(hours=1, minutes=59), "1 hour ago"),
        (timedelta(days=2, hours=3), "2 days ago"),
        (timedelta(days=15), "2 wks ago"),
        (timedelta(days=65), "2 mos ago"),
        (timedelta(days=800), "2 yrs ago"),
    ],
)
def test_relative_time(age: timedelta, expected: str) -> None:
    """Ages round down to the largest fitting unit."""
    assert relative_time(EVENING_UTC - age, EVENING_UTC) == expected


def test_relative_time_never() -> None:
    """No time at all reads as 'never'."""
    assert relative_time(None, EVENING_UTC) == "never"


@pytest.mark.parametrize(("stars", "expected"), [(4, "★★★★☆"), (4.5, "★★★★☆"), (4.6, "★★★★★"), (1, "★☆☆☆☆"),
                                                 (None, "")])
def test_star_text(stars: float | None, expected: str) -> None:
    """Whole stars; averages round to the nearest star (4.5 rounds to even)."""
    assert star_text(stars) == expected


def test_datetime_local_round_trip() -> None:
    """Form input values convert to and from the display timezone."""
    assert datetime_local_value(EVENING_UTC, EASTERN) == "2026-10-20T19:30"
    assert parse_datetime_local("2026-10-20T19:30", EASTERN) == EVENING_UTC


def test_bad_datetime_local_raises() -> None:
    """Malformed input is a ValueError the page can report."""
    with pytest.raises(ValueError):
        parse_datetime_local("yesterday-ish", EASTERN)
