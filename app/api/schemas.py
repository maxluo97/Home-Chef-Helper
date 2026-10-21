"""Request and response shapes shared by the account routes.

Pydantic validates incoming JSON against these and answers 422 with a
readable message when something is off.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.core.security import normalize_username

# A display name or household name: trimmed, 1-100 characters.
NameText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

# Password length limits. The upper bound stops absurdly long inputs from
# tying up the (deliberately slow) password hasher.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
PasswordText = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]


# --- Requests ---


class SignupRequest(BaseModel):
    """New account."""

    username: str
    password: PasswordText
    display_name: NameText

    @field_validator("username")
    @classmethod
    def check_username(cls, raw_username: str) -> str:
        """Trim, lower-case, and check the username's characters."""
        return normalize_username(raw_username)


class LoginRequest(BaseModel):
    """Login attempt. Not validated beyond being text: a malformed username
    is simply a failed login, not a 422 that hints at the rules."""

    username: str
    password: str


class CreateHouseholdRequest(BaseModel):
    """New household."""

    name: NameText


class JoinHouseholdRequest(BaseModel):
    """Join by code; case and spacing are forgiven."""

    join_code: Annotated[str, Field(min_length=1, max_length=20)]


# --- Responses ---


class UserOut(BaseModel):
    """Public view of a user (never includes the password hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str


class HouseholdOut(BaseModel):
    """A household as its members see it, join code included."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    join_code: str


class MeResponse(BaseModel):
    """Who is logged in, their households, and which one is active here."""

    user: UserOut
    households: list[HouseholdOut]
    active_household_id: int | None
