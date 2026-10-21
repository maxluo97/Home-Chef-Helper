"""A small in-memory stand-in for Mealie's recipe API, for tests.

Tests add, edit, and delete recipes on it, then point a MealieClient at it
via `client()`. It can also be told to fail after N requests, to check that
callers don't leave half-finished work behind.
"""

from dataclasses import dataclass, field
from itertools import count

import httpx2

from app.integrations.mealie_client import MealieClient

# Shared counter so every edit gets a new, increasing updatedAt.
EDIT_COUNTER = count(1)


def ingredient_line(food_name: str | None, display: str, note: str = "", title: str = "") -> dict:
    """One ingredient line shaped like Mealie's RecipeIngredient output."""
    return {
        "quantity": 1.0,
        "unit": None,
        "food": {"id": f"food-{food_name}", "name": food_name} if food_name else None,
        "note": note,
        "display": display,
        "originalText": display,
        "title": title,
        "referenceId": f"ref-{display}",
    }


@dataclass
class FakeMealie:
    """Recipes keyed by slug, served like Mealie v3.29.0 would."""

    recipes: dict[str, dict] = field(default_factory=dict)
    per_page: int = 2  # small, so multi-page listing is exercised
    fail_after_requests: int | None = None  # simulate Mealie dying mid-sync
    requests_seen: list[str] = field(default_factory=list)

    # --- Test controls ---

    def put_recipe(self, slug: str, ingredients: list[dict], tags: list[str] | None = None) -> None:
        """Add a recipe, or edit an existing one (bumping its updatedAt)."""
        existing = self.recipes.get(slug)
        self.recipes[slug] = {
            "id": existing["id"] if existing else f"mealie-id-{slug}",
            "slug": slug,
            "name": slug.replace("-", " ").title(),
            "householdId": "household-1",
            "groupId": "group-1",
            "tags": [{"name": tag_slug, "slug": tag_slug} for tag_slug in (tags or [])] or None,
            "updatedAt": f"2026-10-21T00:00:00.{next(EDIT_COUNTER):06d}+00:00",
            "recipeIngredient": ingredients,
        }

    def delete_recipe(self, slug: str) -> None:
        """Remove a recipe from Mealie."""
        del self.recipes[slug]

    def client(self) -> MealieClient:
        """A MealieClient whose requests are answered by this fake."""
        return MealieClient("http://mealie.test", "fake-token", transport=httpx2.MockTransport(self.handle))

    # --- Request handling ---

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Answer one HTTP request."""
        self.requests_seen.append(request.url.path)
        if self.fail_after_requests is not None and len(self.requests_seen) > self.fail_after_requests:
            raise httpx2.ConnectError("Mealie went away", request=request)

        if request.url.path == "/api/users/self":
            return httpx2.Response(200, json={
                "id": "user-1", "username": "Test", "admin": False,
                "household": "Family", "householdId": "household-1",
                "group": "Home", "groupId": "group-1",
            })
        if request.url.path == "/api/recipes":
            return self.recipe_page(int(request.url.params["page"]))
        if request.url.path.startswith("/api/recipes/"):
            slug = request.url.path.removeprefix("/api/recipes/")
            if slug not in self.recipes:
                return httpx2.Response(404, json={"detail": "Not found"})
            return httpx2.Response(200, json=self.recipes[slug])
        return httpx2.Response(404)

    def recipe_page(self, page_number: int) -> httpx2.Response:
        """One page of recipe summaries, sorted by name like the real API."""
        all_recipes = sorted(self.recipes.values(), key=lambda recipe: recipe["name"])
        nTotalPages = (len(all_recipes) + self.per_page - 1) // self.per_page
        start_index = (page_number - 1) * self.per_page
        page_items = [
            {key: value for key, value in recipe.items() if key != "recipeIngredient"}
            for recipe in all_recipes[start_index:start_index + self.per_page]
        ]
        return httpx2.Response(200, json={
            "page": page_number, "per_page": self.per_page, "total": len(all_recipes),
            "total_pages": nTotalPages, "items": page_items,
        })
