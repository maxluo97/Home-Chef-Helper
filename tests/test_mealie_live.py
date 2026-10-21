"""Smoke tests against the real local Mealie (deploy/compose.dev.yaml).

Skipped automatically unless MEALIE_TOKEN is set (env var or the repo's
.env) and Mealie answers at MEALIE_URL. They only read; nothing is changed.
"""

import os
from pathlib import Path

import httpx2
import pytest
from dotenv import dotenv_values

from app.integrations.mealie_client import MealieClient

# --- Find credentials and decide whether to skip ---

REPO_ROOT = Path(__file__).resolve().parents[1]
DOTENV_VALUES = dotenv_values(REPO_ROOT / ".env") if (REPO_ROOT / ".env").is_file() else {}
LIVE_TOKEN = os.environ.get("MEALIE_TOKEN") or DOTENV_VALUES.get("MEALIE_TOKEN")
LIVE_URL = (os.environ.get("MEALIE_URL") or DOTENV_VALUES.get("MEALIE_URL") or "http://localhost:9925").rstrip("/")


def mealie_is_up() -> bool:
    """True if something answers at the Mealie URL."""
    try:
        return httpx2.get(f"{LIVE_URL}/api/app/about", timeout=3).status_code == 200
    except httpx2.TransportError:
        return False


pytestmark = pytest.mark.skipif(
    not LIVE_TOKEN or not mealie_is_up(),
    reason="needs MEALIE_TOKEN and a running Mealie (docker compose -f deploy/compose.dev.yaml up -d)",
)


# --- Tests ---


def test_live_token_identifies_a_household() -> None:
    """The dev token resolves to a user with a household and group."""
    with MealieClient(LIVE_URL, LIVE_TOKEN) as mealie:
        user = mealie.get_current_user()

    assert user.household_id and user.group_id


def test_live_recipes_list_and_load() -> None:
    """Every listed recipe can be loaded in full, ingredients included."""
    with MealieClient(LIVE_URL, LIVE_TOKEN) as mealie:
        recipes = mealie.list_recipe_summaries()
        assert recipes, "Mealie has no recipes; import a few first"

        for recipe_summary in recipes:
            full_recipe = mealie.get_recipe(recipe_summary.slug)
            assert full_recipe.slug == recipe_summary.slug
            assert full_recipe.recipe_ingredient, f"{full_recipe.slug} has no ingredients"
