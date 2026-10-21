"""Tests for connecting households to Mealie and the live recipe list.

A fake Mealie stands in for the real one. Its behaviour depends on the
token: GOOD_TOKEN works, BAD_TOKEN is rejected, DOWN_TOKEN simulates
Mealie being unreachable.
"""

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.api.mealie import get_mealie_client_factory
from app.core.config import Settings
from app.core.crypto import SecretBox
from app.integrations.mealie_client import MealieClient
from app.main import create_app
from app.models import Household

PASSWORD = "kitchen-secret-42"
GOOD_TOKEN = "good-mealie-token"
BAD_TOKEN = "revoked-mealie-token"
DOWN_TOKEN = "mealie-is-down-token"


# --- Fake Mealie ---


def fake_mealie(request: httpx2.Request) -> httpx2.Response:
    """Answer like Mealie would, based on the bearer token sent."""
    api_token = request.headers["authorization"].removeprefix("Bearer ")
    if api_token == DOWN_TOKEN:
        raise httpx2.ConnectError("connection refused", request=request)
    if api_token != GOOD_TOKEN:
        return httpx2.Response(401, json={"detail": "Not authenticated"})

    if request.url.path == "/api/users/self":
        return httpx2.Response(200, json={
            "id": "user-1", "username": "Test", "admin": False,
            "household": "Family", "householdId": "household-1",
            "group": "Home", "groupId": "group-1",
        })
    if request.url.path == "/api/recipes":
        return httpx2.Response(200, json={
            "page": 1, "per_page": 100, "total": 2, "total_pages": 1,
            "items": [
                {"id": "r1", "slug": "carrot-cake", "name": "Carrot Cake", "householdId": "household-1", "tags": None},
                {"id": "r2", "slug": "lasagna", "name": "Lasagna", "householdId": "household-1",
                 "tags": [{"name": "Want to try", "slug": "want-to-try"}]},
            ],
        })
    return httpx2.Response(404)


def fake_client_factory() -> object:
    """Dependency override: Mealie clients that talk to fake_mealie."""

    def make_client(api_token: str) -> MealieClient:
        return MealieClient("http://mealie.test", api_token, transport=httpx2.MockTransport(fake_mealie))

    return make_client


@pytest.fixture(autouse=True)
def use_fake_mealie(app_under_test: FastAPI) -> None:
    """Every test in this file talks to the fake Mealie."""
    app_under_test.dependency_overrides[get_mealie_client_factory] = fake_client_factory


# --- Helpers ---


def signup_with_household(client: TestClient, username: str, household_name: str = "Home") -> int:
    """Sign up, create a household, and return its ID."""
    client.post("/api/auth/signup", json={"username": username, "password": PASSWORD, "display_name": username})
    return client.post("/api/households", json={"name": household_name}).json()["id"]


def connect(client: TestClient, household_id: int, api_token: str) -> httpx2.Response:
    """PUT a Mealie token for a household."""
    return client.put(f"/api/households/{household_id}/mealie", json={"api_token": api_token})


# --- Connecting ---


def test_new_household_is_not_connected(client: TestClient) -> None:
    """Before a token is set, the status says so."""
    household_id = signup_with_household(client, "alice")

    response = client.get(f"/api/households/{household_id}/mealie")

    assert response.status_code == 200
    assert response.json()["connected"] is False


def test_connect_with_good_token(client: TestClient) -> None:
    """A working token is accepted; the status names the Mealie user/household."""
    household_id = signup_with_household(client, "alice")

    response = connect(client, household_id, f"  {GOOD_TOKEN}  ")  # whitespace from copy-paste

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert (body["mealie_username"], body["mealie_household_name"]) == ("Test", "Family")
    assert GOOD_TOKEN not in response.text  # token is never echoed back
    assert client.get(f"/api/households/{household_id}/mealie").json() == body


def test_token_is_stored_encrypted(client: TestClient, app_under_test: FastAPI) -> None:
    """The database holds ciphertext, never the token itself."""
    household_id = signup_with_household(client, "alice")
    connect(client, household_id, GOOD_TOKEN)

    with app_under_test.state.session_factory() as db:
        stored_value = db.get(Household, household_id).mealie_token_encrypted

    assert stored_value and GOOD_TOKEN not in stored_value


def test_rejected_token_is_not_saved(client: TestClient) -> None:
    """A token Mealie refuses gives 400 and leaves the household unconnected."""
    household_id = signup_with_household(client, "alice")

    response = connect(client, household_id, BAD_TOKEN)

    assert response.status_code == 400
    assert client.get(f"/api/households/{household_id}/mealie").json()["connected"] is False


def test_mealie_down_while_connecting(client: TestClient) -> None:
    """If Mealie is unreachable, the answer is 502 and nothing is saved."""
    household_id = signup_with_household(client, "alice")

    response = connect(client, household_id, DOWN_TOKEN)

    assert response.status_code == 502
    assert client.get(f"/api/households/{household_id}/mealie").json()["connected"] is False


