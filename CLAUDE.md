# CLAUDE.md — Home Chef Helper

Project-specific context. The owner's global `~/.claude/CLAUDE.md` (naming,
comments, incremental development, question IDs, git rules) also applies;
don't duplicate it here.

## Start here

- **Master plan:** [home-chef-helper-plan.md](home-chef-helper-plan.md). Read it
  before writing code. Owner decisions are in **§9** and override anything else
  in the plan. Items marked **[VERIFY]** must be checked against live docs or
  Mealie's OpenAPI spec (`http://localhost:9925/docs`) before coding against them.
- Work phase by phase (plan §8); don't start a phase until the previous one's
  acceptance criteria pass.

## Current status (2026-10-20)

- **Phase 0: done.** Local Mealie runs in Docker; `scripts/list_mealie_recipes.py`
  lists recipes with an API token. One recipe imported so far.
- **Phase 1: in progress.** Built as small, separately tested blocks:
  1. ✅ Scaffold: uv project, `app/` package, env config, `/healthz`, pytest. (v0.0.1)
  2. ✅ DB + Alembic migrations (SQLite WAL, upgrade on startup). (v0.0.2)
  3. ✅ Accounts + households + join code (JSON API; switcher UI comes in block 7). (v0.0.3)
  4. ✅ `mealie_client` (list/get recipes) + per-household encrypted Mealie token. (v0.0.4)
  5. ✅ Recipe sync (on demand) into `recipes` / `recipe_ingredients`. (v0.0.5)
     Nightly scheduled sync still to add (APScheduler; planned alongside block 9).
  6. ✅ Cook log + per-person ratings (whole stars, latest wins, history kept). (v0.0.6)
  7. Jinja + htmx UI.
  8. ✅ Mealie write-back: "last made" + clear want-to-try after first cook. (v0.0.7)
     Built before block 7 so the UI can show write-back status.
  9. Dockerfile + add Chef Helper to `compose.dev.yaml`.
- Mealie credentials are **per household**, encrypted in the DB with the
  installation key `CHEF_SECRET_KEY` (plan §9.8). Local `.env` holds
  `MEALIE_TOKEN` (dev scripts + live tests) and `CHEF_SECRET_KEY`.
- **Mealie API facts (v3.29.0, verified):** recipes are shared across a Mealie
  *group*; `householdId` on a recipe is who added it; other households see it
  unless "private household" is on. "Last made" is per household
  (`GET /api/households/self/recipes/{slug}`, `PATCH /api/recipes/{slug}/last-made`).
  `/api/users/self` gives a token's household and group. Spec saved locally:
  fetch `http://localhost:9925/openapi.json`.
- The NAS (HexOS) is not set up yet; everything runs on the dev PC until Phase 5.

## Versions and phases

The global versioning rules apply (tags down to `vX.Y.Z`; `version` in
`pyproject.toml` kept in step). Planned mapping onto the plan §8 phases:

| Version | Milestone |
|---|---|
| `v0.0.x` | Phase 0 + Phase 1 blocks, on the way to the first usable version. Block 1 (plus the Phase 0 setup) = `v0.0.1`. **Not tagged** (see below). |
| `v0.1` | Phase 1 acceptance criteria pass (cook log, ratings, Mealie write-back). |
| `v0.2` / `v0.3` / `v0.4` | Phases 2 / 3 / 4 pass (inventory + Whole Foods paste / recommendations / check-ins). |
| `v1.0` | Phase 5: first production release, running on the NAS. |
| `v1.1`+ | Phase 6 (optional LLM layer), then "later ideas." |

- **No git tags before `v0.1.0`** (owner decision, 2026-10-21; overrides the
  global "tag every three-place version" rule for `v0.0.x` only). `v0.0.x`
  versions still bump `version` in `pyproject.toml` and are named in commit
  messages. The `v0.1.0` tag message summarizes all `v0.0.x` work (build it
  from the block commit messages). Tagging resumes normally from `v0.1.0`.
- Within a phase, a finished block (or a group of small blocks) bumps the third
  place: e.g. Phase 2 blocks are `v0.1.1`, `v0.1.2`, ... until `v0.2`.
- The mapping isn't strictly 1-to-1. If a phase is big enough to deserve two
  releases, it can take two second-place numbers, and later phases shift up.
  Ask the owner before doing that.

## Dev environment

- Windows 11. Shell is **Windows PowerShell 5.1** (no `&&`; use `;` / `if ($?)`).
- **Python 3.13** (user install): `%LOCALAPPDATA%\Programs\Python\Python313\python.exe`,
  also `py`. Packages and the virtual environment are managed with **uv**.
- **Docker Desktop** runs the dev stack.
- Mealie dev instance: UI `http://localhost:9925`, API docs `/docs`. It is a
  local throwaway; its accounts are not registered anywhere else.

## Commands

