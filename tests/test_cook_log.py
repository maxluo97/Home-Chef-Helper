"""Tests for the cook-log and rating service (no HTTP)."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import make_engine, make_session_factory
from app.core.migrate import upgrade_to_head
from app.core.security import generate_join_code
from app.models import CookEvent, Household, HouseholdMembership, Rating, Recipe, User
from app.models.base import utc_now
from app.services import cook_log


@dataclass
class Kitchen:
    """IDs of the test household, its members, and its recipes."""

    household_id: int
    other_household_id: int
    alice_id: int
    bob_id: int
    outsider_id: int
    lasagna_id: int
    soup_id: int
    other_household_recipe_id: int


# --- Fixtures ---


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    """A session on a freshly migrated throwaway database."""
    database_url = f"sqlite:///{(tmp_path / 'cook.db').as_posix()}"
    upgrade_to_head(database_url)
    engine = make_engine(database_url)
    with make_session_factory(engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture
def kitchen(db: Session) -> Kitchen:
    """Alice and Bob share a household with two recipes; an outsider has another."""
    home = Household(name="Home", join_code=generate_join_code())
    elsewhere = Household(name="Elsewhere", join_code=generate_join_code())
    alice, bob, outsider = (
        User(username=username, password_hash="x", display_name=username.title())
        for username in ("alice", "bob", "outsider")
    )
    db.add_all([home, elsewhere, alice, bob, outsider])
    db.flush()
    db.add_all([
        HouseholdMembership(household_id=home.id, user_id=alice.id),
        HouseholdMembership(household_id=home.id, user_id=bob.id),
        HouseholdMembership(household_id=elsewhere.id, user_id=outsider.id),
    ])

    def make_recipe(household_id: int, slug: str) -> Recipe:
        return Recipe(household_id=household_id, mealie_recipe_id=f"m-{slug}-{household_id}", slug=slug,
                      name=slug.title(), tags=[], synced_at=utc_now())

    lasagna, soup, foreign_recipe = make_recipe(home.id, "lasagna"), make_recipe(home.id, "soup"), make_recipe(
        elsewhere.id, "stew")
    db.add_all([lasagna, soup, foreign_recipe])
    db.commit()
    return Kitchen(home.id, elsewhere.id, alice.id, bob.id, outsider.id, lasagna.id, soup.id, foreign_recipe.id)


# --- Logging cooks ---


def test_log_cook_defaults_to_now_and_logger(db: Session, kitchen: Kitchen) -> None:
    """Without details, it's cooked now, by whoever logs it."""
    before_logging = utc_now()

    cook_event = cook_log.log_cook(db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id)

    assert cook_event.cooked_by_user_id == kitchen.alice_id
    assert cook_event.logged_by_user_id == kitchen.alice_id
    assert before_logging <= cook_event.cooked_at <= utc_now()


def test_log_cook_for_someone_else_backdated(db: Session, kitchen: Kitchen) -> None:
    """Alice logs that Bob cooked two days ago, with servings and notes."""
    two_days_ago = utc_now() - timedelta(days=2)

    cook_event = cook_log.log_cook(
        db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id,
        cooked_at=two_days_ago, cooked_by_user_id=kitchen.bob_id, servings=4, notes="  extra garlic  ",
    )

    assert (cook_event.cooked_by_user_id, cook_event.logged_by_user_id) == (kitchen.bob_id, kitchen.alice_id)
    assert cook_event.cooked_at == two_days_ago
    assert (cook_event.servings, cook_event.notes) == (4, "extra garlic")


def test_log_cook_with_stars_rates_as_the_logger(db: Session, kitchen: Kitchen) -> None:
    """A rating given while logging is the logger's, linked to the cook."""
    cook_event = cook_log.log_cook(db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id, stars=5)

    stored_rating = db.scalars(select(Rating)).one()
    assert (stored_rating.user_id, stored_rating.stars, stored_rating.cook_event_id) == (
        kitchen.alice_id, 5, cook_event.id,
    )


