"""Cook log and per-person ratings for a household (plan §3, §9.2).

Every function takes the household it acts in and only touches that
household's rows, so a caller scoped by ActiveHouseholdId can't reach
another household's data even with a guessed ID.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.ratings import RatingRecord, average_stars, latest_rating_per_user
from app.models import CookEvent, Rating, Recipe
from app.models.base import utc_now
from app.models.cooking import MAX_STARS, MIN_STARS
from app.services.accounts import is_member

# How far in the future a cook time may be (phone clocks drift a little).
FUTURE_TOLERANCE = timedelta(minutes=5)

# Cook-event fields that can be edited after logging.
EDITABLE_COOK_FIELDS = ("cooked_at", "cooked_by_user_id", "servings", "notes")


# --- Errors ---


class CookLogError(Exception):
    """Base class for expected, user-facing cook-log errors."""


class RecipeNotFoundError(CookLogError):
    """No such recipe in this household."""


class CookEventNotFoundError(CookLogError):
    """No such cook-log entry in this household."""


class NotHouseholdMemberError(CookLogError):
    """The named cook isn't a member of this household."""


class InvalidCookTimeError(CookLogError):
    """The cook time is in the future."""


class InvalidStarsError(CookLogError):
    """Stars outside 1-5."""


# --- Lookups scoped to the household ---


def get_household_recipe(db: Session, household_id: int, recipe_id: int) -> Recipe:
    """The household's recipe, or RecipeNotFoundError."""
    recipe = db.scalars(
        select(Recipe).where(Recipe.id == recipe_id, Recipe.household_id == household_id)
    ).one_or_none()
    if recipe is None:
        raise RecipeNotFoundError(recipe_id)
    return recipe


def get_household_cook(db: Session, household_id: int, cook_event_id: int) -> CookEvent:
    """The household's cook-log entry, or CookEventNotFoundError."""
    cook_event = db.scalars(
        select(CookEvent).where(CookEvent.id == cook_event_id, CookEvent.household_id == household_id)
    ).one_or_none()
    if cook_event is None:
        raise CookEventNotFoundError(cook_event_id)
    return cook_event


# --- Validation ---


def check_cook_time(cooked_at: datetime) -> None:
    """Reject cook times meaningfully in the future."""
    if cooked_at > utc_now() + FUTURE_TOLERANCE:
        raise InvalidCookTimeError("The cook time can't be in the future.")


def check_cook_is_member(db: Session, household_id: int, cooked_by_user_id: int | None) -> None:
    """The named cook (if any) must belong to the household."""
    if cooked_by_user_id is not None and not is_member(db, cooked_by_user_id, household_id):
        raise NotHouseholdMemberError(cooked_by_user_id)


def check_stars(stars: int) -> None:
    """Whole stars from 1 to 5 only (plan §9.2)."""
    if not MIN_STARS <= stars <= MAX_STARS:
        raise InvalidStarsError(f"Stars must be between {MIN_STARS} and {MAX_STARS}.")


# --- Cook log ---


def log_cook(
    db: Session,
    household_id: int,
    recipe_id: int,
    logged_by_user_id: int,
    *,
    cooked_at: datetime | None = None,
    cooked_by_user_id: int | None = None,
    servings: int | None = None,
    notes: str | None = None,
    stars: int | None = None,
) -> CookEvent:
    """Record that a recipe was cooked, optionally with the logger's rating.

    Defaults: cooked now, by the person logging it.
    """
    get_household_recipe(db, household_id, recipe_id)  # must be this household's recipe

    # --- Fill in defaults, then validate ---
    cooked_at = cooked_at or utc_now()
    if cooked_by_user_id is None:
        cooked_by_user_id = logged_by_user_id
    check_cook_time(cooked_at)
    check_cook_is_member(db, household_id, cooked_by_user_id)
    if stars is not None:
        check_stars(stars)

    cook_event = CookEvent(
        household_id=household_id,
        recipe_id=recipe_id,
        cooked_at=cooked_at,
        cooked_by_user_id=cooked_by_user_id,
        servings=servings,
        notes=notes.strip() if notes else None,
        logged_by_user_id=logged_by_user_id,
    )
    db.add(cook_event)
    db.flush()  # assigns cook_event.id for the rating link

    # A rating given while logging belongs to the person logging.
    if stars is not None:
        db.add(Rating(
            household_id=household_id,
            recipe_id=recipe_id,
            user_id=logged_by_user_id,
            stars=stars,
            cook_event_id=cook_event.id,
        ))
    db.commit()
    return cook_event


