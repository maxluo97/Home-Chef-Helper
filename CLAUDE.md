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
- **Next: Phase 1** (FastAPI skeleton, DB + migrations, `/healthz`, Mealie sync,
  cook log, per-person ratings, accounts + household join code). Build it in
  small, separately tested blocks.
- The NAS (HexOS) is not set up yet; everything runs on the dev PC until Phase 5.

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

# Phase 0 check: list recipes via the Mealie API
$env:MEALIE_TOKEN = "<token>"   # from Mealie: User profile -> API Tokens
py scripts/list_mealie_recipes.py
```

## Conventions and gotchas

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
