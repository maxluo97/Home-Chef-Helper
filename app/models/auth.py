"""Login sessions: who is logged in on which device.

The browser holds a random token in a cookie; the database holds only that
token's hash. Logging out (or a password change, later) deletes the row,
which ends the session immediately.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, utc_now


class LoginSession(Base):
    """One logged-in browser/device.

    Named LoginSession to avoid confusion with SQLAlchemy's database Session.
    """

    __tablename__ = "login_sessions"

    # SHA-256 hex of the cookie token (see app/core/security.py).
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Household this device is currently looking at. Per session, so a phone
    # and a laptop can show different households. Cleared if the household
    # is deleted.
    active_household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
