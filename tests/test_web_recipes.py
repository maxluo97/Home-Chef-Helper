"""Tests for the recipe pages, recipe-page actions (htmx), and the cook log page."""

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.mealie import get_mealie_client_factory
from tests.fake_mealie import FakeMealie, ingredient_line

PASSWORD = "kitchen-secret-42"
HTMX = {"HX-Request": "true"}


# --- Fixtures and helpers ---


@pytest.fixture
def fake_mealie(app_under_test: FastAPI) -> FakeMealie:
    """Lasagna (want-to-try) and carrot cake, wired into the app."""
    fake = FakeMealie()
    fake.put_recipe("lasagna", [ingredient_line("lasagna noodle", "12 lasagna noodles"),
                                ingredient_line("basil", "basil (optional)", note="optional", title="Garnish")],
                    tags=["want-to-try"])
    fake.put_recipe("carrot-cake", [ingredient_line("carrot", "3 carrots")])
    app_under_test.dependency_overrides[get_mealie_client_factory] = lambda: (lambda _api_token: fake.client())
    return fake


@pytest.fixture
def kitchen(client: TestClient, fake_mealie: FakeMealie) -> dict[str, int]:
    """Alice's connected household; returns recipe IDs by slug (after a sync)."""
    client.post("/signup", data={"display_name": "Alice", "username": "alice", "password": PASSWORD})
    client.post("/households/create", data={"name": "Home"})
    client.post("/household/mealie", data={"api_token": "any"})
    client.post("/recipes/sync", headers=HTMX)
    return {recipe["slug"]: recipe["id"] for recipe in client.get("/api/recipes").json()["recipes"]}


def log_cook_form(client: TestClient, recipe_id: int, **fields: str) -> str:
    """Submit the "I cooked this" form via htmx; return the activity HTML."""
    form_data = {"cooked_by_user_id": "", "cooked_at": "", "stars": "", "servings": "", "notes": ""} | fields
    response = client.post(f"/recipes/{recipe_id}/cooks", data=form_data, headers=HTMX)
    assert response.status_code == 200, response.text
    return response.text


# --- Recipe list and sync ---


def test_empty_library_explains_what_to_do(client: TestClient, fake_mealie: FakeMealie) -> None:
    """Before any sync, the list says to connect Mealie and press Sync."""
    client.post("/signup", data={"display_name": "Alice", "username": "alice", "password": PASSWORD})
    client.post("/households/create", data={"name": "Home"})

    page = client.get("/recipes").text

    assert "Not synced from Mealie yet" in page
    assert "Press" in page and "Sync" in page


def test_sync_button_reports_and_triggers_list_reload(client: TestClient, fake_mealie: FakeMealie) -> None:
    """The htmx sync returns a status fragment and tells the list to reload."""
    client.post("/signup", data={"display_name": "Alice", "username": "alice", "password": PASSWORD})
    client.post("/households/create", data={"name": "Home"})
    client.post("/household/mealie", data={"api_token": "any"})

    response = client.post("/recipes/sync", headers=HTMX)

    assert "Synced: 2 new" in response.text
    assert "<html" not in response.text  # a fragment, not a page
    assert response.headers["HX-Trigger"] == "recipes-changed"
    assert "Lasagna" in client.get("/recipes").text


def test_sync_without_mealie_explains(client: TestClient, fake_mealie: FakeMealie) -> None:
    """Syncing an unconnected household points to settings."""
    client.post("/signup", data={"display_name": "Alice", "username": "alice", "password": PASSWORD})
    client.post("/households/create", data={"name": "Home"})

    response = client.post("/recipes/sync", headers=HTMX)

    assert "Household settings" in response.text


def test_list_shows_badges_and_search_returns_fragment(client: TestClient, kitchen: dict[str, int]) -> None:
    """Full page has both recipes; htmx search returns only matching rows."""
    page = client.get("/recipes").text
    assert "Lasagna" in page and "Carrot Cake" in page and "WANT TO TRY" in page

    fragment = client.get("/recipes", params={"q": "carr"}, headers=HTMX).text
    assert "Carrot Cake" in fragment and "Lasagna" not in fragment
    assert "<html" not in fragment

    want_only = client.get("/recipes", params={"show": "want"}).text
    assert "Lasagna" in want_only and "Carrot Cake" not in want_only


# --- Recipe page ---


def test_recipe_page_has_form_link_and_ingredients(client: TestClient, kitchen: dict[str, int]) -> None:
    """The page shows the cook form (me preselected), Mealie link, and ingredients."""
    page = client.get(f"/recipes/{kitchen['lasagna']}").text

    assert "I cooked this" in page
    assert re.search(r'<option value="\d+" selected>\s*Alice \(me\)', page)
    assert 'href="http://mealie.test/g/home/r/lasagna"' in page
    assert "12 lasagna noodles" in page and "Garnish" in page and "(optional)" in page


def test_log_cook_updates_page_and_mealie(client: TestClient, kitchen: dict[str, int],
                                          fake_mealie: FakeMealie) -> None:
    """Logging via the form shows the result, the history row, and clears want-to-try."""
    activity = log_cook_form(client, kitchen["lasagna"], stars="5", servings="4", notes="great")

    assert "Logged · Mealie updated" in activity
    assert "Cooked 1×" in activity and "serves 4" in activity and "great" in activity
    assert "WANT TO TRY" not in activity
    assert "lasagna" in fake_mealie.last_made


