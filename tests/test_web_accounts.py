"""Tests for the account and household web pages."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.mealie import get_mealie_client_factory
from tests.fake_mealie import FakeMealie

PASSWORD = "kitchen-secret-42"


# --- Helpers ---


def web_signup(client: TestClient, username: str, display_name: str | None = None) -> None:
    """Sign up through the web form (logs in)."""
    response = client.post("/signup", data={"display_name": display_name or username.title(),
                                            "username": username, "password": PASSWORD},
                           follow_redirects=False)
    assert response.status_code == 303, response.text


def web_create_household(client: TestClient, name: str = "Home") -> None:
    """Create a household through the web form."""
    response = client.post("/households/create", data={"name": name}, follow_redirects=False)
    assert response.status_code == 303


# --- Logged-out behaviour ---


def test_pages_redirect_to_login_with_return_path(client: TestClient) -> None:
    """Visiting a page while logged out sends you to log in, then back."""
    response = client.get("/household/settings", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/household/settings"


def test_htmx_requests_get_hx_redirect(client: TestClient) -> None:
    """htmx fragments can't follow a 303 into a full page; they get HX-Redirect."""
    response = client.get("/households", headers={"HX-Request": "true"}, follow_redirects=False)

    assert response.status_code == 200
    assert response.headers["HX-Redirect"].startswith("/login")


