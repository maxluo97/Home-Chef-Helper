"""Tests for sign-up, login sessions, households, and household scoping."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.api.deps import SESSION_COOKIE_NAME, ActiveHouseholdId
from app.models import HouseholdMembership, LoginSession
from app.models.base import utc_now

PASSWORD = "kitchen-secret-42"


# --- Helpers ---


def signup(client: TestClient, username: str, display_name: str | None = None) -> dict:
    """Sign up (which also logs in) and return the response body."""
    response = client.post(
        "/api/auth/signup",
        json={"username": username, "password": PASSWORD, "display_name": display_name or username.title()},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_household(client: TestClient, name: str) -> dict:
    """Create a household as the client's user and return it."""
    response = client.post("/api/households", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def me(client: TestClient) -> dict:
    """GET /api/me, asserting success."""
    response = client.get("/api/me")
    assert response.status_code == 200, response.text
    return response.json()


# --- Sign-up ---


def test_signup_logs_in_and_starts_with_no_household(client: TestClient) -> None:
    """A new account is logged in right away and has no households yet."""
    body = signup(client, "  Alice ", display_name="Alice A.")

    assert body["user"]["username"] == "alice"  # trimmed + lower-cased
    assert body["user"]["display_name"] == "Alice A."
    assert body["households"] == []
    assert body["active_household_id"] is None
    assert me(client)["user"]["username"] == "alice"


def test_signup_response_never_contains_password_data(client: TestClient) -> None:
    """Neither the password nor its hash is ever sent back."""
    response_text = client.post(
        "/api/auth/signup",
        json={"username": "alice", "password": PASSWORD, "display_name": "Alice"},
    ).text

    assert PASSWORD not in response_text
    assert "password" not in response_text
    assert "argon2" not in response_text


def test_login_cookie_is_httponly_and_samesite(client: TestClient) -> None:
    """The session cookie can't be read by page scripts or sent cross-site."""
    response = client.post(
        "/api/auth/signup",
        json={"username": "alice", "password": PASSWORD, "display_name": "Alice"},
    )
    set_cookie_header = response.headers["set-cookie"].lower()

    assert set_cookie_header.startswith(f"{SESSION_COOKIE_NAME}=")
    assert "httponly" in set_cookie_header
    assert "samesite=lax" in set_cookie_header


def test_duplicate_username_is_rejected_case_insensitively(client: TestClient, other_client: TestClient) -> None:
    """'Alice' can't sign up once 'alice' exists."""
    signup(client, "alice")

    response = other_client.post(
        "/api/auth/signup",
        json={"username": "ALICE", "password": PASSWORD, "display_name": "Imposter"},
    )

    assert response.status_code == 409


@pytest.mark.parametrize(
    "bad_fields",
    [
        {"username": "a b"},  # space not allowed
        {"username": "ab"},  # too short
        {"password": "short"},  # under 8 characters
        {"display_name": "   "},  # blank after trimming
    ],
)
def test_signup_validation_errors(client: TestClient, bad_fields: dict) -> None:
    """Invalid sign-up fields get a 422 and no account is created."""
    payload = {"username": "alice", "password": PASSWORD, "display_name": "Alice"} | bad_fields

    response = client.post("/api/auth/signup", json=payload)

    assert response.status_code == 422
    assert client.get("/api/me").status_code == 401


# --- Login / logout ---


def test_login_with_right_password(client: TestClient, other_client: TestClient) -> None:
    """Correct credentials (any letter case in the username) log in."""
    signup(client, "alice")

    response = other_client.post("/api/auth/login", json={"username": "Alice", "password": PASSWORD})

    assert response.status_code == 200
    assert me(other_client)["user"]["username"] == "alice"


@pytest.mark.parametrize(
    ("username", "password"),
    [("alice", "wrong-password"), ("nobody", PASSWORD), ("bad name!", PASSWORD)],
)
def test_failed_logins_all_look_the_same(
    client: TestClient, other_client: TestClient, username: str, password: str
) -> None:
    """Wrong password, unknown user, and malformed name give one 401 message."""
    signup(client, "alice")

    response = other_client.post("/api/auth/login", json={"username": username, "password": password})

    assert response.status_code == 401
    assert response.json()["detail"] == "Wrong username or password."
    assert other_client.get("/api/me").status_code == 401


def test_me_requires_login(client: TestClient) -> None:
    """Without a session cookie, /api/me is 401."""
    assert client.get("/api/me").status_code == 401


def test_forged_cookie_is_rejected(client: TestClient) -> None:
    """A made-up token doesn't log anyone in."""
    signup(client, "alice")
    # Replace (not add to) the real cookie. Setting without clearing would
    # leave the real one in the jar too, and both would be sent.
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, "made-up-token")

    assert client.get("/api/me").status_code == 401


