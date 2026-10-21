"""Connect a household to Mealie: check, encrypt, and store its API token.

Each Chef Helper household stores its own Mealie token (plan §9.8),
encrypted with the installation key. Other code gets a ready-to-use client
for a household through `open_mealie_client`, never the raw token.
"""

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import SecretBox, SecretDecryptionError
from app.integrations.mealie_client import MealieAuthError, MealieClient
from app.models import Household
from app.models.base import utc_now
from app.services.accounts import NotAMemberError, is_member

# Builds a Mealie client for a given API token. Production uses the real
# Mealie URL; tests swap in one backed by a fake Mealie.
MealieClientFactory = Callable[[str], MealieClient]


# --- Errors ---


class MealieLinkError(Exception):
    """Base class for household <-> Mealie link problems."""


class MealieNotConfiguredError(MealieLinkError):
    """The server has no CHEF_SECRET_KEY, so tokens can't be stored."""


class MealieTokenRejectedError(MealieLinkError):
    """Mealie said the token is invalid (when connecting, or later)."""


class MealieNotConnectedError(MealieLinkError):
    """The household has no usable Mealie token (never set, or unreadable)."""


# --- Helpers ---


def secret_box_for(settings: Settings) -> SecretBox:
    """The installation's encrypter, or MealieNotConfiguredError if no key."""
    if settings.chef_secret_key is None:
        raise MealieNotConfiguredError("CHEF_SECRET_KEY is not set on the server.")
    return SecretBox(settings.chef_secret_key.get_secret_value())


def household_for_member(db: Session, user_id: int, household_id: int) -> Household:
    """Load a household the user belongs to, else NotAMemberError."""
    if not is_member(db, user_id, household_id):
        raise NotAMemberError(household_id)
    return db.get(Household, household_id)


# --- Connect / disconnect ---


def connect_mealie(
    db: Session,
    settings: Settings,
    make_client: MealieClientFactory,
    user_id: int,
    household_id: int,
    api_token: str,
) -> Household:
    """Check a Mealie token with Mealie, then store it encrypted.

    Replaces any previous token. Raises NotAMemberError,
    MealieNotConfiguredError, MealieTokenRejectedError, or
    MealieUnavailableError (Mealie down; nothing is saved).
    """
    household = household_for_member(db, user_id, household_id)
    secret_box = secret_box_for(settings)  # fail early if the server can't store it

    # Ask Mealie who this token belongs to; proves it works.
    try:
        with make_client(api_token) as mealie:
            mealie_user = mealie.get_current_user()
    except MealieAuthError as auth_error:
        raise MealieTokenRejectedError("Mealie rejected this token.") from auth_error

    # Store the encrypted token plus a readable description of the link.
    household.mealie_token_encrypted = secret_box.encrypt(api_token)
    household.mealie_username = mealie_user.username
    household.mealie_household_id = mealie_user.household_id
    household.mealie_household_name = mealie_user.household
    household.mealie_group_id = mealie_user.group_id
    household.mealie_connected_at = utc_now()
    db.commit()
    return household


def disconnect_mealie(db: Session, user_id: int, household_id: int) -> None:
    """Forget the household's Mealie token and link details."""
    household = household_for_member(db, user_id, household_id)
    household.mealie_token_encrypted = None
    household.mealie_username = None
    household.mealie_household_id = None
    household.mealie_household_name = None
    household.mealie_group_id = None
    household.mealie_connected_at = None
    db.commit()


# --- Use ---


def open_mealie_client(
    db: Session, settings: Settings, make_client: MealieClientFactory, household_id: int
) -> MealieClient:
    """A Mealie client acting with this household's token.

    The caller must already have checked membership (e.g. via the
    ActiveHouseholdId dependency) and should use the result in a `with`.
    Raises MealieNotConfiguredError or MealieNotConnectedError.
    """
    household = db.get(Household, household_id)
    if household is None or household.mealie_token_encrypted is None:
        raise MealieNotConnectedError("This household isn't connected to Mealie yet.")

    try:
        api_token = secret_box_for(settings).decrypt(household.mealie_token_encrypted)
    except SecretDecryptionError as decryption_error:
        # The server key changed since the token was saved (plan §9.8: the
        # household just reconnects).
        raise MealieNotConnectedError(
            "The saved Mealie token can't be read (the server key changed). Please reconnect."
        ) from decryption_error
    return make_client(api_token)
