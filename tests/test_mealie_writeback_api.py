"""Tests for writing the cook log back to Mealie (Phase 1: "Mealie last made updated").

Uses the fake Mealie, which records "last made" per recipe and tag edits.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.mealie import get_mealie_client_factory
from tests.fake_mealie import FakeMealie, ingredient_line

PASSWORD = "kitchen-secret-42"


# --- Fixtures and helpers ---


@pytest.fixture
def fake_mealie(app_under_test: FastAPI) -> FakeMealie:
    """Lasagna (tagged want-to-try) and soup, wired into the app."""
    fake = FakeMealie()
    fake.put_recipe("lasagna", [ingredient_line("lasagna noodle", "12 noodles")], tags=["want-to-try", "dinner"])
    fake.put_recipe("soup", [ingredient_line("leek", "2 leeks")])
    app_under_test.dependency_overrides[get_mealie_client_factory] = lambda: (lambda _api_token: fake.client())
    return fake


@pytest.fixture
def recipe_ids(client: TestClient, fake_mealie: FakeMealie) -> dict[str, int]:
    """Alice's connected, synced household; returns recipe IDs by slug."""
    client.post("/api/auth/signup", json={"username": "alice", "password": PASSWORD, "display_name": "Alice"})
    household = client.post("/api/households", json={"name": "Home"}).json()
    client.put(f"/api/households/{household['id']}/mealie", json={"api_token": "any"})
    client.post("/api/recipes/sync")
    return {recipe["slug"]: recipe["id"] for recipe in client.get("/api/recipes").json()["recipes"]}


def parse_time(text: str) -> datetime:
    """Parse an ISO timestamp from the API or the fake Mealie."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def hours_ago(hours: float) -> datetime:
    """An aware UTC time some hours in the past."""
    return datetime.now(UTC) - timedelta(hours=hours)


def recipe_row(client: TestClient, recipe_id: int) -> dict:
    """The recipe's entry in the library list."""
    return next(recipe for recipe in client.get("/api/recipes").json()["recipes"] if recipe["id"] == recipe_id)