def test_logout_ends_only_this_devices_session(client: TestClient, other_client: TestClient) -> None:
    """Logging out on one device leaves the other logged in."""
    signup(client, "alice")
    other_client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})

    assert client.post("/api/auth/logout").status_code == 204

    assert client.get("/api/me").status_code == 401
    assert me(other_client)["user"]["username"] == "alice"
    # Logging out again (already logged out) is harmless.
    assert client.post("/api/auth/logout").status_code == 204


def test_expired_session_is_rejected_and_removed(client: TestClient, app_under_test: FastAPI) -> None:
    """Once expires_at passes, the cookie stops working and the row is deleted."""
    signup(client, "alice")
    session_factory = app_under_test.state.session_factory

    # Push this session's expiry into the past.
    with session_factory() as db:
        db.execute(update(LoginSession).values(expires_at=utc_now() - timedelta(seconds=1)))
        db.commit()

    assert client.get("/api/me").status_code == 401
    with session_factory() as db:
        assert db.scalars(select(LoginSession)).all() == []


# --- Households ---


def test_create_household_joins_and_activates_it(client: TestClient) -> None:
    """The creator becomes a member and the new household becomes active."""
    signup(client, "alice")

    household = create_household(client, "  Luo Kitchen ")

    assert household["name"] == "Luo Kitchen"
    assert len(household["join_code"]) == 9  # XXXX-XXXX
    profile = me(client)
    assert profile["households"] == [household]
    assert profile["active_household_id"] == household["id"]


def test_join_by_code_forgives_case_and_spacing(client: TestClient, other_client: TestClient) -> None:
    """Bob joins Alice's household by typing the code loosely."""
    signup(client, "alice")
    household = create_household(client, "Luo Kitchen")
    signup(other_client, "bob")
    sloppy_code = household["join_code"].lower().replace("-", " ")

    response = other_client.post("/api/households/join", json={"join_code": sloppy_code})

    assert response.status_code == 200
    assert response.json()["id"] == household["id"]
    assert me(other_client)["active_household_id"] == household["id"]


def test_joining_twice_does_not_duplicate_membership(
    client: TestClient, other_client: TestClient, app_under_test: FastAPI
) -> None:
    """Re-using a code for a household you're in just switches to it."""
    signup(client, "alice")
    household = create_household(client, "Luo Kitchen")
    signup(other_client, "bob")

    for _iJoin in range(2):
        other_client.post("/api/households/join", json={"join_code": household["join_code"]})

    with app_under_test.state.session_factory() as db:
        memberships = db.scalars(
            select(HouseholdMembership).where(HouseholdMembership.household_id == household["id"])
        ).all()
    assert len(memberships) == 2  # Alice + Bob, once each


def test_wrong_join_code_is_404(client: TestClient) -> None:
    """A code that matches no household is refused."""
    signup(client, "alice")

    response = client.post("/api/households/join", json={"join_code": "ZZZZ-ZZZZ"})

    assert response.status_code == 404


def test_households_require_login(client: TestClient) -> None:
    """Household routes are closed to anonymous callers."""
    assert client.get("/api/households").status_code == 401
    assert client.post("/api/households", json={"name": "X"}).status_code == 401
    assert client.post("/api/households/join", json={"join_code": "ABCD-EFGH"}).status_code == 401


# --- Several households per person ---


