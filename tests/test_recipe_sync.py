"""Tests for copying Mealie recipes into the local cache."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import make_engine, make_session_factory
from app.core.migrate import upgrade_to_head
from app.core.security import generate_join_code
from app.integrations.mealie_client import MealieUnavailableError
from app.models import Household, Recipe, RecipeIngredient
from app.services.recipe_sync import sync_household_recipes
from tests.fake_mealie import FakeMealie, ingredient_line


# --- Fixtures ---


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    """A session on a freshly migrated throwaway database."""
    database_url = f"sqlite:///{(tmp_path / 'sync.db').as_posix()}"
    upgrade_to_head(database_url)
    engine = make_engine(database_url)
    with make_session_factory(engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture
def household_id(db: Session) -> int:
    """A household to sync into."""
    household = Household(name="Home", join_code=generate_join_code())
    db.add(household)
    db.commit()
    return household.id


@pytest.fixture
def mealie() -> FakeMealie:
    """A fake Mealie with three recipes (two pages at per_page=2)."""
    fake = FakeMealie()
    fake.put_recipe("carrot-cake", [
        ingredient_line("carrot", "3 carrots grated", note="grated"),
        ingredient_line("walnut", "1 cup walnuts (optional)", note="optional"),
    ])
    fake.put_recipe("lasagna", [
        ingredient_line("lasagna noodle", "12 lasagna noodles"),
        ingredient_line("Mozzarella  Cheese", "2 cups mozzarella", title="Topping"),
        ingredient_line(None, "salt to taste"),
    ], tags=["want-to-try"])
    fake.put_recipe("stir-fry", [ingredient_line("rice noodle", "8 oz rice noodles")])
    return fake


def run_sync(db: Session, mealie: FakeMealie, household_id: int, full_refresh: bool = False):
    """Sync with a fresh client, closing it afterwards."""
    with mealie.client() as client:
        return sync_household_recipes(db, client, household_id, full_refresh=full_refresh)


def cached_recipes(db: Session, household_id: int) -> dict[str, Recipe]:
    """All cached recipes for the household, keyed by slug."""
    db.expire_all()  # read what's really in the DB
    return {recipe.slug: recipe for recipe in db.scalars(select(Recipe).where(Recipe.household_id == household_id))}


# --- First sync ---


def test_first_sync_copies_every_recipe_across_pages(db: Session, mealie: FakeMealie, household_id: int) -> None:
    """All recipes (spanning two pages) are added with their ingredients."""
    result = run_sync(db, mealie, household_id)

    assert (result.added, result.updated, result.unchanged, result.removed) == (3, 0, 0, 0)
    recipes = cached_recipes(db, household_id)
    assert sorted(recipes) == ["carrot-cake", "lasagna", "stir-fry"]
    assert [line.display_text for line in recipes["carrot-cake"].ingredients] == [
        "3 carrots grated", "1 cup walnuts (optional)",
    ]
    assert db.get(Household, household_id).recipes_synced_at == result.finished_at


def test_ingredient_details_are_interpreted(db: Session, mealie: FakeMealie, household_id: int) -> None:
    """Canonical names, optional flags, section titles, and unparsed lines."""
    run_sync(db, mealie, household_id)
    lasagna = cached_recipes(db, household_id)["lasagna"]
    noodles, mozzarella, salt = lasagna.ingredients
    walnuts = cached_recipes(db, household_id)["carrot-cake"].ingredients[1]

    assert lasagna.tags == ["want-to-try"]
    assert [line.position for line in lasagna.ingredients] == [0, 1, 2]
    assert noodles.canonical_name == "lasagna noodle"
    assert mozzarella.canonical_name == "mozzarella cheese"  # whitespace + case normalised
    assert mozzarella.food_name == "Mozzarella  Cheese"  # Mealie's text kept as-is
    assert mozzarella.section_title == "Topping"
    assert noodles.section_title is None  # Mealie's "" becomes None
    assert (salt.food_name, salt.canonical_name) == (None, None)
    assert walnuts.is_optional and not noodles.is_optional


# --- Later syncs ---


def test_unchanged_recipes_are_not_downloaded_again(db: Session, mealie: FakeMealie, household_id: int) -> None:
    """A second sync with no edits only lists; no full-recipe downloads."""
    run_sync(db, mealie, household_id)
    mealie.requests_seen.clear()

    result = run_sync(db, mealie, household_id)

    assert (result.added, result.updated, result.unchanged) == (0, 0, 3)
    assert all(request_path == "/api/recipes" for request_path in mealie.requests_seen)


def test_edited_recipe_gets_new_ingredients(db: Session, mealie: FakeMealie, household_id: int) -> None:
    """An edit in Mealie replaces the cached ingredient lines."""
    run_sync(db, mealie, household_id)
    mealie.put_recipe("stir-fry", [
        ingredient_line("rice noodle", "8 oz rice noodles"),
        ingredient_line("basil", "1 cup thai basil"),
    ])

    result = run_sync(db, mealie, household_id)

    assert (result.updated, result.unchanged) == (1, 2)
    stir_fry = cached_recipes(db, household_id)["stir-fry"]
    assert [line.canonical_name for line in stir_fry.ingredients] == ["rice noodle", "basil"]
    # Old lines were deleted, not left behind: the table holds only current lines.
    total_lines = len(db.scalars(select(RecipeIngredient)).all())
    assert total_lines == 2 + 3 + 2  # carrot cake + lasagna + new stir fry


def test_tag_changes_apply_without_full_download(db: Session, mealie: FakeMealie, household_id: int) -> None:
    """Tags come with the list, so they're refreshed on every sync."""
    run_sync(db, mealie, household_id)
    # Change tags but keep updatedAt (simulates a summary-only difference).
    mealie.recipes["stir-fry"]["tags"] = [{"name": "want-to-try", "slug": "want-to-try"}]

    run_sync(db, mealie, household_id)

    assert cached_recipes(db, household_id)["stir-fry"].tags == ["want-to-try"]


