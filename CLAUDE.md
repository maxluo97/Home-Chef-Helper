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
  2. DB + Alembic migrations (SQLite WAL, upgrade on startup).
  3. Accounts + households + join code (every household-owned table has `household_id`).
  4. `mealie_client` (list/get recipes) against the live OpenAPI spec.
  5. Recipe sync into `recipes_cache` / `recipe_ingredients`.
  6. Cook log + per-person ratings.
  7. Jinja + htmx UI.
  8. Mealie "last made" write-back.
  9. Dockerfile + add Chef Helper to `compose.dev.yaml`.
- Mealie credentials are **per household**, encrypted in the DB (plan §9.8).
- The NAS (HexOS) is not set up yet; everything runs on the dev PC until Phase 5.

## Versions and phases

The global versioning rules apply (tags down to `vX.Y.Z`; `version` in
`pyproject.toml` kept in step). Planned mapping onto the plan §8 phases:

| Version | Milestone |
|---|---|
| `v0.0.x` | Phase 0 + Phase 1 blocks, on the way to the first usable version. Block 1 (plus the Phase 0 setup) = `v0.0.1`. |
| `v0.1` | Phase 1 acceptance criteria pass (cook log, ratings, Mealie write-back). |
| `v0.2` / `v0.3` / `v0.4` | Phases 2 / 3 / 4 pass (inventory + Whole Foods paste / recommendations / check-ins). |
| `v1.0` | Phase 5: first production release, running on the NAS. |
| `v1.1`+ | Phase 6 (optional LLM layer), then "later ideas." |

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

# Phase 0 check: list recipes via the Mealie API
$env:MEALIE_TOKEN = "<token>"   # from Mealie: User profile -> API Tokens
py scripts/list_mealie_recipes.py
```

## Conventions and gotchas

- **Dependencies:** add with `uv add <pkg>` / `uv add --dev <pkg>`; never edit
  the lists in `pyproject.toml` by hand. Commit `uv.lock`.
- **Config:** `app/core/config.py` `Settings`; read via the `get_settings()`
  dependency. Dev data (SQLite DB) goes in `./data/` (git-ignored).
- **Tests:** use `fastapi.testclient.TestClient` (backed by `httpx2`; plain
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
