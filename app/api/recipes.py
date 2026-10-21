"""The active household's recipe library: sync from Mealie and read the cache.

Reads come from the local cache, so they work even while Mealie is down
(plan §10). Only the sync route talks to Mealie.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import ActiveHouseholdId, AppSettings, CurrentLoginSession, DbSession
from app.api.mealie import MealieClientFactoryDep, mealie_errors_as_http
from app.domain.ingredients import is_want_to_try
from app.models import Household, Recipe
from app.services import mealie_link
from app.integrations.mealie_client import MealieError
from app.services.cook_log import RecipeCookStats, household_recipe_stats
from app.services.mealie_writeback import WritebackResult, count_pending, plan_for_recipe, write_back_household
from app.services.recipe_sync import sync_household_recipes

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


# --- Response shapes ---


class RecipeSyncOut(BaseModel):
    """Counts from one sync run, including the write-back pass after it."""

    added: int
    updated: int
    unchanged: int
    removed: int
    restored: int
    finished_at: datetime
    # --- Write-back of the cook log to Mealie ---
    last_made_updated: int
    want_to_try_cleared: int
    writeback_pending: int  # still not written (Mealie failed part-way)


class RecipeOut(BaseModel):
    """A recipe in the library list, with the household's history of it."""

    id: int
    slug: str
    name: str
    tags: list[str]
    want_to_try: bool
    ingredient_count: int
    removed_at: datetime | None
    # --- From the cook log and ratings ---
    cook_count: int
    last_cooked_at: datetime | None
    average_stars: float | None  # mean of each member's latest rating
    my_stars: int | None  # the caller's latest rating
    # True if Mealie hasn't yet been told about the latest cook (retried at sync).
    mealie_writeback_pending: bool

    @classmethod
    def from_recipe(cls, recipe: Recipe, stats: RecipeCookStats | None, user_id: int) -> "RecipeOut":
        """Build from a cached recipe (ingredients loaded) and its stats."""
        writeback_pending = recipe.removed_at is None and plan_for_recipe(recipe, stats).is_needed
        stats = stats or RecipeCookStats()
        my_latest = stats.latest_ratings.get(user_id)
        return cls(
            id=recipe.id,
            slug=recipe.slug,
            name=recipe.name,
            tags=recipe.tags,
            want_to_try=is_want_to_try(recipe.tags),
            ingredient_count=len(recipe.ingredients),
            removed_at=recipe.removed_at,
            cook_count=stats.cook_count,
            last_cooked_at=stats.last_cooked_at,
            average_stars=stats.average_stars,
            my_stars=my_latest.stars if my_latest else None,
            mealie_writeback_pending=writeback_pending,
        )


class RecipeListOut(BaseModel):
    """The library, plus when it was last refreshed from Mealie."""

    synced_at: datetime | None
    recipes: list[RecipeOut]


class RecipeIngredientOut(BaseModel):
    """One ingredient line."""

    position: int
    display_text: str
    section_title: str | None
    quantity: float | None
    unit: str | None
    food_name: str | None
    canonical_name: str | None
    is_optional: bool


class RecipeDetailOut(RecipeOut):
    """A recipe with its ingredient lines."""

    ingredients: list[RecipeIngredientOut]


# --- Routes ---


@router.post("/sync")
def sync_recipes(
    household_id: ActiveHouseholdId,
    db: DbSession,
    settings: AppSettings,
    make_client: MealieClientFactoryDep,
    full_refresh: bool = False,
) -> RecipeSyncOut:
    """Refresh the active household's library from Mealie now.

    `?full_refresh=true` re-downloads every recipe, not just changed ones.
    Afterwards, any cook-log changes Mealie hasn't heard about yet ("last
    made", want-to-try) are written back.
    """
    with mealie_errors_as_http():
        with mealie_link.open_mealie_client(db, settings, make_client, household_id) as mealie:
            sync_result = sync_household_recipes(db, mealie, household_id, full_refresh=full_refresh)

            # The sync itself is saved; a write-back failure only leaves
            # some recipes pending, reported in the counts.
            writeback_result = WritebackResult()
            try:
                writeback_result = write_back_household(db, mealie, household_id)
            except MealieError:
                db.rollback()

    return RecipeSyncOut(
        **vars(sync_result),
        last_made_updated=writeback_result.last_made_updated,
        want_to_try_cleared=writeback_result.want_to_try_cleared,
        writeback_pending=count_pending(db, household_id),
    )


@router.get("")
def list_recipes(
    household_id: ActiveHouseholdId,
    login_session: CurrentLoginSession,
    db: DbSession,
    include_removed: bool = False,
) -> RecipeListOut:
    """The active household's cached recipes, by name.

    Recipes deleted from Mealie are hidden unless `?include_removed=true`.
    """
    query = (
        select(Recipe)
        .where(Recipe.household_id == household_id)
        .options(selectinload(Recipe.ingredients))  # load lines in one extra query, not one per recipe
        .order_by(Recipe.name)
    )
    if not include_removed:
        query = query.where(Recipe.removed_at.is_(None))

    stats_by_recipe = household_recipe_stats(db, household_id)
    return RecipeListOut(
        synced_at=db.get(Household, household_id).recipes_synced_at,
        recipes=[
            RecipeOut.from_recipe(recipe, stats_by_recipe.get(recipe.id), login_session.user_id)
            for recipe in db.scalars(query)
        ],
    )


@router.get("/{recipe_id}")
def get_recipe(
    recipe_id: int, household_id: ActiveHouseholdId, login_session: CurrentLoginSession, db: DbSession
) -> RecipeDetailOut:
    """One cached recipe with its ingredients.

    404 if it doesn't exist *or* belongs to another household (scoped by
    the active household, plan §9.4).
    """
    recipe = db.scalars(
        select(Recipe).where(Recipe.id == recipe_id, Recipe.household_id == household_id)
    ).one_or_none()
    if recipe is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recipe not found.")

    stats = household_recipe_stats(db, household_id).get(recipe.id)
    return RecipeDetailOut(
        **RecipeOut.from_recipe(recipe, stats, login_session.user_id).model_dump(),
        ingredients=[
            RecipeIngredientOut(
                position=line.position,
                display_text=line.display_text,
                section_title=line.section_title,
                quantity=line.quantity,
                unit=line.unit,
                food_name=line.food_name,
                canonical_name=line.canonical_name,
                is_optional=line.is_optional,
            )
            for line in recipe.ingredients
        ],
    )
