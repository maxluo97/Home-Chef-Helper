"""Write the cook log back to Mealie so Mealie's UI stays consistent (plan §2.3).

For each recipe: set Mealie's "last made" to the household's latest cook,
and remove the want-to-try tag after the first cook (plan §9.7). What to
send is decided by app/domain/writeback.py; this module does the I/O.

Write-back is best effort. A cook is always saved locally first; if Mealie
can't be reached, the recipe stays "pending" and the next recipe sync
retries it. Each recipe is committed as soon as Mealie accepts it, since
Mealie's side can't be rolled back anyway.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.ingredients import WANT_TO_TRY_TAG_SLUG
from app.domain.writeback import WritebackPlan, plan_writeback
from app.integrations.mealie_client import MealieClient, MealieNotFoundError
from app.models import Recipe
from app.models.base import utc_now
from app.services.cook_log import RecipeCookStats, household_recipe_stats


@dataclass
class WritebackResult:
    """What one write-back pass sent to Mealie."""

    last_made_updated: int = 0
    want_to_try_cleared: int = 0
    missing_in_mealie: int = 0  # recipe deleted in Mealie; skipped


def plan_for_recipe(recipe: Recipe, stats: RecipeCookStats | None) -> WritebackPlan:
    """What this recipe needs written, given its cook-log stats."""
    stats = stats or RecipeCookStats()
    return plan_writeback(
        latest_cook_at=stats.last_cooked_at,
        last_made_pushed=recipe.mealie_last_made_pushed,
        cook_count=stats.cook_count,
        tag_slugs=recipe.tags,
        want_to_try_cleared_at=recipe.want_to_try_cleared_at,
    )


def write_back_recipe(
    db: Session, mealie: MealieClient, recipe: Recipe, stats: RecipeCookStats | None, result: WritebackResult
) -> None:
    """Send one recipe's pending changes to Mealie and record them.

    MealieUnavailableError / MealieAuthError propagate; whatever was already
    accepted stays recorded.
    """
    plan = plan_for_recipe(recipe, stats)
    if not plan.is_needed or recipe.removed_at is not None:
        return

    try:
        # --- Last made ---
        if plan.set_last_made_to is not None:
            mealie.set_last_made(recipe.slug, plan.set_last_made_to)
            recipe.mealie_last_made_pushed = plan.set_last_made_to
            db.commit()
            result.last_made_updated += 1

        # --- Want-to-try tag, once, after the first cook ---
        if plan.clear_want_to_try:
            mealie.remove_recipe_tag(recipe.slug, WANT_TO_TRY_TAG_SLUG)
            # Mirror it locally so scoring stops boosting it right away; the
            # next sync confirms from Mealie.
            recipe.tags = [tag_slug for tag_slug in recipe.tags if tag_slug != WANT_TO_TRY_TAG_SLUG]
            recipe.want_to_try_cleared_at = utc_now()
            db.commit()
            result.want_to_try_cleared += 1
    except MealieNotFoundError:
        # Deleted in Mealie since the last sync; the next sync marks it removed.
        result.missing_in_mealie += 1


def write_back_one(db: Session, mealie: MealieClient, household_id: int, recipe_id: int) -> WritebackResult:
    """Write back a single recipe (e.g. right after a cook is logged)."""
    result = WritebackResult()
    recipe = db.get(Recipe, recipe_id)
    if recipe is not None and recipe.household_id == household_id:
        write_back_recipe(db, mealie, recipe, household_recipe_stats(db, household_id).get(recipe_id), result)
    return result


def write_back_household(db: Session, mealie: MealieClient, household_id: int) -> WritebackResult:
    """Write back every recipe in the household that has pending changes."""
    result = WritebackResult()
    stats_by_recipe = household_recipe_stats(db, household_id)
    household_recipes = db.scalars(
        select(Recipe).where(Recipe.household_id == household_id, Recipe.removed_at.is_(None))
    )
    for recipe in household_recipes:
        write_back_recipe(db, mealie, recipe, stats_by_recipe.get(recipe.id), result)
    return result


def count_pending(db: Session, household_id: int) -> int:
    """How many of the household's recipes still need writing to Mealie."""
    stats_by_recipe = household_recipe_stats(db, household_id)
    household_recipes = db.scalars(
        select(Recipe).where(Recipe.household_id == household_id, Recipe.removed_at.is_(None))
    )
    return sum(
        1 for recipe in household_recipes if plan_for_recipe(recipe, stats_by_recipe.get(recipe.id)).is_needed
    )