def update_cook(db: Session, household_id: int, cook_event_id: int, changes: dict[str, Any]) -> CookEvent:
    """Edit a cook-log entry. `changes` holds only the fields to change.

    Any household member may edit (plan §9.2). Unknown keys are ignored.
    """
    cook_event = get_household_cook(db, household_id, cook_event_id)

    if "cooked_at" in changes:
        if changes["cooked_at"] is None:
            raise InvalidCookTimeError("A cook time is required.")
        check_cook_time(changes["cooked_at"])
    if "cooked_by_user_id" in changes:
        check_cook_is_member(db, household_id, changes["cooked_by_user_id"])
    if changes.get("notes"):
        changes = changes | {"notes": changes["notes"].strip()}

    for field_name in EDITABLE_COOK_FIELDS:
        if field_name in changes:
            setattr(cook_event, field_name, changes[field_name])
    db.commit()
    return cook_event


def delete_cook(db: Session, household_id: int, cook_event_id: int) -> None:
    """Remove a cook-log entry. Ratings given with it are kept."""
    db.delete(get_household_cook(db, household_id, cook_event_id))
    db.commit()


def list_cooks(db: Session, household_id: int, recipe_id: int | None = None, limit: int = 50) -> list[CookEvent]:
    """The household's cook log, newest first, optionally for one recipe."""
    query = select(CookEvent).where(CookEvent.household_id == household_id)
    if recipe_id is not None:
        query = query.where(CookEvent.recipe_id == recipe_id)
    return list(db.scalars(query.order_by(CookEvent.cooked_at.desc(), CookEvent.id.desc()).limit(limit)))


# --- Ratings ---


def rate_recipe(db: Session, household_id: int, recipe_id: int, user_id: int, stars: int) -> Rating:
    """Add a rating by this person. Earlier ratings stay as history."""
    get_household_recipe(db, household_id, recipe_id)
    check_stars(stars)
    new_rating = Rating(household_id=household_id, recipe_id=recipe_id, user_id=user_id, stars=stars)
    db.add(new_rating)
    db.commit()
    return new_rating


def recipe_ratings(db: Session, household_id: int, recipe_id: int) -> list[Rating]:
    """Every rating of the recipe in this household, newest first."""
    get_household_recipe(db, household_id, recipe_id)
    return list(db.scalars(
        select(Rating)
        .where(Rating.household_id == household_id, Rating.recipe_id == recipe_id)
        .order_by(Rating.created_at.desc(), Rating.id.desc())
    ))


def as_rating_record(stored_rating: Rating) -> RatingRecord:
    """Convert a stored rating to the pure-domain form."""
    return RatingRecord(stored_rating.id, stored_rating.user_id, stored_rating.stars, stored_rating.created_at)


# --- Per-recipe summary for lists ---


@dataclass
class RecipeCookStats:
    """Cook-log and rating summary for one recipe."""

    cook_count: int = 0
    last_cooked_at: datetime | None = None
    latest_ratings: dict[int, RatingRecord] = field(default_factory=dict)  # by user ID

    @property
    def average_stars(self) -> float | None:
        """Mean of each person's latest rating."""
        return average_stars(self.latest_ratings.values())


def household_recipe_stats(db: Session, household_id: int) -> dict[int, RecipeCookStats]:
    """Cook counts, last-cooked times, and latest ratings for every recipe.

    Two queries for the whole household, rather than two per recipe.
    """
    stats_by_recipe: dict[int, RecipeCookStats] = {}

    # --- Cook counts and most recent cook ---
    cook_rows = db.execute(
        select(CookEvent.recipe_id, func.count(CookEvent.id), func.max(CookEvent.cooked_at))
        .where(CookEvent.household_id == household_id)
        .group_by(CookEvent.recipe_id)
    )
    for recipe_id, nCooks, last_cooked_at in cook_rows:
        # max() keeps the column's UTCDateTime type, so this is aware UTC.
        stats_by_recipe[recipe_id] = RecipeCookStats(cook_count=nCooks, last_cooked_at=last_cooked_at)

    # --- Latest rating per person per recipe ---
    records_by_recipe: dict[int, list[RatingRecord]] = {}
    for stored_rating in db.scalars(select(Rating).where(Rating.household_id == household_id)):
        records_by_recipe.setdefault(stored_rating.recipe_id, []).append(as_rating_record(stored_rating))
    for recipe_id, rating_records in records_by_recipe.items():
        stats_by_recipe.setdefault(recipe_id, RecipeCookStats()).latest_ratings = latest_rating_per_user(
            rating_records
        )

    return stats_by_recipe
