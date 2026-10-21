"""Tests for the recipe library API: sync, list, detail, scoping."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.mealie import get_mealie_client_factory
from tests.fake_mealie import FakeMealie, ingredient_line

PASSWORD = "kitchen-secret-42"


# --- Fixtures and helpers ---


@pytest.fixture
def fake_mealie(app_under_test: FastAPI) -> FakeMealie:
    """A fake Mealie with two recipes, wired into the app."""
    fake = FakeMealie()
    fake.put_recipe("carrot-cake", [
        ingredient_line("carrot", "3 carrots"),
        ingredient_line("walnut", "walnuts (optional)", note="optional"),
    ])
    fake.put_recipe("lasagna", [ingredient_line("lasagna noodle", "12 noodles")], tags=["want-to-try"])
    # Every household's client talks to this one fake, whatever its token.
    app_under_test.dependency_overrides[get_mealie_client_factory] = lambda: (lambda _api_token: fake.client())
    return fake


def signup_connected(client: TestClient, username: str) -> int:
    """Sign up, create a household, connect it to Mealie; return its ID."""
    client.post("/api/auth/signup", json={"username": username, "password": PASSWORD, "display_name": username})
    household_id = client.post("/api/households", json={"name": f"{username}'s"}).json()["id"]
    response = client.put(f"/api/households/{household_id}/mealie", json={"api_token": "any-token"})
    assert response.status_code == 200, response.text
    return household_id


def recipes_by_slug(client: TestClient, **params: object) -> dict[str, dict]:
    """GET /api/recipes and index the result by slug."""
    response = client.get("/api/recipes", params=params)
    assert response.status_code == 200, response.text
    return {recipe["slug"]: recipe for recipe in response.json()["recipes"]}


# --- Sync ---


def test_sync_then_list(client: TestClient, fake_mealie: FakeMealie) -> None:
    """Syncing fills the library; the list shows tags, flags, and counts."""
    signup_connected(client, "alice")

    sync_response = client.post("/api/recipes/sync")

    assert sync_response.status_code == 200
    assert sync_response.json()["added"] == 2
    listing = client.get("/api/recipes").json()
    assert listing["synced_at"] == sync_response.json()["finished_at"]
    recipes = {recipe["slug"]: recipe for recipe in listing["recipes"]}
    assert recipes["lasagna"]["want_to_try"] is True
    assert recipes["carrot-cake"]["want_to_try"] is False
    assert recipes["carrot-cake"]["ingredient_count"] == 2


def test_library_is_empty_before_first_sync(client: TestClient, fake_mealie: FakeMealie) -> None:
    """No sync yet: empty list and no sync time."""
    signup_connected(client, "alice")

    assert client.get("/api/recipes").json() == {"synced_at": None, "recipes": []}


def test_sync_needs_a_mealie_connection(client: TestClient, fake_mealie: FakeMealie) -> None:
    """An unconnected household gets 409 with a hint."""
    client.post("/api/auth/signup", json={"username": "alice", "password": PASSWORD, "display_name": "A"})
    client.post("/api/households", json={"name": "Home"})

    response = client.post("/api/recipes/sync")

    assert response.status_code == 409


def test_full_refresh_flag(client: TestClient, fake_mealie: FakeMealie) -> None:
    """?full_refresh=true re-downloads unchanged recipes."""
    signup_connected(client, "alice")
    client.post("/api/recipes/sync")

    assert client.post("/api/recipes/sync").json()["unchanged"] == 2
    assert client.post("/api/recipes/sync", params={"full_refresh": True}).json()["updated"] == 2


# --- Detail ---


def test_recipe_detail_lists_ingredients_in_order(client: TestClient, fake_mealie: FakeMealie) -> None:
    """The detail view returns every line with its interpretation."""
    signup_connected(client, "alice")
    client.post("/api/recipes/sync")
    carrot_cake_id = recipes_by_slug(client)["carrot-cake"]["id"]

    detail = client.get(f"/api/recipes/{carrot_cake_id}").json()

    assert [line["canonical_name"] for line in detail["ingredients"]] == ["carrot", "walnut"]
    assert [line["is_optional"] for line in detail["ingredients"]] == [False, True]


def test_unknown_recipe_is_404(client: TestClient, fake_mealie: FakeMealie) -> None:
    """A recipe ID that doesn't exist is 404."""
    signup_connected(client, "alice")

    assert client.get("/api/recipes/99999").status_code == 404


# --- Removed recipes ---


def test_removed_recipes_hidden_unless_asked(client: TestClient, fake_mealie: FakeMealie) -> None:
    """After deletion in Mealie, the recipe drops out of the default list."""
    signup_connected(client, "alice")
    client.post("/api/recipes/sync")
    fake_mealie.delete_recipe("carrot-cake")
    client.post("/api/recipes/sync")

    assert sorted(recipes_by_slug(client)) == ["lasagna"]
    with_removed = recipes_by_slug(client, include_removed=True)
    assert with_removed["carrot-cake"]["removed_at"] is not None


# --- Mealie down ---


def test_reads_work_while_mealie_is_down(client: TestClient, fake_mealie: FakeMealie) -> None:
    """The cached library still loads; only sync reports the outage."""
    signup_connected(client, "alice")
    client.post("/api/recipes/sync")
    lasagna_id = recipes_by_slug(client)["lasagna"]["id"]
    fake_mealie.requests_seen.clear()
    fake_mealie.fail_after_requests = 0  # every request now fails

    assert client.post("/api/recipes/sync").status_code == 502
    assert sorted(recipes_by_slug(client)) == ["carrot-cake", "lasagna"]
    assert client.get(f"/api/recipes/{lasagna_id}").status_code == 200


# --- Household scoping ---


def test_other_households_cannot_see_your_recipes(
    client: TestClient, other_client: TestClient, fake_mealie: FakeMealie
) -> None:
    """Bob's household has its own library; Alice's recipe IDs are 404 for him."""
    signup_connected(client, "alice")
    client.post("/api/recipes/sync")
    alice_lasagna_id = recipes_by_slug(client)["lasagna"]["id"]
    signup_connected(other_client, "bob")

    assert recipes_by_slug(other_client) == {}  # Bob hasn't synced
    assert other_client.get(f"/api/recipes/{alice_lasagna_id}").status_code == 404

    other_client.post("/api/recipes/sync")
    bob_lasagna_id = recipes_by_slug(other_client)["lasagna"]["id"]
    assert bob_lasagna_id != alice_lasagna_id  # separate copies


def test_recipe_routes_need_login_and_household(client: TestClient, fake_mealie: FakeMealie) -> None:
    """Anonymous: 401. Logged in without a household: 409."""
    assert client.get("/api/recipes").status_code == 401
    assert client.post("/api/recipes/sync").status_code == 401

    client.post("/api/auth/signup", json={"username": "alice", "password": PASSWORD, "display_name": "A"})
    assert client.get("/api/recipes").status_code == 409
