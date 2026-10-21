"""Shared route dependencies: database session, current login, active household.

Any route that touches household-owned data should depend on
`ActiveHouseholdId` and filter every query by it (plan §9.4).
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.models import LoginSession
from app.services import accounts

# Name of the cookie holding the login-session token.
SESSION_COOKIE_NAME = "chef_session"

# One database session per request. FastAPI reuses it for every dependency
# in the same request, so objects loaded here can be changed and committed
# in the route.
DbSession = Annotated[Session, Depends(get_session)]


def current_login_session(request: Request, db: DbSession) -> LoginSession:
    """The caller's live login session, or 401 if not logged in."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    login_session = accounts.find_login_session(db, session_token) if session_token else None
    if login_session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in.")
    return login_session


CurrentLoginSession = Annotated[LoginSession, Depends(current_login_session)]


def active_household_id(login_session: CurrentLoginSession, db: DbSession) -> int:
    """The household this device is working in.

    409 if none is selected yet (the user must create or join one first);
    403 if the user is no longer a member of it.
    """
    household_id = login_session.active_household_id
    if household_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No household selected. Create or join one first.")
    if not accounts.is_member(db, login_session.user_id, household_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not a member of this household.")
    return household_id


ActiveHouseholdId = Annotated[int, Depends(active_household_id)]
