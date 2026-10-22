"""Jinja template setup and the shared context every page receives."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.core.config import Settings
from app.models import Household, LoginSession, User
from app.models.base import utc_now
from app.services import accounts
from app.web import formatting

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# Jinja2Templates turns on HTML auto-escaping for .html files, so text from
# users or Mealie can never inject markup into pages.
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# --- Filters usable in templates, e.g. {{ cook.cooked_at | local_datetime(tz) }} ---
templates.env.filters["local_datetime"] = formatting.local_datetime
templates.env.filters["local_date"] = formatting.local_date
templates.env.filters["stars"] = formatting.star_text
templates.env.filters["relative_time"] = formatting.relative_time
templates.env.filters["datetime_local_value"] = formatting.datetime_local_value


def is_htmx(request: Request) -> bool:
    """True if htmx sent this request (it wants a page fragment, not a page)."""
    return request.headers.get("HX-Request") == "true"


def page_context(
    request: Request,
    db: Any,
    settings: Settings,
    login_session: LoginSession | None,
    **page_values: Any,
) -> dict[str, Any]:
    """Values every page template can use (user, households, timezone...)."""
    user = db.get(User, login_session.user_id) if login_session else None
    my_households = accounts.list_households(db, login_session.user_id) if login_session else []
    active_household = (
        db.get(Household, login_session.active_household_id)
        if login_session and login_session.active_household_id
        else None
    )
    return {
        "request": request,
        "user": user,
        "my_households": my_households,
        "active_household": active_household,
        "tz": settings.timezone,
        "now": utc_now(),
        "mealie_public_url": settings.mealie_public_url,
        **page_values,
    }
