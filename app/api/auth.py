"""Sign-up, login, logout, and "who am I" (JSON API).

The HTML pages (block 7) will reuse app/services/accounts.py; these JSON
routes are the API and the test surface.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.deps import SESSION_COOKIE_NAME, CurrentLoginSession, DbSession
from app.api.schemas import HouseholdOut, LoginRequest, MeResponse, SignupRequest, UserOut
from app.core.config import Settings, get_settings
from app.core.security import normalize_username
from app.models import LoginSession, User
from app.services import accounts

router = APIRouter(prefix="/api", tags=["auth"])

AppSettings = Annotated[Settings, Depends(get_settings)]


def set_session_cookie(response: Response, session_token: str, settings: Settings) -> None:
    """Attach the login cookie to the response."""
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=int(timedelta(days=settings.session_lifetime_days).total_seconds()),
        httponly=True,  # page scripts can't read it, which blunts XSS token theft
        samesite="lax",  # not sent on cross-site form posts (basic CSRF guard)
        secure=settings.cookie_secure,
        path="/",
    )


def build_me_response(db: DbSession, user: User, login_session: LoginSession) -> MeResponse:
    """Assemble the "who am I" payload."""
    households = accounts.list_households(db, user.id)
    return MeResponse(
        user=UserOut.model_validate(user),
        households=[HouseholdOut.model_validate(household) for household in households],
        active_household_id=login_session.active_household_id,
    )


@router.post("/auth/signup", status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, response: Response, db: DbSession, settings: AppSettings) -> MeResponse:
    """Create an account and log in on this device."""
    try:
        user = accounts.create_user(db, payload.username, payload.password, payload.display_name)
    except accounts.UsernameTakenError:
        raise HTTPException(status.HTTP_409_CONFLICT, "That username is taken.") from None

    # Log the new user straight in.
    session_token = accounts.start_login_session(db, user, timedelta(days=settings.session_lifetime_days))
    set_session_cookie(response, session_token, settings)
    login_session = accounts.find_login_session(db, session_token)
    return build_me_response(db, user, login_session)


@router.post("/auth/login")
def login(payload: LoginRequest, response: Response, db: DbSession, settings: AppSettings) -> MeResponse:
    """Check username and password; on success, set the login cookie."""
    # One vague message for every failure, so it doesn't reveal which
    # usernames exist.
    invalid_login = HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password.")
    try:
        username = normalize_username(payload.username)
    except ValueError:
        raise invalid_login from None

    user = accounts.authenticate(db, username, payload.password)
    if user is None:
        raise invalid_login

    session_token = accounts.start_login_session(db, user, timedelta(days=settings.session_lifetime_days))
    set_session_cookie(response, session_token, settings)
    login_session = accounts.find_login_session(db, session_token)
    return build_me_response(db, user, login_session)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: DbSession) -> None:
    """End this device's session. Safe to call when not logged in."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        accounts.end_login_session(db, session_token)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.get("/me")
def me(login_session: CurrentLoginSession, db: DbSession) -> MeResponse:
    """The logged-in user, their households, and the active one."""
    user = db.get(User, login_session.user_id)
    return build_me_response(db, user, login_session)
