"""Cook log and ratings for the active household (JSON API).

All routes are scoped by ActiveHouseholdId; IDs from other households
answer 404, exactly like IDs that don't exist.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import AwareDatetime, BaseModel, Field

from app.api.deps import ActiveHouseholdId, AppSettings, CurrentLoginSession, DbSession
from app.api.mealie import MealieClientFactoryDep
from app.core.config import Settings
from app.domain.ratings import average_stars, latest_rating_per_user
from app.integrations.mealie_client import MealieError
from app.models import CookEvent, Recipe, User
from app.models.cooking import MAX_STARS, MIN_STARS
from app.services import cook_log, mealie_link
from app.services.accounts import list_members
from app.services.mealie_link import MealieClientFactory
from app.services.mealie_writeback import write_back_one

# Outcome of writing a cook back to Mealie right after it was saved:
# - "up_to_date": Mealie now matches the cook log
# - "pending": Mealie couldn't be reached; the next recipe sync retries
# - "not_connected": the household has no working Mealie link
WritebackStatus = Literal["up_to_date", "pending", "not_connected"]

router = APIRouter(tags=["cooking"])

# Field limits for cook-log input.
MAX_SERVINGS = 100
MAX_NOTES_LENGTH = 2000
DEFAULT_LOG_LIMIT = 50
MAX_LOG_LIMIT = 500

Stars = Annotated[int, Field(ge=MIN_STARS, le=MAX_STARS)]
Servings = Annotated[int, Field(ge=1, le=MAX_SERVINGS)]
Notes = Annotated[str, Field(max_length=MAX_NOTES_LENGTH)]


# --- Request / response shapes ---


class LogCookRequest(BaseModel):
    """Everything is optional: an empty body means "I cooked it just now"."""

    # Must include a timezone (e.g. "2026-10-21T19:30:00-04:00" or "...Z"),
    # so the time isn't ambiguous.
    cooked_at: AwareDatetime | None = None
    cooked_by_user_id: int | None = None
    servings: Servings | None = None
    notes: Notes | None = None
    stars: Stars | None = None  # the logger's own rating, if given


class UpdateCookRequest(BaseModel):
    """Only fields present in the request body are changed."""

    cooked_at: AwareDatetime | None = None
    cooked_by_user_id: int | None = None
    servings: Servings | None = None
    notes: Notes | None = None


class PersonOut(BaseModel):
    """A household member as shown next to cooks and ratings."""

    user_id: int
    display_name: str


class CookEventOut(BaseModel):
    """One cook-log entry."""

    id: int
    recipe_id: int
    recipe_name: str
    cooked_at: datetime
    cooked_by: PersonOut | None  # None if that account was deleted
    servings: int | None
    notes: str | None
    logged_by: PersonOut | None
    # Only on responses to a change (log/edit); None in log listings.
    mealie_writeback: WritebackStatus | None = None


class RateRequest(BaseModel):
    """A new rating; replaces your previous one for scoring."""

    stars: Stars


class RatingEntryOut(BaseModel):
    """One person's rating at one time."""

    user_id: int
    display_name: str
    stars: int
    rated_at: datetime


class RecipeRatingsOut(BaseModel):
    """Who thinks what about a recipe, now and over time."""

    average_stars: float | None  # mean of each person's latest rating
    my_stars: int | None
    latest: list[RatingEntryOut]  # one per person who rated, best first
    history: list[RatingEntryOut]  # every rating, newest first


# --- Helpers ---


@contextmanager
def cook_log_errors_as_http() -> Iterator[None]:
    """Turn the cook-log service's expected errors into HTTP answers."""
    try:
        yield
    except cook_log.RecipeNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recipe not found.") from None
    except cook_log.CookEventNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cook-log entry not found.") from None
    except cook_log.NotHouseholdMemberError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The cook must be a member of this household.") from None
    except (cook_log.InvalidCookTimeError, cook_log.InvalidStarsError) as invalid_input:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(invalid_input)) from None


def people_by_id(db: DbSession, household_id: int) -> dict[int, PersonOut]:
    """Household members keyed by user ID, for labelling entries."""
    return {
        member.id: PersonOut(user_id=member.id, display_name=member.display_name)
        for member in list_members(db, household_id)
    }


def person_for(db: DbSession, people: dict[int, PersonOut], user_id: int | None) -> PersonOut | None:
    """Label a user: from the member list, or looked up if they've left."""
    if user_id is None:
        return None
    if user_id in people:
        return people[user_id]
    former_member = db.get(User, user_id)
    return PersonOut(user_id=user_id, display_name=former_member.display_name) if former_member else None


def try_write_back(
    db: DbSession, settings: Settings, make_client: MealieClientFactory, household_id: int, recipe_id: int
) -> WritebackStatus:
    """Push a recipe's latest cook to Mealie now, without ever failing the request.

    The cook is already saved locally; a Mealie problem only leaves the
    write-back pending for the next sync (plan §10: degrade gracefully).
    """
    try:
        with mealie_link.open_mealie_client(db, settings, make_client, household_id) as mealie:
            write_back_one(db, mealie, household_id, recipe_id)
    except (mealie_link.MealieNotConfiguredError, mealie_link.MealieNotConnectedError):
        return "not_connected"
    except MealieError:
        db.rollback()  # discard any half-recorded state; it's retried at sync
        return "pending"
    return "up_to_date"