def test_static_assets_are_served(client: TestClient) -> None:
    """Pico and htmx are served locally (no internet needed)."""
    assert client.get("/static/vendor/pico-2.0.6.min.css").status_code == 200
    assert client.get("/static/vendor/htmx-2.0.4.min.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


# --- Sign up / log in / log out ---


def test_signup_then_home_goes_to_household_picker(client: TestClient) -> None:
    """A new user without a household lands on the household page."""
    web_signup(client, "alice")

    response = client.get("/")

    assert response.url.path == "/households"
    assert "not in a household yet" in response.text


@pytest.mark.parametrize(
    ("form_data", "expected_message"),
    [
        ({"username": "a b", "password": PASSWORD, "display_name": "A"}, "Username must be"),
        ({"username": "alice", "password": "short", "display_name": "A"}, "Password must be"),
        ({"username": "alice", "password": PASSWORD, "display_name": "   "}, "Please enter your name"),
    ],
)
def test_signup_errors_are_shown(client: TestClient, form_data: dict, expected_message: str) -> None:
    """Bad sign-up input re-shows the form with a message."""
    response = client.post("/signup", data=form_data)

    assert response.status_code == 400
    assert expected_message in response.text


def test_duplicate_username_is_shown(client: TestClient, other_client: TestClient) -> None:
    """Taking an existing username shows a clear message."""
    web_signup(client, "alice")

    response = other_client.post("/signup", data={"username": "Alice", "password": PASSWORD, "display_name": "X"})

    assert response.status_code == 400
    assert "taken" in response.text


def test_login_wrong_and_right(client: TestClient, other_client: TestClient) -> None:
    """Wrong password re-shows the form; right password goes to `next`."""
    web_signup(client, "alice")

    wrong = other_client.post("/login", data={"username": "alice", "password": "nope", "next": "/cooks"})
    assert wrong.status_code == 400
    assert "Wrong username or password" in wrong.text

    right = other_client.post("/login", data={"username": "Alice", "password": PASSWORD, "next": "/cooks"},
                              follow_redirects=False)
    assert (right.status_code, right.headers["location"]) == (303, "/cooks")


@pytest.mark.parametrize("unsafe_next", ["//evil.example/phish", "https://evil.example", "javascript:alert(1)"])
def test_login_ignores_offsite_next(client: TestClient, other_client: TestClient, unsafe_next: str) -> None:
    """`next` can only point inside this site."""
    web_signup(client, "alice")

    response = other_client.post("/login", data={"username": "alice", "password": PASSWORD, "next": unsafe_next},
                                 follow_redirects=False)

    assert response.headers["location"] == "/"


def test_logout(client: TestClient) -> None:
    """Logging out ends the session."""
    web_signup(client, "alice")

    client.post("/logout")

    assert client.get("/households", follow_redirects=False).status_code == 303


# --- Households ---


def test_create_household_opens_settings_with_join_code(client: TestClient) -> None:
    """Creating a household shows its settings, join code, and member list."""
    web_signup(client, "alice", display_name="Alice")

    page = client.post("/households/create", data={"name": "Luo Kitchen"})

    assert page.url.path == "/household/settings"
    assert "Luo Kitchen" in page.text
    join_code = client.get("/api/me").json()["households"][0]["join_code"]
    assert join_code in page.text
    assert "Alice" in page.text


def test_join_and_switch_households(client: TestClient, other_client: TestClient) -> None:
    """Bob joins Alice's household by code, then switches back to his own."""
    web_signup(client, "alice")
    web_create_household(client, "Alice's")
    alice_code = client.get("/api/me").json()["households"][0]["join_code"]
    web_signup(other_client, "bob")
    web_create_household(other_client, "Bob's")

    other_client.post("/households/join", data={"join_code": alice_code.lower()})
    assert other_client.get("/api/me").json()["households"][-1]["name"] == "Alice's"

    bob_household_id = other_client.get("/api/me").json()["households"][0]["id"]
    other_client.post(f"/households/{bob_household_id}/switch")
    assert other_client.get("/api/me").json()["active_household_id"] == bob_household_id


def test_wrong_join_code_message(client: TestClient) -> None:
    """A bad code shows a fixed error message."""
    web_signup(client, "alice")

    page = client.post("/households/join", data={"join_code": "ZZZZ-ZZZZ"})

    assert "No household has that join code" in page.text


def test_crafted_error_text_is_not_shown(client: TestClient) -> None:
    """Only known error codes produce messages; anything else shows nothing."""
    web_signup(client, "alice")

    page = client.get("/households", params={"error": "<b>Call 555-SCAM</b>"})

    assert "555-SCAM" not in page.text


def test_switching_to_a_strangers_household_is_refused(client: TestClient, other_client: TestClient) -> None:
    """You can't switch into a household you don't belong to."""
    web_signup(client, "alice")
    web_create_household(client)
    alice_household_id = client.get("/api/me").json()["active_household_id"]
    web_signup(other_client, "bob")

    page = other_client.post(f"/households/{alice_household_id}/switch")

    assert "not a member" in page.text
    assert other_client.get("/api/me").json()["active_household_id"] is None


def test_settings_needs_a_household(client: TestClient) -> None:
    """Without an active household, settings sends you to the picker."""
    web_signup(client, "alice")

    response = client.get("/household/settings", follow_redirects=False)

    assert response.headers["location"] == "/households"


# --- Mealie connection on the settings page ---


@pytest.fixture
def fake_mealie(app_under_test: FastAPI) -> FakeMealie:
    """A fake Mealie that accepts any token."""
    fake = FakeMealie()
    app_under_test.dependency_overrides[get_mealie_client_factory] = lambda: (lambda _api_token: fake.client())
    return fake


def test_connect_mealie_from_settings(client: TestClient, fake_mealie: FakeMealie) -> None:
    """Pasting a token connects; the page shows who it's connected as."""
    web_signup(client, "alice")
    web_create_household(client)

    page = client.post("/household/mealie", data={"api_token": "  a-token  "})

    assert "Connected to Mealie" in page.text
    assert "Connected as" in page.text and "Family" in page.text
    assert "a-token" not in page.text  # token never shown back

    page = client.post("/household/mealie/disconnect")
    assert "Not connected" in page.text


def test_regenerate_join_code_from_settings(client: TestClient) -> None:
    """A new code replaces the old one on the page."""
    web_signup(client, "alice")
    web_create_household(client)
    old_code = client.get("/api/me").json()["households"][0]["join_code"]

    page = client.post("/household/join-code")

    new_code = client.get("/api/me").json()["households"][0]["join_code"]
    assert new_code != old_code
    assert new_code in page.text and old_code not in page.text