def test_future_cook_time_is_refused(db: Session, kitchen: Kitchen) -> None:
    """Cooks can't be logged for tomorrow; slight clock drift is fine."""
    with pytest.raises(cook_log.InvalidCookTimeError):
        cook_log.log_cook(db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id,
                          cooked_at=utc_now() + timedelta(days=1))

    cook_log.log_cook(db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id,
                      cooked_at=utc_now() + timedelta(minutes=2))  # within tolerance


def test_cook_must_be_a_household_member(db: Session, kitchen: Kitchen) -> None:
    """You can't record an outsider as the cook."""
    with pytest.raises(cook_log.NotHouseholdMemberError):
        cook_log.log_cook(db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id,
                          cooked_by_user_id=kitchen.outsider_id)


def test_other_households_recipe_is_not_found(db: Session, kitchen: Kitchen) -> None:
    """A recipe ID from another household behaves like it doesn't exist."""
    with pytest.raises(cook_log.RecipeNotFoundError):
        cook_log.log_cook(db, kitchen.household_id, kitchen.other_household_recipe_id, kitchen.alice_id)
    with pytest.raises(cook_log.RecipeNotFoundError):
        cook_log.rate_recipe(db, kitchen.household_id, kitchen.other_household_recipe_id, kitchen.alice_id, 4)


def test_bad_stars_are_refused(db: Session, kitchen: Kitchen) -> None:
    """0 and 6 stars are rejected before touching the database."""
    for bad_stars in (0, 6):
        with pytest.raises(cook_log.InvalidStarsError):
            cook_log.rate_recipe(db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id, bad_stars)


# --- Editing and deleting ---


def test_update_changes_only_given_fields(db: Session, kitchen: Kitchen) -> None:
    """Fields not mentioned stay as they were; notes can be cleared."""
    cook_event = cook_log.log_cook(db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id,
                                   servings=2, notes="first try")

    cook_log.update_cook(db, kitchen.household_id, cook_event.id,
                         {"cooked_by_user_id": kitchen.bob_id, "notes": None})

    db.refresh(cook_event)
    assert (cook_event.cooked_by_user_id, cook_event.servings, cook_event.notes) == (kitchen.bob_id, 2, None)


def test_update_validates_like_logging(db: Session, kitchen: Kitchen) -> None:
    """Edits can't introduce outsiders, future times, or a missing time."""
    cook_event = cook_log.log_cook(db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id)

    with pytest.raises(cook_log.NotHouseholdMemberError):
        cook_log.update_cook(db, kitchen.household_id, cook_event.id, {"cooked_by_user_id": kitchen.outsider_id})
    with pytest.raises(cook_log.InvalidCookTimeError):
        cook_log.update_cook(db, kitchen.household_id, cook_event.id, {"cooked_at": utc_now() + timedelta(days=3)})
    with pytest.raises(cook_log.InvalidCookTimeError):
        cook_log.update_cook(db, kitchen.household_id, cook_event.id, {"cooked_at": None})


def test_delete_cook_keeps_its_rating(db: Session, kitchen: Kitchen) -> None:
    """Deleting a cook removes the entry but the opinion remains."""
    cook_event = cook_log.log_cook(db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id, stars=4)

    cook_log.delete_cook(db, kitchen.household_id, cook_event.id)

    assert db.scalars(select(CookEvent)).all() == []
    remaining_rating = db.scalars(select(Rating)).one()
    assert (remaining_rating.stars, remaining_rating.cook_event_id) == (4, None)


def test_cannot_edit_or_delete_another_households_cook(db: Session, kitchen: Kitchen) -> None:
    """Cook IDs from another household are not found."""
    foreign_cook = cook_log.log_cook(db, kitchen.other_household_id, kitchen.other_household_recipe_id,
                                     kitchen.outsider_id)

    with pytest.raises(cook_log.CookEventNotFoundError):
        cook_log.update_cook(db, kitchen.household_id, foreign_cook.id, {"servings": 9})
    with pytest.raises(cook_log.CookEventNotFoundError):
        cook_log.delete_cook(db, kitchen.household_id, foreign_cook.id)