```powershell
# Start / stop the dev stack (Mealie). Data persists in a named volume.
docker compose -f deploy/compose.dev.yaml up -d
docker compose -f deploy/compose.dev.yaml down

# Install / update dependencies into .venv (after a pull or pyproject change)
uv sync

# Run the tests
uv run pytest

# Run Chef Helper locally (auto-reloads on code changes) -> http://localhost:8000
uv run uvicorn app.main:app --reload

# Database migrations (the app also applies pending ones at startup)
uv run alembic revision --autogenerate --rev-id 0002 -m "describe change"   # then review the file
uv run alembic upgrade head
uv run alembic check      # "No new upgrade operations" = models and migrations agree

# Phase 0 check: list recipes via the Mealie API
$env:MEALIE_TOKEN = "<token>"   # from Mealie: User profile -> API Tokens
py scripts/list_mealie_recipes.py
```

## Conventions and gotchas

- **Dependencies:** add with `uv add <pkg>` / `uv add --dev <pkg>`; never edit
  the lists in `pyproject.toml` by hand. Commit `uv.lock`.
- **Config:** `app/core/config.py` `Settings`; read via the `get_settings()`
  dependency. Dev data (SQLite DB) goes in `./data/` (git-ignored).
- **Database:** models in `app/models/` (import each in `app/models/__init__.py`).
  Every schema change needs a migration in `migrations/versions/`, numbered
  `0001`, `0002`, ... via `--rev-id`; `tests/test_migrations.py` fails if
  models and migrations drift. Store times with the `UTCDateTime` column type
  (aware UTC in, aware UTC out; naive datetimes are rejected). Migrations
  must not import from `app/` (env.py renders `UTCDateTime` as `sa.DateTime()`).
- **Layers:** routes in `app/api/` stay thin; logic that touches the DB lives
  in `app/services/` (shared by the JSON API and, later, HTML pages); pure
  logic lives in `app/core/security.py` now and `app/domain/` later.
- **Household scoping:** any route on household-owned data takes the
  `ActiveHouseholdId` dependency (`app/api/deps.py`) and filters every query
  by it. Logins are DB-backed sessions (`login_sessions`, cookie
  `chef_session`, only the token's SHA-256 stored); the active household is
  per session (per device).
- **Mealie access:** go through `app/services/mealie_link.open_mealie_client`
  (decrypts the household's token); routes get the client factory from the
  `get_mealie_client_factory` dependency, which tests override with a fake
  Mealie (`httpx2.MockTransport`). `tests/test_mealie_live.py` hits the real
  dev Mealie and skips if it's down or `MEALIE_TOKEN` is missing.
- **Recipe cache:** `recipes` is per household (plan's `recipes_cache`); a
  recipe gone from Mealie gets `removed_at`, never deleted (cook logs point
  at it). Ingredient lines store Mealie's food plus `canonical_name`
  (`app/domain/ingredients.py`); Phase 2's `ingredients` table will link to
  that name. Whether canonical ingredients are global or per household is
  still open; decide at Phase 2.
- **Cook log / ratings:** `app/services/cook_log.py` (every function takes the
  household and filters by it; other households' IDs act as not found).
  Ratings are append-only; "latest per person wins" lives in
  `app/domain/ratings.py`. API datetimes must carry a timezone
  (`AwareDatetime`); naive ones get 422.
- **Mealie write-back:** best effort, never fails a request. Rules in
  `app/domain/writeback.py` (last made = latest cook; never moves backwards
  on a back-dated cook, moves back when the latest cook is deleted; can't be
  cleared via API; want-to-try cleared once, tracked by
  `recipes.want_to_try_cleared_at`). Pending write-backs retry after every
  recipe sync. Mealie tags are group-wide, so clearing want-to-try affects
  every Mealie household in the group.
- **Probing Mealie writes:** test on a throwaway recipe (POST /api/recipes,
  then DELETE), never on the owner's real recipes.
- **Fake Mealie for tests:** `tests/fake_mealie.py` (`FakeMealie`): add/edit/
  delete recipes, small pages, and `fail_after_requests` to simulate outages.
- **Tests:** the `client` fixture (tests/conftest.py) is a started app on a
  throwaway SQLite DB per test; `other_client` is a second browser (own
  cookies) on the same app, for two-user tests. Use `fastapi.testclient.TestClient` (backed by `httpx2`; plain
  `httpx` triggers a Starlette deprecation warning).

- **Secrets:** config comes from environment variables only. Never commit the
  Mealie token; a local `.env` is git-ignored.
- **Grocery fixtures:** committed fixtures in `tests/fixtures/wholefoods/` are
  **synthetic**. Real pasted orders live in `tests/fixtures/wholefoods/private/`
  (git-ignored, personal purchase history) — never commit or push them, and
  tests using them must skip if the folder is empty.
- **Git commit messages from PowerShell 5.1:** passing a message containing
  double quotes via `-m` gets mangled. Write the message to a file in the
  scratchpad and use `git commit -F <file>`.
- `git push` output appears as a red "error" in PowerShell 5.1 because git
  writes progress to stderr; check the actual text before assuming failure.
