"""Tests for the Mealie client against a fake Mealie (no network).

Response bodies are trimmed copies of real Mealie v3.29.0 responses,
keeping the fields the client reads plus a few it must ignore.
"""

import json
from collections.abc import Callable

import httpx2
import pytest

from app.integrations.mealie_client import (
    MealieAuthError,
    MealieClient,
    MealieError,
    MealieNotFoundError,
    MealieUnavailableError,
)

MEALIE_URL = "http://mealie.test"
API_TOKEN = "test-token"

USER_SELF_BODY = {
    "id": "user-1",
    "username": "Test",
    "fullName": "Test",
    "admin": True,
    "group": "Home",
    "household": "Family",
    "groupId": "group-1",
    "householdId": "household-1",
    "email": "ignored@example.com",  # extra field: must be ignored
}


def recipe_summary_body(slug: str, tags: list[dict] | None = None) -> dict:
    """A recipe-list item shaped like Mealie's RecipeSummary."""
    return {
        "id": f"id-{slug}",
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "householdId": "household-1",
        "groupId": "group-1",
        "userId": "user-1",
        "tags": tags,
        "dateUpdated": "2026-10-20T12:00:00",
        "recipeServings": 4,  # extra field: must be ignored
    }


def make_client(handler: Callable[[httpx2.Request], httpx2.Response]) -> MealieClient:
    """A client whose requests go to `handler` instead of the network."""
    return MealieClient(MEALIE_URL, API_TOKEN, transport=httpx2.MockTransport(handler))


# --- Identity ---


def test_get_current_user_parses_household_and_sends_token() -> None:
    """The token goes in the Authorization header; household info comes back."""
    seen_auth_headers = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen_auth_headers.append(request.headers["authorization"])
        assert request.url.path == "/api/users/self"
        return httpx2.Response(200, json=USER_SELF_BODY)

    with make_client(handler) as mealie:
        user = mealie.get_current_user()

    assert seen_auth_headers == [f"Bearer {API_TOKEN}"]
    assert (user.username, user.household, user.household_id, user.group_id) == (
        "Test", "Family", "household-1", "group-1",
    )
    assert user.admin is True


# --- Recipe list ---


def test_recipe_list_walks_every_page() -> None:
    """All pages are fetched and combined, in order."""
    pages = {
        1: ["apple-pie", "beef-stew"],
        2: ["carrot-soup"],
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        page_number = int(request.url.params["page"])
        return httpx2.Response(200, json={
            "page": page_number, "per_page": 2, "total": 3, "total_pages": 2,
            "items": [recipe_summary_body(slug) for slug in pages[page_number]],
        })

    with make_client(handler) as mealie:
        recipes = mealie.list_recipe_summaries()

    assert [recipe.slug for recipe in recipes] == ["apple-pie", "beef-stew", "carrot-soup"]


def test_empty_library_returns_no_recipes() -> None:
    """Mealie reports 0 total pages for an empty library; that's fine."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"page": 1, "per_page": 100, "total": 0, "total_pages": 0, "items": []})

    with make_client(handler) as mealie:
        assert mealie.list_recipe_summaries() == []


def test_null_tags_become_empty_list_and_tags_parse() -> None:
    """`tags: null` is treated as no tags; real tags keep name and slug."""
    want_to_try = {"id": "tag-1", "name": "Want to try", "slug": "want-to-try", "groupId": "group-1"}

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={
            "page": 1, "per_page": 100, "total": 2, "total_pages": 1,
            "items": [recipe_summary_body("plain", tags=None), recipe_summary_body("new-one", tags=[want_to_try])],
        })

    with make_client(handler) as mealie:
        plain, new_one = mealie.list_recipe_summaries()

    assert plain.tags == []
    assert [tag.slug for tag in new_one.tags] == ["want-to-try"]


# --- Full recipe ---


def test_get_recipe_parses_ingredients_with_and_without_food() -> None:
    """Parsed ingredients keep their food; unparsed lines have food=None."""
    recipe_body = recipe_summary_body("spinach-dal") | {
        "recipeIngredient": [
            {
                "quantity": 2.0,
                "unit": {"id": "unit-1", "name": "cup"},
                "food": {"id": "food-1", "name": "spinach"},
                "note": "packed",
                "display": "2 cups spinach packed",
                "originalText": "2 cups packed spinach",
                "referenceId": "ref-1",
            },
            {
                "quantity": 0,
                "unit": None,
                "food": None,
                "note": "salt to taste",
                "display": "salt to taste",
                "originalText": None,
                "referenceId": "ref-2",
            },
        ],
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/recipes/spinach-dal"
        return httpx2.Response(200, json=recipe_body)

    with make_client(handler) as mealie:
        recipe = mealie.get_recipe("spinach-dal")

    spinach_line, salt_line = recipe.recipe_ingredient
    assert (spinach_line.food.name, spinach_line.unit.name, spinach_line.quantity) == ("spinach", "cup", 2.0)
    assert spinach_line.original_text == "2 cups packed spinach"
    assert salt_line.food is None
    assert salt_line.display == "salt to taste"


# --- Error translation ---


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (401, MealieAuthError),
        (403, MealieAuthError),
        (404, MealieNotFoundError),
        (500, MealieUnavailableError),
        (503, MealieUnavailableError),
        (422, MealieError),
    ],
)
def test_http_errors_become_mealie_errors(status_code: int, expected_error: type[MealieError]) -> None:
    """Each HTTP failure maps to a specific, catchable error type."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, content=json.dumps({"detail": "nope"}))

    with make_client(handler) as mealie, pytest.raises(expected_error):
        mealie.get_current_user()


def test_unreachable_mealie_is_unavailable() -> None:
    """A refused connection becomes MealieUnavailableError, not a raw crash."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    with make_client(handler) as mealie, pytest.raises(MealieUnavailableError):
        mealie.get_current_user()