# --- Listing ---


def test_list_cooks_newest_first_and_filtered(db: Session, kitchen: Kitchen) -> None:
    """The log is newest-first, can be filtered by recipe, and is household-only."""
    now = utc_now()
    for days_ago, recipe_id in [(3, kitchen.lasagna_id), (1, kitchen.soup_id), (2, kitchen.lasagna_id)]:
        cook_log.log_cook(db, kitchen.household_id, recipe_id, kitchen.alice_id,
                          cooked_at=now - timedelta(days=days_ago))
    cook_log.log_cook(db, kitchen.other_household_id, kitchen.other_household_recipe_id, kitchen.outsider_id)

    whole_log = cook_log.list_cooks(db, kitchen.household_id)
    lasagna_log = cook_log.list_cooks(db, kitchen.household_id, recipe_id=kitchen.lasagna_id)

    assert [cook_event.recipe_id for cook_event in whole_log] == [kitchen.soup_id, kitchen.lasagna_id,
                                                                 kitchen.lasagna_id]
    assert [cook_event.cooked_at for cook_event in lasagna_log] == [now - timedelta(days=2),
                                                                   now - timedelta(days=3)]


# --- Stats for the recipe list ---


def test_household_recipe_stats(db: Session, kitchen: Kitchen) -> None:
    """Counts, last cook (as aware UTC), and latest-wins averages per recipe."""
    now = utc_now()
    cook_log.log_cook(db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id,
                      cooked_at=now - timedelta(days=10), stars=2)
    cook_log.log_cook(db, kitchen.household_id, kitchen.lasagna_id, kitchen.bob_id,
                      cooked_at=now - timedelta(days=1))
    cook_log.rate_recipe(db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id, 5)  # replaces her 2
    cook_log.rate_recipe(db, kitchen.household_id, kitchen.lasagna_id, kitchen.bob_id, 4)
    cook_log.rate_recipe(db, kitchen.household_id, kitchen.soup_id, kitchen.bob_id, 3)  # rated, never cooked
    # Another household's activity must not leak in.
    cook_log.log_cook(db, kitchen.other_household_id, kitchen.other_household_recipe_id, kitchen.outsider_id,
                      stars=1)

    stats = cook_log.household_recipe_stats(db, kitchen.household_id)

    assert set(stats) == {kitchen.lasagna_id, kitchen.soup_id}
    lasagna_stats = stats[kitchen.lasagna_id]
    assert lasagna_stats.cook_count == 2
    assert lasagna_stats.last_cooked_at == now - timedelta(days=1)
    assert lasagna_stats.last_cooked_at.tzinfo == UTC
    assert lasagna_stats.average_stars == 4.5
    assert (stats[kitchen.soup_id].cook_count, stats[kitchen.soup_id].average_stars) == (0, 3.0)


def test_rating_history_is_kept(db: Session, kitchen: Kitchen) -> None:
    """Re-rating adds rows; the full history is returned newest first."""
    for stars in (3, 4, 2):
        cook_log.rate_recipe(db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id, stars)

    history = cook_log.recipe_ratings(db, kitchen.household_id, kitchen.lasagna_id)

    assert [stored_rating.stars for stored_rating in history] == [2, 4, 3]


def test_cook_time_round_trip_keeps_instant(db: Session, kitchen: Kitchen) -> None:
    """A cook time given in another zone is stored as the same instant."""
    eastern_evening = datetime(2026, 10, 20, 19, 0, tzinfo=UTC) - timedelta(hours=4)

    cook_event = cook_log.log_cook(db, kitchen.household_id, kitchen.lasagna_id, kitchen.alice_id,
                                   cooked_at=eastern_evening)
    db.expire_all()

    assert db.get(CookEvent, cook_event.id).cooked_at == eastern_evening