def cook_event_out(db: DbSession, cook_event: CookEvent, people: dict[int, PersonOut]) -> CookEventOut:
    """Build the response for one cook-log entry."""
    return CookEventOut(
        id=cook_event.id,
        recipe_id=cook_event.recipe_id,
        recipe_name=db.get(Recipe, cook_event.recipe_id).name,
        cooked_at=cook_event.cooked_at,
        cooked_by=person_for(db, people, cook_event.cooked_by_user_id),
        servings=cook_event.servings,
        notes=cook_event.notes,
        logged_by=person_for(db, people, cook_event.logged_by_user_id),
    )


# --- Cook log routes ---


@router.post("/api/recipes/{recipe_id}/cooks", status_code=status.HTTP_201_CREATED)
def log_cook(
    recipe_id: int,
    payload: LogCookRequest,
    household_id: ActiveHouseholdId,
    login_session: CurrentLoginSession,
    db: DbSession,
    settings: AppSettings,
    make_client: MealieClientFactoryDep,
) -> CookEventOut:
    """'I cooked this': record a cook, optionally with your rating.

    Then updates Mealie's "last made" (and clears want-to-try after the
    first cook); `mealie_writeback` in the response says how that went.
    """
    with cook_log_errors_as_http():
        cook_event = cook_log.log_cook(
            db, household_id, recipe_id, login_session.user_id,
            cooked_at=payload.cooked_at,
            cooked_by_user_id=payload.cooked_by_user_id,
            servings=payload.servings,
            notes=payload.notes,
            stars=payload.stars,
        )
    writeback_status = try_write_back(db, settings, make_client, household_id, recipe_id)
    response = cook_event_out(db, cook_event, people_by_id(db, household_id))
    response.mealie_writeback = writeback_status
    return response


@router.get("/api/cooks")
def list_cooks(
    household_id: ActiveHouseholdId,
    db: DbSession,
    recipe_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LOG_LIMIT)] = DEFAULT_LOG_LIMIT,
) -> list[CookEventOut]:
    """The household's cook log, newest first (`?recipe_id=` to filter)."""
    people = people_by_id(db, household_id)
    return [
        cook_event_out(db, cook_event, people)
        for cook_event in cook_log.list_cooks(db, household_id, recipe_id=recipe_id, limit=limit)
    ]


@router.patch("/api/cooks/{cook_event_id}")
def update_cook(
    cook_event_id: int,
    payload: UpdateCookRequest,
    household_id: ActiveHouseholdId,
    db: DbSession,
    settings: AppSettings,
    make_client: MealieClientFactoryDep,
) -> CookEventOut:
    """Edit an entry. Send only the fields to change; `null` clears notes/servings."""
    # model_fields_set = the keys actually present in the JSON body, which
    # tells "leave alone" (absent) apart from "clear it" (null).
    changes = {field_name: getattr(payload, field_name) for field_name in payload.model_fields_set}
    with cook_log_errors_as_http():
        cook_event = cook_log.update_cook(db, household_id, cook_event_id, changes)

    # A changed cook time can change which cook is the latest.
    writeback_status = try_write_back(db, settings, make_client, household_id, cook_event.recipe_id)
    response = cook_event_out(db, cook_event, people_by_id(db, household_id))
    response.mealie_writeback = writeback_status
    return response


@router.delete("/api/cooks/{cook_event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cook(
    cook_event_id: int,
    household_id: ActiveHouseholdId,
    db: DbSession,
    settings: AppSettings,
    make_client: MealieClientFactoryDep,
) -> None:
    """Remove an entry (any member may). Ratings given with it are kept.

    If it was the latest cook, Mealie's "last made" moves back to the
    previous one (best effort, like logging).
    """
    with cook_log_errors_as_http():
        recipe_id = cook_log.get_household_cook(db, household_id, cook_event_id).recipe_id
        cook_log.delete_cook(db, household_id, cook_event_id)
    try_write_back(db, settings, make_client, household_id, recipe_id)


# --- Rating routes ---


@router.put("/api/recipes/{recipe_id}/rating")
def rate_recipe(
    recipe_id: int,
    payload: RateRequest,
    household_id: ActiveHouseholdId,
    login_session: CurrentLoginSession,
    db: DbSession,
) -> RecipeRatingsOut:
    """Set your rating (no cook needed). Earlier ratings stay in history."""
    with cook_log_errors_as_http():
        cook_log.rate_recipe(db, household_id, recipe_id, login_session.user_id, payload.stars)
    return get_recipe_ratings(recipe_id, household_id, login_session, db)


@router.get("/api/recipes/{recipe_id}/ratings")
def get_recipe_ratings(
    recipe_id: int, household_id: ActiveHouseholdId, login_session: CurrentLoginSession, db: DbSession
) -> RecipeRatingsOut:
    """Everyone's current rating, the average, and the full history."""
    with cook_log_errors_as_http():
        stored_ratings = cook_log.recipe_ratings(db, household_id, recipe_id)

    people = people_by_id(db, household_id)

    def entry_for(user_id: int, stars: int, rated_at: datetime) -> RatingEntryOut:
        person = person_for(db, people, user_id)
        return RatingEntryOut(
            user_id=user_id, display_name=person.display_name if person else "?", stars=stars, rated_at=rated_at
        )

    latest_by_user = latest_rating_per_user(cook_log.as_rating_record(stored) for stored in stored_ratings)
    my_latest = latest_by_user.get(login_session.user_id)
    return RecipeRatingsOut(
        average_stars=average_stars(latest_by_user.values()),
        my_stars=my_latest.stars if my_latest else None,
        latest=sorted(
            (entry_for(record.user_id, record.stars, record.created_at) for record in latest_by_user.values()),
            key=lambda entry: (-entry.stars, entry.display_name),
        ),
        history=[entry_for(stored.user_id, stored.stars, stored.created_at) for stored in stored_ratings],
    )