def test_disconnect_forgets_the_token(client: TestClient) -> None:
    """After disconnecting, the household is unconnected again."""
    household_id = signup_with_household(client, "alice")
    connect(client, household_id, GOOD_TOKEN)

    assert client.delete(f"/api/households/{household_id}/mealie").status_code == 204

    status_body = client.get(f"/api/households/{household_id}/mealie").json()
    assert status_body == {"connected": False, "mealie_username": None, "mealie_household_name": None,
                           "connected_at": None}


def test_outsiders_cannot_view_set_or_remove_the_link(client: TestClient, other_client: TestClient) -> None:
    """Non-members get 403 on every connection route."""
    household_id = signup_with_household(client, "alice")
    connect(client, household_id, GOOD_TOKEN)
    signup_with_household(other_client, "bob", household_name="Bob's")

    assert other_client.get(f"/api/households/{household_id}/mealie").status_code == 403
    assert connect(other_client, household_id, GOOD_TOKEN).status_code == 403
    assert other_client.delete(f"/api/households/{household_id}/mealie").status_code == 403
    # Alice's link is untouched.
    assert client.get(f"/api/households/{household_id}/mealie").json()["connected"] is True


# --- Live recipe list ---


def test_live_recipes_for_connected_household(client: TestClient) -> None:
    """A connected household sees its Mealie recipes, tags included."""
    household_id = signup_with_household(client, "alice")
    connect(client, household_id, GOOD_TOKEN)

    response = client.get("/api/mealie/recipes")

    assert response.status_code == 200
    assert response.json() == [
        {"slug": "carrot-cake", "name": "Carrot Cake", "tags": []},
        {"slug": "lasagna", "name": "Lasagna", "tags": ["want-to-try"]},
    ]


def test_live_recipes_need_a_connection(client: TestClient) -> None:
    """Unconnected household: 409 with a hint to connect."""
    signup_with_household(client, "alice")

    response = client.get("/api/mealie/recipes")

    assert response.status_code == 409
    assert "connected" in response.json()["detail"]


def test_live_recipes_use_the_active_households_token(client: TestClient) -> None:
    """Switching households switches which token (and so which Mealie view) is used."""
    connected_household_id = signup_with_household(client, "alice", household_name="Connected")
    connect(client, connected_household_id, GOOD_TOKEN)
    client.post("/api/households", json={"name": "Not connected"})  # becomes active

    assert client.get("/api/mealie/recipes").status_code == 409

    client.post(f"/api/households/{connected_household_id}/switch")
    assert client.get("/api/mealie/recipes").status_code == 200


def test_revoked_token_asks_to_reconnect(
    client: TestClient, app_under_test: FastAPI, test_settings: Settings
) -> None:
    """If Mealie later rejects the saved token, the user is told to reconnect."""
    household_id = signup_with_household(client, "alice")
    connect(client, household_id, GOOD_TOKEN)

    # Simulate the token being revoked in Mealie: swap in (properly
    # encrypted) a token the fake Mealie rejects.
    secret_box = SecretBox(test_settings.chef_secret_key.get_secret_value())
    with app_under_test.state.session_factory() as db:
        db.get(Household, household_id).mealie_token_encrypted = secret_box.encrypt(BAD_TOKEN)
        db.commit()

    response = client.get("/api/mealie/recipes")

    assert response.status_code == 409
    assert "reconnect" in response.json()["detail"]


def test_changed_server_key_asks_to_reconnect(client: TestClient, app_under_test: FastAPI) -> None:
    """Ciphertext the current key can't read is treated as 'please reconnect'."""
    household_id = signup_with_household(client, "alice")
    connect(client, household_id, GOOD_TOKEN)
    with app_under_test.state.session_factory() as db:
        db.execute(update(Household).values(mealie_token_encrypted="not-valid-ciphertext"))
        db.commit()

    response = client.get("/api/mealie/recipes")

    assert response.status_code == 409
    assert "reconnect" in response.json()["detail"].lower()


# --- Server without CHEF_SECRET_KEY ---


def test_connecting_without_server_key_is_503(test_settings: Settings) -> None:
    """If the server has no key, connecting explains the problem (and the app still runs)."""
    keyless_settings = test_settings.model_copy(update={"chef_secret_key": None})
    keyless_app = create_app(keyless_settings)
    keyless_app.dependency_overrides[get_mealie_client_factory] = fake_client_factory

    with TestClient(keyless_app) as keyless_client:
        household_id = signup_with_household(keyless_client, "alice")
        response = connect(keyless_client, household_id, GOOD_TOKEN)
        health = keyless_client.get("/healthz")

    assert response.status_code == 503
    assert "CHEF_SECRET_KEY" in response.json()["detail"]
    assert health.status_code == 200
