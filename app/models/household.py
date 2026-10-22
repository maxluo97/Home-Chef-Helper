"""Households, users, and who belongs to which household.

A user can belong to several households (plan §9.4); membership is the
link table between them. Every household-owned table added later carries a
`household_id`, and queries are scoped to the user's active household.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, utc_now

# Maximum length for display names (households and people).
NAME_MAX_LENGTH = 100

# Stored usernames are 3-32 chars (see app/core/security.py).
USERNAME_MAX_LENGTH = 32

# Join codes are stored as "XXXX-XXXX".
JOIN_CODE_STORED_LENGTH = 9

# Mealie IDs are UUIDs (36 characters); a little headroom.
MEALIE_ID_MAX_LENGTH = 64


class Household(Base):
    """A group of people sharing one kitchen, inventory, and Mealie household."""

    __tablename__ = "households"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    # Code others type to join; unique so it identifies one household.
    join_code: Mapped[str] = mapped_column(String(JOIN_CODE_STORED_LENGTH), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    # --- Link to Mealie (all empty until a member connects a token) ---
    # The household's Mealie API token, encrypted with CHEF_SECRET_KEY
    # (app/core/crypto.py). Never stored or logged in plain text.
    mealie_token_encrypted: Mapped[str | None] = mapped_column(Text)
    # Who/where the token points, recorded when it was checked, so the UI can
    # show "connected as Test (Family)" without calling Mealie.
    mealie_username: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH))
    mealie_household_id: Mapped[str | None] = mapped_column(String(MEALIE_ID_MAX_LENGTH))
    mealie_household_name: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH))
    mealie_group_id: Mapped[str | None] = mapped_column(String(MEALIE_ID_MAX_LENGTH))
    # Group's short name, used to build "Open in Mealie" links. None if
    # Mealie didn't provide it (links are then hidden).
    mealie_group_slug: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH))
    mealie_connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # When recipe sync last finished successfully (None = never).
    recipes_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class User(Base):
    """One person using Chef Helper (plan §3 `users`)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Login name, stored lower-case so it's case-insensitive.
    username: Mapped[str] = mapped_column(String(USERNAME_MAX_LENGTH), unique=True)
    # Argon2 hash; the password itself is never stored.
    password_hash: Mapped[str] = mapped_column(String(255))
    # Friendly name shown in the UI ("Max"), free-form.
    display_name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class HouseholdMembership(Base):
    """Links one user to one household. A user may have several rows."""

    __tablename__ = "household_memberships"

    # The pair is the primary key, so a user can't join the same household twice.
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
