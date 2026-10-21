"""Copy a household's Mealie recipes into the local recipe cache.

One sync run:
1. Lists every recipe Mealie shows the household's token (plan §9.7).
2. Downloads full recipes (ingredients) only for new or changed ones,
   judged by Mealie's `updatedAt`, unless a full refresh is requested.
3. Marks cached recipes that vanished from Mealie as removed (kept, since
   cook logs point at them) and un-marks any that come back.

The whole run is one database transaction: if Mealie fails part-way, the
cache is left exactly as it was before the run.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.ingredients import canonical_ingredient_name, is_optional_line
from app.integrations.mealie_client import MealieClient, MealieIngredient, MealieNotFoundError
from app.models import Household, Recipe, RecipeIngredient
from app.models.base import utc_now


@dataclass
class RecipeSyncResult:
    """What one sync run changed."""

    added: int = 0  # new to this household
    updated: int = 0  # changed in Mealie (or refreshed), ingredients re-copied
    unchanged: int = 0  # same as last time; only light fields refreshed
    removed: int = 0  # no longer in Mealie; marked removed
    restored: int = 0  # previously removed, back in Mealie
    finished_at: datetime | None = None


def ingredient_rows_from_mealie(mealie_lines: list[MealieIngredient]) -> list[RecipeIngredient]:
    """Turn Mealie ingredient lines into local rows, in recipe order."""
    ingredient_rows = []
    for iLine, mealie_line in enumerate(mealie_lines):
        food_name = mealie_line.food.name if mealie_line.food else None
        ingredient_rows.append(
            RecipeIngredient(
                position=iLine,
                # Fall back through the texts Mealie may provide.
                display_text=mealie_line.display or mealie_line.original_text or mealie_line.note or "",
                original_text=mealie_line.original_text,
                section_title=mealie_line.title or None,  # Mealie sends "" for none
                quantity=mealie_line.quantity,
                unit=mealie_line.unit.name if mealie_line.unit else None,
                note=mealie_line.note,
                mealie_food_id=mealie_line.food.id if mealie_line.food else None,
                food_name=food_name,
                canonical_name=canonical_ingredient_name(food_name),
                is_optional=is_optional_line(mealie_line.note, mealie_line.original_text),
            )
        )
    return ingredient_rows


def sync_household_recipes(
    db: Session, mealie: MealieClient, household_id: int, full_refresh: bool = False
) -> RecipeSyncResult:
    """Bring one household's recipe cache in line with Mealie.

    `mealie` must be a client using this household's token. Mealie errors
    (MealieUnavailableError, MealieAuthError) propagate, and nothing is saved.
    `full_refresh=True` re-downloads every recipe even if unchanged.
    """
    result = RecipeSyncResult()
    sync_time = utc_now()

    # --- What we have now, keyed by Mealie's recipe ID ---
    cached_by_mealie_id = {
        cached_recipe.mealie_recipe_id: cached_recipe
        for cached_recipe in db.scalars(select(Recipe).where(Recipe.household_id == household_id))
    }
    seen_mealie_ids: set[str] = set()

    # --- Walk Mealie's list ---
    for summary in mealie.iter_recipe_summaries():
        cached_recipe = cached_by_mealie_id.get(summary.id)
        is_new = cached_recipe is None
        was_removed = cached_recipe is not None and cached_recipe.removed_at is not None
        needs_ingredients = is_new or full_refresh or cached_recipe.mealie_updated_at != summary.updated_at

        # Download the full recipe first, so a recipe deleted between the
        # list and this call is simply skipped (and treated as gone).
        full_recipe = None
        if needs_ingredients:
            try:
                full_recipe = mealie.get_recipe(summary.slug)
            except MealieNotFoundError:
                continue
        seen_mealie_ids.add(summary.id)

        if is_new:
            cached_recipe = Recipe(household_id=household_id, mealie_recipe_id=summary.id)
            db.add(cached_recipe)

        # Light fields come with the list, so refresh them every time.
        cached_recipe.slug = summary.slug
        cached_recipe.name = summary.name or summary.slug
        cached_recipe.tags = [tag.slug for tag in summary.tags]
        cached_recipe.mealie_household_id = summary.household_id
        cached_recipe.synced_at = sync_time
        cached_recipe.removed_at = None

        if full_recipe is not None:
            # Replacing the list deletes the old lines (delete-orphan cascade).
            cached_recipe.ingredients = ingredient_rows_from_mealie(full_recipe.recipe_ingredient)
            cached_recipe.mealie_updated_at = summary.updated_at

        # --- Count what happened (each recipe lands in exactly one bucket) ---
        if is_new:
            result.added += 1
        elif was_removed:
            result.restored += 1
        elif full_recipe is not None:
            result.updated += 1
        else:
            result.unchanged += 1

    # --- Anything cached but no longer in Mealie is marked removed ---
    for mealie_recipe_id, cached_recipe in cached_by_mealie_id.items():
        if mealie_recipe_id not in seen_mealie_ids and cached_recipe.removed_at is None:
            cached_recipe.removed_at = sync_time
            result.removed += 1

    # --- Record success and save everything at once ---
    db.get(Household, household_id).recipes_synced_at = sync_time
    db.commit()
    result.finished_at = sync_time
    return result
