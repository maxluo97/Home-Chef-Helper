"""Sign-up, login sessions, households, and membership (plan §9.4).

Functions take an open database session and commit their own changes.
Expected failures (taken username, bad join code, ...) raise the exceptions
below; the API layer turns them into HTTP errors.
"""

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import (
    TIMING_EQUALIZER_HASH,
    generate_join_code,
    hash_password,
    hash_session_token,
    new_session_token,
    normalize_join_code,
    password_needs_rehash,
    verify_password,
)
from app.models import Household, HouseholdMembership, LoginSession, User
from app.models.base import utc_now

# Attempts at drawing an unused join code before giving up. With ~5×10^11
# possible codes a repeat is astronomically unlikely; this just bounds a loop.
JOIN_CODE_MAX_ATTEMPTS = 10


# --- Errors ---


class AccountError(Exception):
    """Base class for expected, user-facing account errors."""


class UsernameTakenError(AccountError):
    """Sign-up with a username that already exists."""


class InvalidJoinCodeError(AccountError):
    """No household has this join code."""


class NotAMemberError(AccountError):
    """The user tried to act on a household they don't belong to."""


# --- Users ---


def create_user(db: Session, username: str, password: str, display_name: str) -> User:
    """Create an account. `username` must already be normalised.

    Raises UsernameTakenError if the name is in use.
    """
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name.strip(),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as integrity_error:
        # The UNIQUE constraint on users.username caught a duplicate.
        db.rollback()
        raise UsernameTakenError(username) from integrity_error
    return user


def authenticate(db: Session, username: str, password: str) -> User | None:
    """Return the user if the username/password pair is right, else None."""
    user = db.scalars(select(User).where(User.username == username)).one_or_none()

    if user is None:
        # Spend the same time as a real check so timing doesn't reveal
        # whether the username exists.
        verify_password(TIMING_EQUALIZER_HASH, password)
        return None
    if not verify_password(user.password_hash, password):
        return None

    # Quietly upgrade old hashes if the hashing settings were strengthened.
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.commit()
    return user


# --- Login sessions ---


def start_login_session(db: Session, user: User, lifetime: timedelta) -> str:
    """Log `user` in on a new device; return the raw token for the cookie.

    The device starts on the user's earliest-joined household, if any.
    """
    session_token = new_session_token()
    login_session = LoginSession(
        token_hash=hash_session_token(session_token),
        user_id=user.id,
        active_household_id=first_household_id(db, user.id),
        expires_at=utc_now() + lifetime,
    )
    db.add(login_session)
    db.commit()
    return session_token


def find_login_session(db: Session, session_token: str) -> LoginSession | None:
    """Look up a live session by its cookie token.

    Expired sessions are deleted on sight and reported as missing.
    """
    login_session = db.get(LoginSession, hash_session_token(session_token))
    if login_session is None:
        return None
    if login_session.expires_at <= utc_now():
        db.delete(login_session)
        db.commit()
        return None
    return login_session


def end_login_session(db: Session, session_token: str) -> None:
    """Log out: delete the session row. Unknown tokens are ignored."""
    db.execute(
        delete(LoginSession).where(LoginSession.token_hash == hash_session_token(session_token))
    )
    db.commit()


# --- Households and membership ---


def first_household_id(db: Session, user_id: int) -> int | None:
    """ID of the household the user joined first, or None if they have none."""
    return db.scalars(
        select(HouseholdMembership.household_id)
        .where(HouseholdMembership.user_id == user_id)
        .order_by(HouseholdMembership.joined_at, HouseholdMembership.household_id)
        .limit(1)
    ).one_or_none()


def is_member(db: Session, user_id: int, household_id: int) -> bool:
    """True if the user belongs to the household."""
    return db.get(HouseholdMembership, (household_id, user_id)) is not None


def list_households(db: Session, user_id: int) -> list[Household]:
    """The user's households, in the order they joined them."""
    return list(
        db.scalars(
            select(Household)
            .join(HouseholdMembership, HouseholdMembership.household_id == Household.id)
            .where(HouseholdMembership.user_id == user_id)
            .order_by(HouseholdMembership.joined_at, Household.id)
        )
    )


def list_members(db: Session, household_id: int) -> list[User]:
    """People in the household, in the order they joined."""
    return list(
        db.scalars(
            select(User)
            .join(HouseholdMembership, HouseholdMembership.user_id == User.id)
            .where(HouseholdMembership.household_id == household_id)
            .order_by(HouseholdMembership.joined_at, User.id)
        )
    )


def unused_join_code(db: Session) -> str:
    """Draw random join codes until one isn't taken by another household."""
    for _iAttempt in range(JOIN_CODE_MAX_ATTEMPTS):
        candidate_code = generate_join_code()
        code_in_use = db.scalars(
            select(Household.id).where(Household.join_code == candidate_code)
        ).first()
        if code_in_use is None:
            return candidate_code
    raise RuntimeError("Could not generate an unused join code.")


def create_household(db: Session, login_session: LoginSession, name: str) -> Household:
    """Create a household, make the creator a member, and switch to it."""
    household = Household(name=name.strip(), join_code=unused_join_code(db))
    db.add(household)
    db.flush()  # assigns household.id without committing yet

    # Creator joins, and this device switches to the new household.
    db.add(HouseholdMembership(household_id=household.id, user_id=login_session.user_id))
    login_session.active_household_id = household.id
    db.commit()
    return household


def join_household(db: Session, login_session: LoginSession, raw_join_code: str) -> Household:
    """Join the household with this code and switch to it.

    Joining a household you're already in just switches to it.
    Raises InvalidJoinCodeError if no household has the code.
    """
    household = db.scalars(
        select(Household).where(Household.join_code == normalize_join_code(raw_join_code))
    ).one_or_none()
    if household is None:
        raise InvalidJoinCodeError(raw_join_code)

    if not is_member(db, login_session.user_id, household.id):
        db.add(HouseholdMembership(household_id=household.id, user_id=login_session.user_id))
    login_session.active_household_id = household.id
    db.commit()
    return household


def switch_household(db: Session, login_session: LoginSession, household_id: int) -> None:
    """Make another of the user's households the active one on this device.

    Raises NotAMemberError if the user doesn't belong to it (including when
    the household doesn't exist, so IDs of others' households aren't revealed).
    """
    if not is_member(db, login_session.user_id, household_id):
        raise NotAMemberError(household_id)
    login_session.active_household_id = household_id
    db.commit()


def regenerate_join_code(db: Session, user_id: int, household_id: int) -> Household:
    """Replace a household's join code (e.g. after sharing it too widely).

    The old code stops working immediately. Members only.
    """
    if not is_member(db, user_id, household_id):
        raise NotAMemberError(household_id)
    household = db.get(Household, household_id)
    household.join_code = unused_join_code(db)
    db.commit()
    return household
