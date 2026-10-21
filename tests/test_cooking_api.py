"""Tests for the cook-log and rating API, including the Phase 1 scenario:
two household members log cooks and rate the same recipe, and both see the
history (plan §8, Phase 1 acceptance, minus the Mealie write-back).
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
    """A fake Mealie with two recipes, wired into the app."""
    fake = FakeMealie()
    fake.put_recipe("lasagna", [ingredient_line("lasagna noodle", "12 noodles")])
    fake.put_recipe("soup", [ingredient_line("leek", "2 leeks")])
    app_under_test.dependency_overrides[get_mealie_client_factory] = lambda: (lambda _api_token: fake.client())
    return fake


@pytest.fixture
def shared_kitchen(client: TestClient, other_client: TestClient, fake_mealie: FakeMealie) -> dict:
    """Alice (client) and Bob (other_client) share a synced household."""
    client.post("/api/auth/signup", json={"username": "alice", "password": PASSWORD, "display_name": "Alice"})
    household = client.post("/api/households", json={"name": "Home"}).json()
    client.put(f"/api/households/{household['id']}/mealie", json={"api_token": "any"})
    client.post("/api/recipes/sync")

    other_client.post("/api/auth/signup", json={"username": "bob", "password": PASSWORD, "display_name": "Bob"})
    other_client.post("/api/households/join", json={"join_code": household["join_code"]})

    recipes = {recipe["slug"]: recipe for recipe in client.get("/api/recipes").json()["recipes"]}
    members = {member["username"]: member["id"] for member in
               client.get(f"/api/households/{household['id']}/members").json()}
    return {"household": household, "recipes": recipes, "members": members}


def iso_hours_ago(hours: float) -> str:
    """An ISO timestamp (with zone) some hours in the past."""
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


# --- The Phase 1 scenario ---


def test_two_members_log_and_rate_the_same_recipe(
    client: TestClient, other_client: TestClient, shared_kitchen: dict
) -> None:
    """Alice and Bob both cook and rate lasagna; both see the same history."""
    lasagna_id = shared_kitchen["recipes"]["lasagna"]["id"]

    # Alice cooked it yesterday and loved it; Bob cooked it today, liked it.
    alice_cook = client.post(f"/api/recipes/{lasagna_id}/cooks",
                             json={"cooked_at": iso_hours_ago(24), "stars": 5, "servings": 4})
    bob_cook = other_client.post(f"/api/recipes/{lasagna_id}/cooks",
                                 json={"stars": 3, "notes": "a bit salty"})
    assert alice_cook.status_code == bob_cook.status_code == 201

    # Both see the same log, newest first, with names attached.
    for viewer in (client, other_client):
        log = viewer.get("/api/cooks", params={"recipe_id": lasagna_id}).json()
        assert [(entry["cooked_by"]["display_name"], entry["notes"]) for entry in log] == [
            ("Bob", "a bit salty"), ("Alice", None),
        ]

    # Ratings: per person, averaged, each viewer sees their own.
    alice_view = client.get(f"/api/recipes/{lasagna_id}/ratings").json()
    bob_view = other_client.get(f"/api/recipes/{lasagna_id}/ratings").json()
    assert alice_view["average_stars"] == bob_view["average_stars"] == 4.0
    assert (alice_view["my_stars"], bob_view["my_stars"]) == (5, 3)
    assert [(entry["display_name"], entry["stars"]) for entry in alice_view["latest"]] == [
        ("Alice", 5), ("Bob", 3),
    ]

    # The library list summarises it.
    lasagna_row = next(recipe for recipe in client.get("/api/recipes").json()["recipes"]
                       if recipe["id"] == lasagna_id)
    assert (lasagna_row["cook_count"], lasagna_row["average_stars"], lasagna_row["my_stars"]) == (2, 4.0, 5)
    assert lasagna_row["last_cooked_at"] == bob_cook.json()["cooked_at"]


def test_rerating_replaces_for_scoring_but_keeps_history(
    client: TestClient, other_client: TestClient, shared_kitchen: dict
) -> None:
    """Bob rates soup without cooking it, then changes his mind."""
    soup_id = shared_kitchen["recipes"]["soup"]["id"]

    other_client.put(f"/api/recipes/{soup_id}/rating", json={"stars": 2})
    response = other_client.put(f"/api/recipes/{soup_id}/rating", json={"stars": 4})

    assert response.status_code == 200
    body = response.json()
    assert (body["my_stars"], body["average_stars"]) == (4, 4.0)
    assert [entry["stars"] for entry in body["history"]] == [4, 2]
    soup_row = next(recipe for recipe in client.get("/api/recipes").json()["recipes"] if recipe["id"] == soup_id)
    assert (soup_row["cook_count"], soup_row["my_stars"], soup_row["average_stars"]) == (0, None, 4.0)


# --- Logging details ---


def test_empty_body_means_cooked_now_by_me(client: TestClient, shared_kitchen: dict) -> None:
    """POST with {} logs a cook by the caller at the current time."""
    lasagna_id = shared_kitchen["recipes"]["lasagna"]["id"]

    entry = client.post(f"/api/recipes/{lasagna_id}/cooks", json={}).json()

    assert entry["cooked_by"]["display_name"] == "Alice"
    assert entry["logged_by"]["display_name"] == "Alice"
    cooked_at = datetime.fromisoformat(entry["cooked_at"].replace("Z", "+00:00"))
    assert abs(datetime.now(UTC) - cooked_at) < timedelta(minutes=1)


def test_log_on_behalf_of_another_member(client: TestClient, shared_kitchen: dict) -> None:
    """Alice records that Bob cooked."""
    lasagna_id = shared_kitchen["recipes"]["lasagna"]["id"]
    bob_id = shared_kitchen["members"]["bob"]

    entry = client.post(f"/api/recipes/{lasagna_id}/cooks", json={"cooked_by_user_id": bob_id}).json()

    assert (entry["cooked_by"]["display_name"], entry["logged_by"]["display_name"]) == ("Bob", "Alice")


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        ({"stars": 6}, 422),
        ({"stars": 0}, 422),
        ({"stars": 3.5}, 422),  # whole stars only
        ({"servings": 0}, 422),
        ({"cooked_at": "2026-10-20T19:00:00"}, 422),  # no timezone: ambiguous
        ({"cooked_at": "2999-01-01T00:00:00Z"}, 400),  # future
        ({"cooked_by_user_id": 99999}, 400),  # not a member
    ],
)
def test_log_cook_validation(client: TestClient, shared_kitchen: dict, payload: dict, expected_status: int) -> None:
    """Bad input is refused with a clear status, and nothing is logged."""
    lasagna_id = shared_kitchen["recipes"]["lasagna"]["id"]

    response = client.post(f"/api/recipes/{lasagna_id}/cooks", json=payload)

    assert response.status_code == expected_status, response.text
    assert client.get("/api/cooks").json() == []


# --- Editing and deleting ---


def test_patch_changes_only_sent_fields(client: TestClient, other_client: TestClient, shared_kitchen: dict) -> None:
    """Any member may edit; absent fields stay, null clears."""
    lasagna_id = shared_kitchen["recipes"]["lasagna"]["id"]
    entry = client.post(f"/api/recipes/{lasagna_id}/cooks", json={"servings": 4, "notes": "great"}).json()

    edited = other_client.patch(f"/api/cooks/{entry['id']}", json={"notes": None}).json()

    assert (edited["servings"], edited["notes"]) == (4, None)


def test_delete_entry(client: TestClient, other_client: TestClient, shared_kitchen: dict) -> None:
    """Any member may delete; the rating given with it remains."""
    lasagna_id = shared_kitchen["recipes"]["lasagna"]["id"]
    entry = client.post(f"/api/recipes/{lasagna_id}/cooks", json={"stars": 4}).json()

    assert other_client.delete(f"/api/cooks/{entry['id']}").status_code == 204

    assert client.get("/api/cooks").json() == []
    assert client.get(f"/api/recipes/{lasagna_id}/ratings").json()["my_stars"] == 4
    assert client.delete(f"/api/cooks/{entry['id']}").status_code == 404


# --- Household isolation ---


def test_outsiders_see_nothing_and_cannot_touch(
    app_under_test: FastAPI, client: TestClient, shared_kitchen: dict
) -> None:
    """Someone in a different household gets 404s and an empty log."""
    lasagna_id = shared_kitchen["recipes"]["lasagna"]["id"]
    entry = client.post(f"/api/recipes/{lasagna_id}/cooks", json={"stars": 5}).json()

    outsider = TestClient(app_under_test)
    outsider.post("/api/auth/signup", json={"username": "eve", "password": PASSWORD, "display_name": "Eve"})
    outsider.post("/api/households", json={"name": "Eve's"})

    assert outsider.get("/api/cooks").json() == []
    assert outsider.post(f"/api/recipes/{lasagna_id}/cooks", json={}).status_code == 404
    assert outsider.put(f"/api/recipes/{lasagna_id}/rating", json={"stars": 1}).status_code == 404
    assert outsider.get(f"/api/recipes/{lasagna_id}/ratings").status_code == 404
    assert outsider.patch(f"/api/cooks/{entry['id']}", json={"notes": "hacked"}).status_code == 404
    assert outsider.delete(f"/api/cooks/{entry['id']}").status_code == 404
    household_id = shared_kitchen["household"]["id"]
    assert outsider.get(f"/api/households/{household_id}/members").status_code == 403
    # Alice's data is untouched.
    assert client.get(f"/api/recipes/{lasagna_id}/ratings").json()["average_stars"] == 5.0


def test_members_route_lists_household(client: TestClient, shared_kitchen: dict) -> None:
    """Members are listed in join order with display names."""
    household_id = shared_kitchen["household"]["id"]

    members = client.get(f"/api/households/{household_id}/members").json()

    assert [member["display_name"] for member in members] == ["Alice", "Bob"]
