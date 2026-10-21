"""Household <-> Mealie connection, and a live view of the household's recipes.

The live recipe list is a direct pass-through to Mealie, useful for checking
a connection. The app's real recipe screens will read the local cache that
recipe sync (block 5) fills.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import ActiveHouseholdId, AppSettings, CurrentLoginSession, DbSession
from app.integrations.mealie_client import MealieAuthError, MealieClient, MealieUnavailableError
from app.models import Household
from app.services import mealie_link
from app.services.accounts import NotAMemberError

router = APIRouter(tags=["mealie"])

NOT_A_MEMBER_MESSAGE = "You are not a member of this household."
MEALIE_DOWN_MESSAGE = "Mealie could not be reached. Is it running?"
NOT_CONFIGURED_MESSAGE = "The server has no CHEF_SECRET_KEY set, so Mealie can't be connected."


# --- Dependency: how to build a Mealie client (overridden in tests) ---


def get_mealie_client_factory(settings: AppSettings) -> mealie_link.MealieClientFactory:
    """Return a function that builds a client for the configured Mealie."""

    def make_client(api_token: str) -> MealieClient:
        return MealieClient(settings.mealie_url, api_token)

    return make_client


MealieClientFactoryDep = Annotated[mealie_link.MealieClientFactory, Depends(get_mealie_client_factory)]


# --- Request / response shapes ---


class ConnectMealieRequest(BaseModel):
    """A Mealie API token (from Mealie: User profile -> API Tokens)."""

    api_token: Annotated[str, Field(min_length=1, max_length=2000)]


class MealieConnectionOut(BaseModel):
    """A household's link to Mealie. Never includes the token."""

    connected: bool
    mealie_username: str | None = None
    mealie_household_name: str | None = None
    connected_at: datetime | None = None

    @classmethod
    def from_household(cls, household: Household) -> "MealieConnectionOut":
        """Describe the household's current Mealie link."""
        return cls(
            connected=household.mealie_token_encrypted is not None,
            mealie_username=household.mealie_username,
            mealie_household_name=household.mealie_household_name,
            connected_at=household.mealie_connected_at,
        )


class MealieRecipeOut(BaseModel):
    """One recipe in the live Mealie list."""

    slug: str
    name: str | None
    tags: list[str]


# --- Connection management (any member) ---


@router.get("/api/households/{household_id}/mealie")
def get_mealie_connection(
    household_id: int, login_session: CurrentLoginSession, db: DbSession
) -> MealieConnectionOut:
    """Is this household connected to Mealie, and as whom?"""
    try:
        household = mealie_link.household_for_member(db, login_session.user_id, household_id)
    except NotAMemberError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_A_MEMBER_MESSAGE) from None
    return MealieConnectionOut.from_household(household)


@router.put("/api/households/{household_id}/mealie")
def connect_mealie(
    household_id: int,
    payload: ConnectMealieRequest,
    login_session: CurrentLoginSession,
    db: DbSession,
    settings: AppSettings,
    make_client: MealieClientFactoryDep,
) -> MealieConnectionOut:
    """Check a Mealie token and save it (encrypted) for this household."""
    try:
        household = mealie_link.connect_mealie(
            db, settings, make_client, login_session.user_id, household_id, payload.api_token.strip()
        )
    except NotAMemberError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_A_MEMBER_MESSAGE) from None
    except mealie_link.MealieNotConfiguredError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONFIGURED_MESSAGE) from None
    except mealie_link.MealieTokenRejectedError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mealie rejected this token. Check it and try again.") from None
    except MealieUnavailableError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, MEALIE_DOWN_MESSAGE) from None
    return MealieConnectionOut.from_household(household)


@router.delete("/api/households/{household_id}/mealie", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_mealie(household_id: int, login_session: CurrentLoginSession, db: DbSession) -> None:
    """Remove this household's Mealie token."""
    try:
        mealie_link.disconnect_mealie(db, login_session.user_id, household_id)
    except NotAMemberError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_A_MEMBER_MESSAGE) from None


# --- Live recipe list for the active household ---


@router.get("/api/mealie/recipes")
def list_live_mealie_recipes(
    household_id: ActiveHouseholdId,
    db: DbSession,
    settings: AppSettings,
    make_client: MealieClientFactoryDep,
) -> list[MealieRecipeOut]:
    """Recipes Mealie shows the active household, fetched live."""
    try:
        with mealie_link.open_mealie_client(db, settings, make_client, household_id) as mealie:
            recipes = mealie.list_recipe_summaries()
    except mealie_link.MealieNotConfiguredError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONFIGURED_MESSAGE) from None
    except mealie_link.MealieNotConnectedError as not_connected:
        raise HTTPException(status.HTTP_409_CONFLICT, str(not_connected)) from None
    except MealieAuthError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Mealie no longer accepts this household's token. Please reconnect."
        ) from None
    except MealieUnavailableError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, MEALIE_DOWN_MESSAGE) from None

    return [
        MealieRecipeOut(slug=recipe.slug, name=recipe.name, tags=[tag.slug for tag in recipe.tags])
        for recipe in recipes
    ]