def test_cook_time_is_read_in_display_timezone(client: TestClient, kitchen: dict[str, int]) -> None:
    """'7:30 PM' typed on an Eastern-time installation is stored as 23:30 UTC."""
    log_cook_form(client, kitchen["carrot-cake"], cooked_at="2026-10-20T19:30")

    stored_time = client.get("/api/cooks").json()[0]["cooked_at"]
    assert stored_time.startswith("2026-10-20T23:30:00")


@pytest.mark.parametrize(
    ("fields", "expected_message"),
    [
        ({"cooked_at": "2999-01-01T00:00"}, "future"),
        ({"cooked_at": "not-a-date"}, "Check the date"),
        ({"servings": "lots"}, "Check the date and numbers"),
    ],
)
def test_bad_cook_form_shows_error(client: TestClient, kitchen: dict[str, int], fields: dict,
                                   expected_message: str) -> None:
    """Bad input shows a message and logs nothing."""
    activity = log_cook_form(client, kitchen["carrot-cake"], **fields)

    assert expected_message in activity
    assert client.get("/api/cooks").json() == []


def test_star_tap_sets_my_rating(client: TestClient, kitchen: dict[str, int]) -> None:
    """Tapping the 4th star rates 4 and shows it filled."""
    response = client.post(f"/recipes/{kitchen['carrot-cake']}/rating", data={"stars": "4"}, headers=HTMX)

    assert "Your rating: 4 ★" in response.text
    assert client.get(f"/api/recipes/{kitchen['carrot-cake']}/ratings").json()["my_stars"] == 4


def test_edit_and_delete_history_entry(client: TestClient, kitchen: dict[str, int]) -> None:
    """Edit opens an inline form; saving updates; delete removes."""
    log_cook_form(client, kitchen["carrot-cake"], notes="first")
    cook_id = client.get("/api/cooks").json()[0]["id"]

    edit_form = client.get(f"/cooks/{cook_id}/edit", headers=HTMX).text
    assert 'name="cooked_at"' in edit_form and "first" in edit_form

    cooked_at_value = re.search(r'name="cooked_at" value="([^"]+)"', edit_form).group(1)
    saved = client.post(f"/cooks/{cook_id}/edit", headers=HTMX, data={
        "cooked_by_user_id": "", "cooked_at": cooked_at_value, "servings": "2", "notes": "second"}).text
    assert "Entry updated" in saved and "second" in saved and "serves 2" in saved

    deleted = client.post(f"/cooks/{cook_id}/delete", headers=HTMX).text
    assert "Entry deleted" in deleted and "Nobody has logged this yet" in deleted


def test_plain_form_post_redirects_back(client: TestClient, kitchen: dict[str, int]) -> None:
    """Without htmx (no JavaScript), the form still works and redirects."""
    response = client.post(f"/recipes/{kitchen['carrot-cake']}/cooks", data={"notes": "no js"},
                           follow_redirects=False)

    assert (response.status_code, response.headers["location"]) == (303, f"/recipes/{kitchen['carrot-cake']}")
    assert client.get("/api/cooks").json()[0]["notes"] == "no js"


def test_user_text_is_escaped(client: TestClient, kitchen: dict[str, int]) -> None:
    """Notes are shown as text, never as HTML."""
    activity = log_cook_form(client, kitchen["carrot-cake"], notes="<script>alert(1)</script>")

    assert "<script>alert(1)</script>" not in activity
    assert "&lt;script&gt;" in activity


# --- Cook log page ---


def test_cook_log_page(client: TestClient, kitchen: dict[str, int]) -> None:
    """The log lists entries with recipe names and who cooked."""
    log_cook_form(client, kitchen["carrot-cake"], notes="yum")

    page = client.get("/cooks").text

    assert "Carrot Cake" in page and "Alice" in page and "yum" in page


# --- Household isolation on pages ---


def test_other_households_pages_are_404(client: TestClient, other_client: TestClient,
                                        kitchen: dict[str, int]) -> None:
    """A different household can't open, log, rate, or edit Alice's recipe."""
    log_cook_form(client, kitchen["lasagna"])
    cook_id = client.get("/api/cooks").json()[0]["id"]
    other_client.post("/signup", data={"display_name": "Eve", "username": "eve", "password": PASSWORD})
    other_client.post("/households/create", data={"name": "Eve's"})

    assert other_client.get(f"/recipes/{kitchen['lasagna']}").status_code == 404
    assert other_client.post(f"/recipes/{kitchen['lasagna']}/cooks", data={}, headers=HTMX).status_code == 404
    assert other_client.post(f"/recipes/{kitchen['lasagna']}/rating", data={"stars": "1"},
                             headers=HTMX).status_code == 404
    assert other_client.get(f"/cooks/{cook_id}/edit", headers=HTMX).status_code == 404
    assert other_client.post(f"/cooks/{cook_id}/delete", headers=HTMX).status_code == 404
    assert "Lasagna" not in other_client.get("/cooks").text
