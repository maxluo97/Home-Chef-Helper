"""Read-side client for the Mealie REST API.

Coded against Mealie v3.29.0's OpenAPI spec (`/openapi.json` on the running
instance). Only the fields Chef Helper uses are modelled; everything else in
Mealie's responses is ignored, so new Mealie fields don't break us.

Facts verified against the spec (plan §2.3 / §9.8 [VERIFY] items):
- A token acts as one Mealie user; `/api/users/self` names that user's
  household and group.
- Recipes belong to the Mealie *group*. Each records the household that
  added it (`householdId`), and other households in the group can see it
  unless that household's "private household" preference is on.
- "Last made" is tracked per household (`/api/households/self/recipes/{slug}`).
- Verified by probing a throwaway recipe: PATCH /api/recipes/{slug} with only
  `tags` changes only the tags; PATCH .../last-made accepts any time, even
  one earlier than the current value, and adds no timeline event.
"""

from collections.abc import Iterator
from datetime import datetime
from typing import Any, Self

import httpx2
from pydantic import BaseModel, ConfigDict, field_validator
from pydantic.alias_generators import to_camel

# Seconds to wait for Mealie before treating it as unavailable.
DEFAULT_TIMEOUT_SECONDS = 10.0

# Recipes requested per page when listing (Mealie paginates /api/recipes).
RECIPES_PER_PAGE = 100


# --- Errors ---


class MealieError(Exception):
    """Base class for problems talking to Mealie."""


class MealieUnavailableError(MealieError):
    """Mealie couldn't be reached, timed out, or had a server error."""


class MealieAuthError(MealieError):
    """Mealie rejected the API token (wrong, expired, or deleted)."""


class MealieNotFoundError(MealieError):
    """The requested item doesn't exist (or this token can't see it)."""


# --- Response models (camelCase JSON <-> snake_case Python) ---