def test_full_refresh_redownloads_everything(db: Session, mealie: FakeMealie, household_id: int) -> None:
    """full_refresh=True re-copies ingredients for every recipe."""
    run_sync(db, mealie, household_id)

    result = run_sync(db, mealie, household_id, full_refresh=True)

    assert (result.updated, result.unchanged) == (3, 0)


def test_deleted_recipe_is_marked_removed_then_restored(db: Session, mealie: FakeMealie, household_id: int) -> None:
    """Removed recipes are kept but flagged; re-adding clears the flag."""
    run_sync(db, mealie, household_id)
    saved_carrot_cake = dict(mealie.recipes["carrot-cake"])
    mealie.delete_recipe("carrot-cake")

    removal_result = run_sync(db, mealie, household_id)

    assert removal_result.removed == 1
    carrot_cake = cached_recipes(db, household_id)["carrot-cake"]
    assert carrot_cake.removed_at is not None
    assert len(carrot_cake.ingredients) == 2  # history kept

    # A later sync doesn't count it as removed again.
    assert run_sync(db, mealie, household_id).removed == 0

    mealie.recipes["carrot-cake"] = saved_carrot_cake
    restore_result = run_sync(db, mealie, household_id)

    assert restore_result.restored == 1
    assert cached_recipes(db, household_id)["carrot-cake"].removed_at is None


# --- Failure safety and isolation ---


def test_mealie_failure_mid_sync_changes_nothing(db: Session, mealie: FakeMealie, household_id: int) -> None:
    """If Mealie dies part-way, the cache is exactly as before the run."""
    run_sync(db, mealie, household_id)
    synced_at_before = db.get(Household, household_id).recipes_synced_at
    mealie.put_recipe("stir-fry", [ingredient_line("tofu", "1 block tofu")])
    mealie.put_recipe("new-soup", [ingredient_line("leek", "2 leeks")])
    mealie.requests_seen.clear()
    mealie.fail_after_requests = 2  # list pages succeed, then Mealie dies

    with pytest.raises(MealieUnavailableError):
        run_sync(db, mealie, household_id)
    db.rollback()

    recipes = cached_recipes(db, household_id)
    assert sorted(recipes) == ["carrot-cake", "lasagna", "stir-fry"]  # no "new-soup"
    assert recipes["stir-fry"].ingredients[0].canonical_name == "rice noodle"  # old lines kept
    assert db.get(Household, household_id).recipes_synced_at == synced_at_before


def test_households_have_separate_caches(db: Session, mealie: FakeMealie, household_id: int) -> None:
    """Two households syncing the same Mealie get independent copies."""
    other_household = Household(name="Cabin", join_code=generate_join_code())
    db.add(other_household)
    db.commit()

    run_sync(db, mealie, household_id)
    run_sync(db, mealie, other_household.id)
    mealie.delete_recipe("lasagna")
    run_sync(db, mealie, household_id)  # only the first household re-syncs

    assert cached_recipes(db, household_id)["lasagna"].removed_at is not None
    assert cached_recipes(db, other_household.id)["lasagna"].removed_at is None
