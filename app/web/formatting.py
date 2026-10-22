"""Turn stored values into text for pages (and form inputs back into values).

Times are stored in UTC and shown in the installation's timezone (TZ).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.cooking import MAX_STARS

# Format for <input type="datetime-local"> values: "2026-10-21T19:30".
DATETIME_LOCAL_FORMAT = "%Y-%m-%dT%H:%M"


def local_datetime(moment: datetime | None, tz: ZoneInfo) -> str:
    """'Tue Oct 20, 7:30 PM' in the display timezone ('' for None)."""
    if moment is None:
        return ""
    local_moment = moment.astimezone(tz)
    hour_12 = local_moment.hour % 12 or 12
    return f"{local_moment:%a %b} {local_moment.day}, {hour_12}:{local_moment:%M %p}"


def local_date(moment: datetime | None, tz: ZoneInfo) -> str:
    """'Tue Oct 20' in the display timezone ('' for None)."""
    if moment is None:
        return ""
    local_moment = moment.astimezone(tz)
    return f"{local_moment:%a %b} {local_moment.day}"


def relative_time(moment: datetime | None, now: datetime) -> str:
    """Rough age of a past time: 'just now', '3 hours ago', '2 wks ago'..."""
    if moment is None:
        return "never"
    age = now - moment
    if age < timedelta(minutes=1):
        return "just now"

    # Pick the largest unit that fits, rounding down.
    age_seconds = age.total_seconds()
    for unit_name, unit_seconds in (
        ("yr", 365 * 86400),
        ("mo", 30 * 86400),
        ("wk", 7 * 86400),
        ("day", 86400),
        ("hour", 3600),
        ("min", 60),
    ):
        nUnits = int(age_seconds // unit_seconds)
        if nUnits >= 1:
            plural_suffix = "" if nUnits == 1 else "s"
            return f"{nUnits} {unit_name}{plural_suffix} ago"
    return "just now"


def star_text(stars: float | None) -> str:
    """'★★★★☆' for 4 (averages round to the nearest whole star); '' for None."""
    if stars is None:
        return ""
    nFilled = max(0, min(MAX_STARS, round(stars)))
    return "★" * nFilled + "☆" * (MAX_STARS - nFilled)


def datetime_local_value(moment: datetime, tz: ZoneInfo) -> str:
    """Value for a datetime-local input, in the display timezone."""
    return moment.astimezone(tz).strftime(DATETIME_LOCAL_FORMAT)


def parse_datetime_local(text: str, tz: ZoneInfo) -> datetime:
    """Read a datetime-local input ('2026-10-21T19:30') as display-timezone time.

    Raises ValueError for malformed text.
    """
    return datetime.strptime(text.strip(), DATETIME_LOCAL_FORMAT).replace(tzinfo=tz)