class MealieModel(BaseModel):
    """Base for Mealie payloads: camelCase aliases, unknown fields ignored."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class MealieUser(MealieModel):
    """The Mealie user an API token belongs to."""

    id: str
    username: str
    full_name: str | None = None
    admin: bool = False
    household_id: str
    household: str  # household name
    group_id: str
    group: str  # group name


class MealieTag(MealieModel):
    """A recipe tag, e.g. "want-to-try" (plan §9.7)."""

    name: str
    slug: str


class MealieRecipeSummary(MealieModel):
    """One row of the recipe list."""

    id: str
    slug: str
    name: str | None = None
    household_id: str
    tags: list[MealieTag] = []
    # Changes on every edit (microsecond precision); recipe sync compares it
    # to skip re-downloading unchanged recipes. Kept as Mealie's raw text.
    updated_at: str | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def null_tags_to_empty(cls, raw_tags: Any) -> Any:
        """Mealie sends `null` instead of `[]` for recipes without tags."""
        return raw_tags or []


class MealieFood(MealieModel):
    """Mealie's parsed "food" for an ingredient line (e.g. "spinach").

    `id` is missing for foods Mealie suggested but hasn't saved.
    """

    id: str | None = None
    name: str


class MealieUnit(MealieModel):
    """Unit of an ingredient line (e.g. "cup")."""

    name: str


class MealieIngredient(MealieModel):
    """One ingredient line of a recipe."""

    quantity: float | None = None
    unit: MealieUnit | None = None
    food: MealieFood | None = None
    note: str | None = None
    display: str = ""  # Mealie's ready-made text, e.g. "2 cups spinach"
    original_text: str | None = None  # the line as originally imported
    reference_id: str | None = None
    # Section heading that starts at this line (e.g. "For the sauce"); Mealie
    # sends "" when there is none.
    title: str | None = None


class MealieRecipe(MealieRecipeSummary):
    """Full recipe, including its ingredient lines."""

    recipe_ingredient: list[MealieIngredient] = []


# --- Client ---


class MealieClient:
    """Talks to one Mealie instance as one user (one API token).

    Use as a context manager so the connection pool is closed:
        with MealieClient(url, token) as mealie:
            user = mealie.get_current_user()

    `transport` lets tests substitute a fake Mealie (httpx2.MockTransport).
    """

    def __init__(
        self,
        base_url: str,
        api_token: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        self._http = httpx2.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
            timeout=timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Close pooled connections."""
        self._http.close()

    # --- Low-level request with error translation ---

    def _get_json(self, api_path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a path and return decoded JSON, raising MealieError subclasses."""
        return self._request_json("GET", api_path, params=params)

    def _request_json(
        self,
        http_method: str,
        api_path: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        """Send one request and return decoded JSON (or None if empty).

        Raises MealieError subclasses for every failure.
        """
        try:
            response = self._http.request(http_method, api_path, params=params, json=json_body)
        except httpx2.TransportError as transport_error:
            # Connection refused, DNS failure, timeout, ...
            raise MealieUnavailableError(f"Could not reach Mealie: {transport_error}") from transport_error

        if response.status_code in (401, 403):
            raise MealieAuthError("Mealie rejected the API token.")
        if response.status_code == 404:
            raise MealieNotFoundError(f"Not found in Mealie: {api_path}")
        if response.status_code >= 500:
            raise MealieUnavailableError(f"Mealie server error {response.status_code} on {api_path}")
        if response.status_code >= 400:
            # Anything else is a bug on our side (bad parameters), not the user's.
            raise MealieError(f"Mealie returned {response.status_code} on {api_path}: {response.text[:200]}")
        return response.json() if response.content else None

    # --- Endpoints ---

    def get_current_user(self) -> MealieUser:
        """Who the token belongs to, including their household and group.

        Also the cheapest way to check that a token works.
        """
        return MealieUser.model_validate(self._get_json("/api/users/self"))

    def iter_recipe_summaries(self, per_page: int = RECIPES_PER_PAGE) -> Iterator[MealieRecipeSummary]:
        """Yield every recipe this token can see, walking all pages."""
        page_number = 1
        while True:
            page_body = self._get_json(
                "/api/recipes",
                params={"page": page_number, "perPage": per_page, "orderBy": "name", "orderDirection": "asc"},
            )
            for recipe_item in page_body["items"]:
                yield MealieRecipeSummary.model_validate(recipe_item)

            # Stop after the last page Mealie reports (0 pages when empty).
            nTotalPages = page_body.get("total_pages", 1)
            if page_number >= nTotalPages:
                return
            page_number += 1

    def list_recipe_summaries(self) -> list[MealieRecipeSummary]:
        """Every recipe this token can see, as a list."""
        return list(self.iter_recipe_summaries())

    def get_recipe(self, slug: str) -> MealieRecipe:
        """Full recipe (with ingredients) by slug. Raises MealieNotFoundError."""
        return MealieRecipe.model_validate(self._get_json(f"/api/recipes/{slug}"))

    # --- Writes (plan §2.3: keep Mealie's UI consistent with our cook log) ---

    def get_last_made(self, slug: str) -> str | None:
        """This token's household's "last made" time for a recipe (raw text)."""
        return self._get_json(f"/api/households/self/recipes/{slug}").get("lastMade")

    def set_last_made(self, slug: str, made_at: datetime) -> None:
        """Set this household's "last made" time for a recipe.

        Mealie stores whatever it's given, even an earlier time than before,
        so callers decide which time is right.
        """
        self._request_json(
            "PATCH", f"/api/recipes/{slug}/last-made", json_body={"timestamp": made_at.isoformat()}
        )

    def remove_recipe_tag(self, slug: str, tag_slug: str) -> bool:
        """Remove one tag from a recipe; True if it was there.

        PATCH replaces the recipe's whole tag list (verified: other recipe
        fields are left alone), so the current list is read first and sent
        back without the one tag. Tags are shared by the Mealie group.
        """
        current_tags = self._get_json(f"/api/recipes/{slug}").get("tags") or []
        remaining_tags = [tag for tag in current_tags if tag.get("slug") != tag_slug]
        if len(remaining_tags) == len(current_tags):
            return False
        self._request_json("PATCH", f"/api/recipes/{slug}", json_body={"tags": remaining_tags})
        return True
