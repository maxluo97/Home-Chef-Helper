"""Dependencies for web pages.

Like app/api/deps.py, but instead of answering 401/409 they send the
browser where it needs to go: the login page, or the household picker.
"""

from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response

from app.api.deps import SESSION_COOKIE_NAME, DbSession
from app.models import Household, LoginSession
from app.services import accounts
from app.web.templating import is_htmx


class LoginRequired(Exception):
    """Raised by page dependencies when nobody is logged in."""


class HouseholdRequired(Exception):
    """Raised when the page needs an active household and there is none."""


def web_login_session(request: Request, db: DbSession) -> LoginSession:
    """The caller's login session, or redirect to the login page."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    login_session = accounts.find_login_session(db, session_token) if session_token else None
    if login_session is None:
        raise LoginRequired
    return login_session


WebLoginSession = Annotated[LoginSession, Depends(web_login_session)]


def web_active_household(login_session: WebLoginSession, db: DbSession) -> Household:
    """The household this device is in, or redirect to the household picker."""
    household_id = login_session.active_household_id
    if household_id is None or not accounts.is_member(db, login_session.user_id, household_id):
        raise HouseholdRequired
    return db.get(Household, household_id)


WebActiveHousehold = Annotated[Household, Depends(web_active_household)]


def safe_next_path(next_path: str | None, default: str = "/") -> str:
    """Only allow redirects within this site (blocks "//evil.example")."""
    if next_path and next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return default


def redirect(request: Request, location: str) -> Response:
    """Redirect that also works for htmx requests (which need HX-Redirect)."""
    if is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": location})
    # 303 makes the browser follow with GET, the right thing after a POST.
    return RedirectResponse(location, status_code=303)


def install_redirect_handlers(application: FastAPI) -> None:
    """Turn LoginRequired / HouseholdRequired into redirects."""

    @application.exception_handler(LoginRequired)
    async def send_to_login(request: Request, _error: LoginRequired) -> Response:
        return_path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return redirect(request, f"/login?next={quote(return_path)}")

    @application.exception_handler(HouseholdRequired)
    async def send_to_household_picker(request: Request, _error: HouseholdRequired) -> Response:
        return redirect(request, "/households")