def test_user_can_belong_to_and_switch_between_households(client: TestClient) -> None:
    """One person in two households switches the active one back and forth."""
    signup(client, "alice")
    home = create_household(client, "Home")
    cabin = create_household(client, "Cabin")
    assert me(client)["active_household_id"] == cabin["id"]  # newest is active

    assert client.post(f"/api/households/{home['id']}/switch").status_code == 204

    profile = me(client)
    assert profile["active_household_id"] == home["id"]
    assert [household["id"] for household in profile["households"]] == [home["id"], cabin["id"]]


def test_new_login_starts_on_first_joined_household(client: TestClient, other_client: TestClient) -> None:
    """A fresh device lands on the household the user joined first."""
    signup(client, "alice")
    home = create_household(client, "Home")
    create_household(client, "Cabin")

    other_client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})

    assert me(other_client)["active_household_id"] == home["id"]


def test_active_household_is_per_device(client: TestClient, other_client: TestClient) -> None:
    """Switching on the laptop doesn't change what the phone shows."""
    signup(client, "alice")
    home = create_household(client, "Home")
    cabin = create_household(client, "Cabin")
    other_client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})

    client.post(f"/api/households/{home['id']}/switch")
    other_client.post(f"/api/households/{cabin['id']}/switch")

    assert me(client)["active_household_id"] == home["id"]
    assert me(other_client)["active_household_id"] == cabin["id"]


# --- Isolation between households ---


def test_outsiders_cannot_see_switch_to_or_recode_a_household(
    client: TestClient, other_client: TestClient
) -> None:
    """Bob, not a member, gets nothing about Alice's household."""
    signup(client, "alice")
    alice_household = create_household(client, "Alice's")
    signup(other_client, "bob")

    assert other_client.get("/api/households").json() == []
    assert other_client.post(f"/api/households/{alice_household['id']}/switch").status_code == 403
    assert other_client.post(f"/api/households/{alice_household['id']}/join-code").status_code == 403
    # A household ID that doesn't exist gets the same answer (no ID probing).
    assert other_client.post("/api/households/99999/switch").status_code == 403


def test_regenerated_join_code_replaces_the_old_one(client: TestClient, other_client: TestClient) -> None:
    """After regenerating, the old code fails and the new one works."""
    signup(client, "alice")
    household = create_household(client, "Luo Kitchen")
    old_code = household["join_code"]

    new_code = client.post(f"/api/households/{household['id']}/join-code").json()["join_code"]

    assert new_code != old_code
    signup(other_client, "bob")
    assert other_client.post("/api/households/join", json={"join_code": old_code}).status_code == 404
    assert other_client.post("/api/households/join", json={"join_code": new_code}).status_code == 200


# --- ActiveHouseholdId dependency (what future household-scoped routes use) ---


@pytest.fixture
def scoped_route_app(app_under_test: FastAPI) -> FastAPI:
    """Add a stand-in household-scoped route to the app under test."""

    @app_under_test.get("/test/active-household")
    def read_active_household(household_id: ActiveHouseholdId) -> dict:
        return {"household_id": household_id}

    return app_under_test


def test_scoped_route_needs_a_selected_household(scoped_route_app: FastAPI, client: TestClient) -> None:
    """Anonymous: 401. Logged in, no household: 409. After creating one: its ID."""
    assert client.get("/test/active-household").status_code == 401

    signup(client, "alice")
    assert client.get("/test/active-household").status_code == 409

    household = create_household(client, "Home")
    response = client.get("/test/active-household")
    assert response.status_code == 200
    assert response.json() == {"household_id": household["id"]}


def test_scoped_route_rejects_a_household_you_left(
    scoped_route_app: FastAPI, client: TestClient, app_under_test: FastAPI
) -> None:
    """If membership disappears, the stale active household gives 403."""
    signup(client, "alice")
    create_household(client, "Home")

    # Remove the membership directly (there's no "leave" feature yet).
    with app_under_test.state.session_factory() as db:
        db.query(HouseholdMembership).delete()
        db.commit()

    assert client.get("/test/active-household").status_code == 403
