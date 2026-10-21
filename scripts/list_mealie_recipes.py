"""List every recipe in a Mealie instance via its REST API.

Phase 0 acceptance check: proves we can reach Mealie and authenticate with
an API token. Uses only the Python standard library so it runs without a
virtual environment.

Usage:
    set MEALIE_URL=http://localhost:9925        (optional; this is the default)
    set MEALIE_TOKEN=<API token from Mealie: User profile -> API Tokens>
    python scripts/list_mealie_recipes.py
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Number of recipes requested per API page. Mealie paginates /api/recipes.
RECIPES_PER_PAGE = 50

# Seconds to wait for Mealie before giving up on a request.
REQUEST_TIMEOUT_SECONDS = 15


def fetch_recipe_page(mealie_url: str, api_token: str, page_number: int) -> dict:
    """Fetch one page of recipe summaries and return the decoded JSON body."""
    # Build the paginated query, sorted by name so output is stable.
    query_string = urllib.parse.urlencode(
        {
            "page": page_number,
            "perPage": RECIPES_PER_PAGE,
            "orderBy": "name",
            "orderDirection": "asc",
        }
    )
    request = urllib.request.Request(
        f"{mealie_url}/api/recipes?{query_string}",
        headers={
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.load(response)


def fetch_all_recipes(mealie_url: str, api_token: str) -> list[dict]:
    """Walk every page of /api/recipes and return all recipe summaries."""
    all_recipes: list[dict] = []
    page_number = 1

    # Keep requesting pages until we've read the last one Mealie reports.
    while True:
        page_body = fetch_recipe_page(mealie_url, api_token, page_number)
        all_recipes.extend(page_body["items"])
        nTotalPages = page_body["total_pages"]
        if page_number >= nTotalPages:
            break
        page_number += 1

    return all_recipes


def main() -> int:
    """Read config from the environment, list recipes, return an exit code."""
    # --- Configuration (environment variables only, per the plan) ---
    mealie_url = os.environ.get("MEALIE_URL", "http://localhost:9925").rstrip("/")
    api_token = os.environ.get("MEALIE_TOKEN")
    if not api_token:
        print("MEALIE_TOKEN is not set. Create one in Mealie under "
              "User profile -> API Tokens.", file=sys.stderr)
        return 2

    # --- Fetch, translating common failures into readable messages ---
    try:
        recipes = fetch_all_recipes(mealie_url, api_token)
    except urllib.error.HTTPError as http_error:
        if http_error.code == 401:
            print("Mealie rejected the token (401 Unauthorized).", file=sys.stderr)
        else:
            print(f"Mealie returned HTTP {http_error.code}: {http_error.reason}",
                  file=sys.stderr)
        return 1
    except urllib.error.URLError as url_error:
        print(f"Could not reach Mealie at {mealie_url}: {url_error.reason}",
              file=sys.stderr)
        return 1

    # --- Report ---
    print(f"{len(recipes)} recipe(s) in Mealie at {mealie_url}:")
    for recipe in recipes:
        print(f"  - {recipe['name']}  ({recipe['slug']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
