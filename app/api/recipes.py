"""The active household's recipe library: sync from Mealie and read the cache.

Reads come from the local cache, so they work even while Mealie is down
(plan §10). Only the sync route talks to Mealie.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import ActiveHouseholdId, AppSettings, DbSession
from app.api.mealie import MealieClientFactoryDep, mealie_errors_as_http
from app.domain.ingredients import is_want_to_try
from app.models import Household, Recipe
from app.services import mealie_link
from app.services.recipe_sync import sync_household_recipes

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


# --- Response shapes ---


class RecipeSyncOut(BaseModel):
    """Counts from one sync run."""

    added: int
    updated: int
    unchanged: int
    removed: int
    restored: int
    finished_at: datetime


class RecipeOut(BaseModel):
    """A recipe in the library list."""

    id: int
    slug: str
    name: str
    tags: list[str]
    want_to_try: bool
    ingredient_count: int
    removed_at: datetime | None

    @classmethod
    def from_recipe(cls, recipe: Recipe) -> "RecipeOut":
        """Build from a cached recipe (ingredients must be loaded)."""
        return cls(
            id=recipe.id,
            slug=recipe.slug,
            name=recipe.name,
            tags=recipe.tags,
            want_to_try=is_want_to_try(recipe.tags),
            ingredient_count=len(recipe.ingredients),
            removed_at=recipe.removed_at,
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
    """
    with mealie_errors_as_http():
        with mealie_link.open_mealie_client(db, settings, make_client, household_id) as mealie:
            result = sync_household_recipes(db, mealie, household_id, full_refresh=full_refresh)
    return RecipeSyncOut(**vars(result))


@router.get("")
def list_recipes(household_id: ActiveHouseholdId, db: DbSession, include_removed: bool = False) -> RecipeListOut:
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

    return RecipeListOut(
        synced_at=db.get(Household, household_id).recipes_synced_at,
        recipes=[RecipeOut.from_recipe(recipe) for recipe in db.scalars(query)],
    )


@router.get("/{recipe_id}")
def get_recipe(recipe_id: int, household_id: ActiveHouseholdId, db: DbSession) -> RecipeDetailOut:
    """One cached recipe with its ingredients.

    404 if it doesn't exist *or* belongs to another household (scoped by
    the active household, plan §9.4).
    """
    recipe = db.scalars(
        select(Recipe).where(Recipe.id == recipe_id, Recipe.household_id == household_id)
    ).one_or_none()
    if recipe is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recipe not found.")

    return RecipeDetailOut(
        **RecipeOut.from_recipe(recipe).model_dump(),
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