def log_cook(client: TestClient, recipe_id: int, cooked_at: datetime | None = None) -> dict:
    """Log a cook and return the response body."""
    payload = {"cooked_at": cooked_at.isoformat()} if cooked_at else {}
    response = client.post(f"/api/recipes/{recipe_id}/cooks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --- Last made ---


def test_logging_a_cook_updates_mealie_last_made(
    client: TestClient, fake_mealie: FakeMealie, recipe_ids: dict[str, int]
) -> None:
    """Right after logging, Mealie's last made equals the cook time."""
    entry = log_cook(client, recipe_ids["soup"], hours_ago(2))

    assert entry["mealie_writeback"] == "up_to_date"
    assert parse_time(fake_mealie.last_made["soup"]) == parse_time(entry["cooked_at"])
    assert recipe_row(client, recipe_ids["soup"])["mealie_writeback_pending"] is False


def test_backdated_older_cook_does_not_move_last_made_back(
    client: TestClient, fake_mealie: FakeMealie, recipe_ids: dict[str, int]
) -> None:
    """Logging an older cook later leaves Mealie on the newest one."""
    newest = log_cook(client, recipe_ids["soup"], hours_ago(1))
    writes_before = len(fake_mealie.writes_seen)

    log_cook(client, recipe_ids["soup"], hours_ago(72))

    assert parse_time(fake_mealie.last_made["soup"]) == parse_time(newest["cooked_at"])
    assert len(fake_mealie.writes_seen) == writes_before  # nothing needed sending


def test_deleting_latest_cook_moves_last_made_back(
    client: TestClient, fake_mealie: FakeMealie, recipe_ids: dict[str, int]
) -> None:
    """Remove a mistaken entry and Mealie returns to the previous cook."""
    earlier = log_cook(client, recipe_ids["soup"], hours_ago(48))
    mistaken = log_cook(client, recipe_ids["soup"], hours_ago(1))

    assert client.delete(f"/api/cooks/{mistaken['id']}").status_code == 204

    assert parse_time(fake_mealie.last_made["soup"]) == parse_time(earlier["cooked_at"])


def test_editing_cook_time_updates_last_made(
    client: TestClient, fake_mealie: FakeMealie, recipe_ids: dict[str, int]
) -> None:
    """Correcting the time of the latest cook is reflected in Mealie."""
    entry = log_cook(client, recipe_ids["soup"], hours_ago(5))
    corrected_time = hours_ago(3)

    edited = client.patch(f"/api/cooks/{entry['id']}", json={"cooked_at": corrected_time.isoformat()}).json()

    assert edited["mealie_writeback"] == "up_to_date"
    assert parse_time(fake_mealie.last_made["soup"]) == corrected_time


# --- Want-to-try tag ---


def test_first_cook_clears_want_to_try_in_mealie_and_locally(
    client: TestClient, fake_mealie: FakeMealie, recipe_ids: dict[str, int]
) -> None:
    """After the first cook, the tag is gone in both places; other tags stay."""
    assert recipe_row(client, recipe_ids["lasagna"])["want_to_try"] is True

    log_cook(client, recipe_ids["lasagna"])

    mealie_tag_slugs = [tag["slug"] for tag in fake_mealie.recipes["lasagna"]["tags"]]
    assert mealie_tag_slugs == ["dinner"]
    lasagna = recipe_row(client, recipe_ids["lasagna"])
    assert (lasagna["want_to_try"], lasagna["tags"]) == (False, ["dinner"])


def test_want_to_try_readded_by_hand_is_left_alone(
    client: TestClient, fake_mealie: FakeMealie, recipe_ids: dict[str, int]
) -> None:
    """Chef Helper clears the tag once; if someone re-adds it, it stays."""
    log_cook(client, recipe_ids["lasagna"], hours_ago(48))
    # Someone re-tags it in Mealie; the next sync picks that up.
    fake_mealie.recipes["lasagna"]["tags"] = [{"name": "want-to-try", "slug": "want-to-try"}]
    client.post("/api/recipes/sync")

    log_cook(client, recipe_ids["lasagna"])
    client.post("/api/recipes/sync")

    assert [tag["slug"] for tag in fake_mealie.recipes["lasagna"]["tags"]] == ["want-to-try"]


# --- Mealie unavailable or not connected ---


def test_mealie_down_leaves_writeback_pending_until_sync(
    client: TestClient, fake_mealie: FakeMealie, recipe_ids: dict[str, int]
) -> None:
    """The cook is saved regardless; the next sync delivers it to Mealie."""
    fake_mealie.fail_after_requests = len(fake_mealie.requests_seen)  # Mealie goes down now

    entry = log_cook(client, recipe_ids["soup"], hours_ago(1))

    assert entry["mealie_writeback"] == "pending"
    assert len(client.get("/api/cooks").json()) == 1  # saved locally
    assert recipe_row(client, recipe_ids["soup"])["mealie_writeback_pending"] is True
    assert "soup" not in fake_mealie.last_made

    fake_mealie.fail_after_requests = None  # Mealie is back
    sync_body = client.post("/api/recipes/sync").json()

    assert (sync_body["last_made_updated"], sync_body["writeback_pending"]) == (1, 0)
    assert parse_time(fake_mealie.last_made["soup"]) == parse_time(entry["cooked_at"])
    assert recipe_row(client, recipe_ids["soup"])["mealie_writeback_pending"] is False


def test_unconnected_household_reports_not_connected(
    client: TestClient, fake_mealie: FakeMealie, recipe_ids: dict[str, int]
) -> None:
    """After disconnecting Mealie, cooks still log, with status not_connected."""
    household_id = client.get("/api/me").json()["active_household_id"]
    client.delete(f"/api/households/{household_id}/mealie")

    entry = log_cook(client, recipe_ids["soup"])

    assert entry["mealie_writeback"] == "not_connected"
    assert recipe_row(client, recipe_ids["soup"])["mealie_writeback_pending"] is True


def test_recipe_deleted_in_mealie_does_not_break_logging(
    client: TestClient, fake_mealie: FakeMealie, recipe_ids: dict[str, int]
) -> None:
    """If the recipe vanished from Mealie since the last sync, the cook still saves."""
    fake_mealie.delete_recipe("soup")

    response = client.post(f"/api/recipes/{recipe_ids['soup']}/cooks", json={})

    assert response.status_code == 201
    client.post("/api/recipes/sync")  # now marked removed
    all_recipes = client.get("/api/recipes", params={"include_removed": True}).json()["recipes"]
    soup = next(recipe for recipe in all_recipes if recipe["slug"] == "soup")
    assert soup["removed_at"] is not None
    assert soup["mealie_writeback_pending"] is False  # removed recipes aren't retried
