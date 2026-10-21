# Home Chef Helper — Project Plan

> **For Claude Code:** This is the master plan for the project. Read it fully before writing code. Work phase by phase; each phase ends with acceptance criteria that must pass before moving on. Items marked **[VERIFY]** are facts gathered from web research in Oct 2026 that should be checked against current docs before relying on them. Items marked **[DECIDE]** need the owner's input; ask before assuming.

---

## 1. Goal

A self-hosted, household-shared kitchen assistant that:

1. Keeps a **library of recipes we've actually cooked**, with **per-person ratings** and a **cook log**.
2. Tracks an **approximate fridge/pantry inventory**, fed mainly by **pasted grocery orders** (Whole Foods via Amazon "Your Orders").
3. **Recommends what to cook** by balancing: ingredients about to spoil, ratings, variety (not-made-recently), and "fatigue" (made too often lately).
4. **Proactively asks** about ingredients it's unsure of ("You bought cilantro 12 days ago and no recipe has used it — still have it?").
5. Runs on a **private NAS running HexOS**, shared by everyone in the household.

### Non-goals (for v1)
- Nutrition/macro tracking, meal-kit ordering, public recipe discovery/social features.
- Gram-precise inventory. Inventory is deliberately **approximate and probabilistic** (see §5.2). This is the key design difference from Grocy, which demands exact units and where users report giving up on recipe tracking because of it.

---

## 2. Architecture decision

### 2.1 Build vs. reuse

| Concern | Choice | Why |
|---|---|---|
| Recipe storage, import from URL, editing UI, households, ratings | **Mealie** (reuse via API) | Mature, active, self-hosted; REST API covering recipes, households, meal plans, shopping lists, webhooks; has "Last Made" and "On Hand" at household level; **already has a curated one-click HexOS install script**. |
| Inventory (approximate, confidence-decaying) | **Build our own** | Grocy's exact-unit stock model is the wrong shape for "probably still have spinach." Our model needs confidence, decay, and check-in state. |
| Recommendation engine (expiry + variety + fatigue + ratings) | **Build our own** | This is the novel part. Nothing off-the-shelf does it. |
| Order-paste parsing, check-ins, notifications | **Build our own** | Specific to our workflow. |
| Grocy | **Not used in v1** | Keep as a fallback option if we later want barcode/stock precision. A `grocy-mcp` MCP server exists if we ever want an agent to drive it. |

**Do NOT fork Mealie's code.** Integrate via its API only. Forking means owning merges against a fast-moving upstream. Our service ("Chef Helper") is a sidecar.

### 2.2 System diagram

```
 Phones / laptops (household)
          │  (LAN, or Tailscale for remote)
          ▼
 ┌──────────────────────┐      REST (API token)     ┌──────────────┐
 │  Chef Helper (ours)  │ ───────────────────────▶ │    Mealie    │
 │  - web UI (PWA)      │ ◀─── webhooks (optional)  │  (recipes,   │
 │  - API               │                           │   ratings)   │
 │  - scheduler         │                           └──────────────┘
 │  - SQLite/Postgres   │
 └─────────┬────────────┘
           │ optional (OpenAI-compatible API)
           ▼
   ┌───────────────┐        ┌────────────┐
   │ LLM provider  │        │  ntfy /    │  push notifications
   │ (Ollama local │        │  Home Asst │  for check-ins
   │  or cloud)    │        └────────────┘
   └───────────────┘
```

### 2.3 Source of truth
- **Mealie owns:** recipe content, ingredients list, tags, images, Mealie's own ratings.
- **Chef Helper owns:** inventory, cook log (who/when/rating-at-that-time), per-user ratings history, ingredient alias map, recommendation state, check-in history.
- Chef Helper stores Mealie recipe IDs/slugs as foreign keys and caches recipe ingredient lists (refresh on webhook or nightly sync).
- When a meal is logged as cooked, Chef Helper also writes back to Mealie (mark "last made", update rating) so Mealie's UI stays consistent. **[VERIFY]** exact endpoints for last-made and rating in current Mealie API (`/docs` on the running instance serves the OpenAPI spec).

### 2.4 Tech stack (suggested — adjust if preferred)
- **Backend:** Python 3.12+, FastAPI, SQLModel/SQLAlchemy, Alembic migrations.
- **DB:** SQLite (WAL mode) to start — single household, low concurrency, trivial backup. Abstract via SQLAlchemy so Postgres is a config change.
- **Scheduler:** APScheduler in-process (nightly sync, decay recompute, check-in generation).
- **Frontend:** server-rendered (Jinja + htmx) **or** a small React/Vite SPA. Must be a mobile-friendly **PWA** — it'll mostly be used on phones in the kitchen/store. **[DECIDE]**
- **Packaging:** single Docker image, multi-stage build, runs as non-root (UID/GID 568 to match TrueNAS/HexOS `apps` user).
- **Tests:** pytest; parsers get fixture-based golden tests.

---

## 3. Data model (Chef Helper DB)

```
users(id, name, mealie_user_id?, notify_topic, created_at)

ingredients                -- canonical ingredient ("spinach", "whole milk")
  (id, name, category, storage_default[fridge|freezer|pantry|counter],
   shelf_life_days_fridge, shelf_life_days_freezer, shelf_life_days_pantry,
   is_staple bool,          -- salt, oil, flour: assume present, never nag
   mealie_food_id?)

ingredient_aliases         -- learned mapping from raw product text
  (id, raw_text_normalized UNIQUE, ingredient_id, source[rule|llm|user], confirmed bool)

purchases                  -- one per pasted order
  (id, source[wholefoods_paste|manual|...], purchased_at, raw_text, created_by)

inventory_items            -- one row per lot of an ingredient
  (id, ingredient_id, purchase_id?, quantity_text, quantity_estimate float?,
   location, acquired_at, expected_expiry_at,
   status[present|used_up|discarded|unknown],
   confidence float,        -- P(still have it), see §5.2
   last_confirmed_at, last_used_at)

recipes_cache              -- mirror of Mealie recipe + parsed ingredient links
  (mealie_id, slug, name, tags, last_synced_at)
recipe_ingredients(recipe_mealie_id, ingredient_id, is_optional, amount_text)

cook_events                -- the cook log
  (id, recipe_mealie_id, cooked_at, cooked_by, servings?, notes)
cook_event_consumption(cook_event_id, inventory_item_id, fraction_used)

ratings                    -- per-person, time-stamped (latest wins for scoring)
  (id, recipe_mealie_id, user_id, stars 1–5, created_at)

recipe_flags               -- "retired", "seasonal: winter" ("want to try" is a Mealie tag, see §9.7)
  (recipe_mealie_id, flag, value)

checkins                   -- proactive questions
  (id, inventory_item_id, asked_at, asked_user_id, answered_at, answer[have|gone|some], channel)

recommendation_log         -- what we suggested and whether it was taken
  (id, generated_at, recipe_mealie_id, score, score_breakdown_json, accepted bool?)
```

Notes:
- `recommendation_log.score_breakdown_json` is important: it makes the engine debuggable and lets the UI explain "suggested because spinach expires in 2 days and you haven't made this in 5 weeks."
- Seed `ingredients.shelf_life_*` from the **USDA FoodKeeper** dataset **[VERIFY]** current download location/format; it's published by USDA FSIS. Fall back to sensible category defaults.

---

## 4. Ingestion: getting groceries into inventory

### 4.1 Whole Foods (Amazon order) paste — primary path
User flow: open the Amazon order details page → select all → copy → paste into Chef Helper → review screen → confirm.

Parser pipeline:
1. **Fixtures.** The format below was **derived from real pasted orders**; extend it only from new real samples — do not guess.
   - **Committed fixtures are synthetic** (`tests/fixtures/wholefoods/synthetic-*.txt`): made-up orders in the exact real format, covering every case below. Golden tests run against these.
   - **Real orders stay local** in `tests/fixtures/wholefoods/private/` (git-ignored; purchase history doesn't go to GitHub). Tests that use them must skip cleanly when the folder is empty.
   - Observed format (Oct 2026): the paste is just the item list. It may start with an `Items in your order` header and end with a `Collapse` footer (full paste), or have neither (user-trimmed paste). Each item is a block:
     ```
     B07FYYBG8J-4                      ← product ID (ASIN) + "-" + position in order
     Roma Tomato                       ← product title
     $1.75                             ← line total actually paid
     Qty: 0.65 lb @ $2.69/lb           ← "Qty: N @ $X each" or "Qty: W lb @ $X/lb"
     Tare Weight: 0.01 lb              ← optional (weighed items)
     $2.12 promotions applied          ← optional
     ```
   - The ASIN is stable across orders, so it is the **first lookup key** for alias mapping (before any title matching).
   - Trust the `Qty` line over the title for units (e.g. "Zucchini Squash, 1 Each" is sold by the lb).
2. **Strip boilerplate:** the `Items in your order` header and `Collapse` footer; tolerate (ignore) any other non-item lines.
3. **Extract line items:** ASIN, product title, quantity (count or weight + unit), unit price, line total (keep for optional cost tracking).
4. **Order events (substitutions, refunds):** not handled by the parser in v1. The user fixes these on the review screen or with manual entry (§4.2).
4a. **Discard ready-to-eat store items:** Whole Foods prepared-food and bakery-counter lines are dropped entirely, not added to inventory, because they aren't cooking ingredients (and hot-bar boxes have unknown contents). Seed rules: titles starting with `PREPARED FOODS` (e.g. `PREPARED FOODS Hot Bar`) or `Pastry Single` (e.g. `Pastry Single Less Than 6, 1 Each`). Anything else the user marks "ignore" on the review screen is remembered by ASIN (see step 7).
5. **Normalize product titles:** remove brand prefixes ("365 by Whole Foods Market", "Organic"), package sizes ("5 oz", "12 ct"), marketing words.
6. **Map to canonical ingredient** via `ingredient_aliases` (exact → fuzzy match with rapidfuzz → optional LLM → else ask user).
7. **Classify non-food** (paper towels, soap) → ignore list, learned over time.
8. **Review screen:** show each parsed line → proposed ingredient → storage location → estimated expiry. User can fix any mapping in one tap; fixes are saved as `confirmed` aliases, so the same product never needs correcting twice.

Design so the parser is **rule-based first, LLM-assisted second**. After a few weeks of confirmed aliases, most orders should need zero LLM calls.

### 4.2 Other inputs
- **Manual entry (required for v1):** add, edit, or remove any inventory item by hand from the inventory screen (ingredient, quantity, location, purchase date). This also covers substitutions, refunds, and anything bought outside Whole Foods.
- **Quick add (free text):** "bought milk, a dozen eggs, 2 avocados, cilantro". Simple rule parser; LLM fallback.
- **Generic receipt paste** (other stores): same pipeline with a generic boilerplate stripper.
- **Later:** receipt photo (OCR → same pipeline), barcode scan (Open Food Facts lookup). Keep the parser interface pluggable: `Parser.parse(raw) -> list[ParsedLine]`.

### 4.3 Consumption
- **Cooking a recipe** consumes linked inventory items (whole item by default; user can mark "used half").
- **Manual:** "used up", "threw out", "still have some" from the inventory screen or a check-in.
- **Staples** (`is_staple`) are never consumed or nagged about unless the user marks them out.

---

## 5. The intelligence: scoring & check-ins

Implement as pure, deterministic, unit-tested functions first. No LLM is needed for any of this.

### 5.1 Recommendation score
For each candidate recipe `r` at time `t`:

```
score(r) =  w_rating   * R(r)        # household preference
          + w_expiry   * U(r)        # use what's about to spoil
          + w_novelty  * N(r)        # haven't had it in a while
          - w_fatigue  * F(r)        # had it a lot recently
          + w_avail    * A(r)        # can I make it with what I have?
          + w_try      * T(r)        # "want to try" boost for untried recipes
```

- **R (rating):** mean of each household member's latest rating, normalized to 0–1. Unrated → neutral prior (e.g. 0.6). Optionally weight by who's eating tonight. **[DECIDE]**
- **U (urgency):** for each inventory item the recipe would use, urgency = `confidence × f(days_to_expiry)` where `f` rises sharply as expiry approaches (e.g. `exp(-days/2)`), zero for staples. Sum, cap at 1.
- **N (novelty):** `1 − exp(−days_since_last_cooked / τ_novelty)`. With τ ≈ 21 days, a recipe last made 3 weeks ago scores ~0.63, 2 months ago ~0.94. Never-cooked library recipes get N = 1.
- **F (fatigue — the hysteresis):** `Σ over past cook events of exp(−Δt / τ_fatigue)`, τ ≈ 7 days. Three times in two weeks → large penalty that decays smoothly. This naturally produces "recommend favorites with decreasing frequency when repeated."
- **A (availability):** fraction of non-staple, non-optional ingredients present (weighted by confidence). Missing ingredients become a "you'd need: …" list.
- **T (try):** boost for recipes tagged `want-to-try` in Mealie that haven't been cooked yet, so the library grows.

**Selection:** don't always return the argmax — sample the top suggestions with a softmax at temperature `T` so suggestions don't get stale. Expose `T` and all weights in a settings screen and in config.

**Always return the breakdown** (per-term contributions) and log it.

### 5.2 Inventory confidence (drives check-ins)
Each `inventory_item` carries `confidence` = P(we still have it and it's usable).

- Starts at 1.0 on purchase or confirmation.
- Decays over time with a curve keyed to shelf life: e.g. stays ~1 until 60% of shelf life, then declines; drops to near 0 well past expiry.
- Extra decay if it's a frequently used category and no cook event touched it (it may have been eaten as a snack/side without logging).
- Snaps to 1.0 on "still have it"; item closed on "gone".

### 5.3 Check-in generation
Nightly (and on app open), find items where **the answer would change a decision**:
- confidence in the uncertain band (e.g. 0.3–0.7), **and**
- the item affects top recommendations or is near expiry, **and**
- not asked in the last N days; max K questions per day per household (default 3) to avoid nagging.

Deliver as: an in-app card ("Still have: cilantro? [Yes] [Some] [Gone]") and optional push notification (§7.3). Rotate who gets asked. **[DECIDE]**

### 5.4 Tuning plan
Start with hand-picked constants. Log everything. After a month, use `recommendation_log` + `cook_events` to see whether suggestions were followed and adjust. (No ML needed; just look at the data.)

---

## 6. Do we need an LLM? (Analysis + options)

### 6.1 What the LLM is *not* needed for
Scoring, variety/fatigue, confidence decay, check-in selection — all deterministic math (§5). These should never depend on an LLM.

### 6.2 Where an LLM genuinely helps
| Task | Without LLM | With LLM | Priority |
|---|---|---|---|
| Map "365 Organic Baby Spinach 5 oz" → spinach | fuzzy match + alias table + ask user once | better first-time guesses | Medium — alias table gets you most of the way |
| Parse messy/unknown receipt formats | per-store rules | generic fallback | Medium |
| Estimate shelf life for unknown items | category default | sensible guess | Low |
| Natural-language queries ("something quick with the chicken") | filters/tags | conversational | Nice-to-have |
| Friendly explanation of suggestions | templated text from breakdown | prose | Low |
| Parse recipe ingredient lines into foods | Mealie's own parser | — | Mealie handles this |

**Recommendation:** build v1 with **no LLM dependency**. Put all LLM use behind a single interface (`LLMClient` with `complete_json(schema, prompt)`), speaking the **OpenAI-compatible API**, so the backend can be swapped by config: none / local Ollama / cloud API. Every LLM-backed step must have a non-LLM fallback and must return **schema-validated JSON** (use structured output + Pydantic validation; on failure, fall back to asking the user).

### 6.3 Local LLM options
- **Runtime: Ollama.** Runs in Docker, exposes an OpenAI-compatible API, supports tool calling and structured outputs. **[VERIFY]** version at build time.
- **Model sizing depends on NAS hardware. [DECIDE: share CPU model, RAM, and whether there's a GPU.]**
  - **CPU-only, ≤16 GB RAM:** small 3–4B models (e.g. Qwen3/3.5 4B, Gemma 4 E4B, Phi-4-mini, Llama 3.2 3B). Fine for the short, structured extraction tasks above; slow-ish but acceptable for background jobs.
  - **CPU-only, 32 GB+ RAM:** a mixture-of-experts model like Qwen3 30B-A3B — only ~3B params active per token, so it runs surprisingly well on CPU while being noticeably smarter.
  - **GPU 8–12 GB:** 8–12B class (Qwen3 8B, Gemma 4 12B).
  - **[VERIFY]** model tags on the Ollama library at build time — this space changes monthly.
- **Alternative: run Ollama on a desktop GPU** elsewhere on the LAN and point Chef Helper at it, with the NAS as fallback/none.
- **Alternative: cloud API** (Claude, etc.) for the alias-mapping step only. Tiny token volumes (a grocery order is a few hundred tokens), so cost is negligible; tradeoff is that grocery lists leave the house. Make it opt-in.
- Benchmark: build a small eval set from confirmed aliases (raw product text → expected ingredient) and measure each candidate model's accuracy and latency before choosing.

### 6.4 Mealie's built-in AI hook
Mealie has optional AI-provider integration (used for things like ingredient parsing and image import). **[VERIFY]** whether it can point at an OpenAI-compatible base URL (i.e. your Ollama). If so, both apps can share one Ollama instance.

---

## 7. Deployment on HexOS

### 7.1 Background facts **[VERIFY all]**
- HexOS is built on **TrueNAS SCALE**. Modern TrueNAS SCALE runs apps on **plain Docker / Docker Compose** (the old Kubernetes layer is gone).
- HexOS "install scripts" are **JSON** files that automate app deployment (storage mounts, directory creation, permissions, networking, resources). They configure the `app_values` of **TrueNAS catalog apps**. Community catalog: `github.com/eshtek/hexos-app-catalog`; docs: `docs.hexos.com` (Schema + Macros reference).
- Useful macros seen in the curated Mealie script: `$LOCATION(ApplicationsCapacity)`, `$LOCATION(ApplicationsPerformance)`, `$HOST_PATH(...)`, `$SERVER_LAN_IP`, `$RANDOM_STRING(n)`. Directory entries can declare an `owner` and a `snapshot` id.
- For non-catalog containers, TrueNAS supports **Apps → Discover → ⋮ → Install via YAML**, which accepts Docker Compose (including an `include:` pointing at a compose file stored on a dataset).

### 7.2 Recommendation: Docker, not bare metal
- **Do not install on bare metal.** TrueNAS/HexOS is an appliance OS; the root filesystem is managed and updates can wipe manual changes. Everything goes in containers with data on ZFS datasets.
- **Deploy order:**
  1. **Mealie** via HexOS's curated one-click install (easiest; gets correct storage + snapshots).
  2. **Chef Helper** as a TrueNAS **Custom App via YAML**, using a compose file kept on a dataset (`include:` pattern) so updates are `git pull` + redeploy.
  3. **Ollama** (optional) as its own container — TrueNAS catalog app if available, else Custom App YAML. **[VERIFY]**
  4. **ntfy** (optional) for push notifications.
- **Networking:** put Chef Helper, Mealie, Ollama on a shared Docker network, or address Mealie via `http://$SERVER_LAN_IP:<port>`. Note Mealie's curated script publishes on port **30111** and sets `base_url` accordingly. **[VERIFY]** on your install.
- **Remote access:** use **Tailscale** (available as a TrueNAS app) rather than exposing ports to the internet. No public exposure in v1.

### 7.3 HexOS-specific "hooks" to build
These make Chef Helper a good HexOS citizen:
1. **Config via environment variables only** (`MEALIE_URL`, `MEALIE_TOKEN`, `DATABASE_URL`, `LLM_BASE_URL`, `LLM_MODEL`, `NTFY_URL`, `TZ`, `PUID/PGID`). No config baked into the image.
2. **Single data dir** (`/data`) for DB, uploads, backups → mounted from `ApplicationsCapacity` (or `ApplicationsPerformance` for the DB if you have SSD pool). Enables **ZFS snapshots** of exactly the app's state.
3. **Runs as UID/GID 568** (`apps`) so permissions match HexOS-created directories.
4. **`/healthz` endpoint** + Docker `HEALTHCHECK` so TrueNAS shows app health.
5. **Startup migrations** (Alembic upgrade on boot) so redeploys "just work."
6. **Nightly SQLite backup** (`.backup` to `/data/backups`, keep N) in addition to ZFS snapshots.
7. **Low resource limits** (Chef Helper should idle under ~200 MB RAM); declare limits in compose.
8. **Publish a HexOS install script JSON** (`deploy/hexos/chef-helper.json`) modeled on `mealie.json`, with `requirements.locations`, `ensure_directories_exists` (with `snapshot`), and `$RANDOM_STRING` for secrets. **[VERIFY]** whether HexOS install scripts can target a custom (non-catalog) image; if not, ship the compose YAML for "Install via YAML" as the supported path and keep the JSON as a future item.
9. **Image published to GHCR** with semver tags so TrueNAS can update by tag bump.

### 7.4 Repo layout (suggested)
```
chef-helper/
  app/
    api/            # FastAPI routers
    core/           # config, db, scheduler
    domain/         # scoring.py, confidence.py, checkins.py  (pure functions)
    ingest/         # parsers: wholefoods.py, freetext.py, normalize.py, aliases.py
    integrations/   # mealie_client.py, llm_client.py, notify.py
    web/            # templates/static or SPA build output
  migrations/
  tests/
    fixtures/wholefoods/
  deploy/
    compose.yaml
    compose.dev.yaml
    hexos/chef-helper.json
    truenas-install-via-yaml.md
  Dockerfile
  CLAUDE.md
  README.md
```

---

## 8. Phased roadmap

Each phase is independently useful. Don't start the next until acceptance criteria pass.

### Phase 0 — Environment & Mealie
- Install Mealie on HexOS (curated script). Create household + user accounts. Generate an API token.
- Import 10–20 recipes you've actually cooked.
- Local dev: `compose.dev.yaml` runs Mealie + Chef Helper on the dev machine.
- **Accept:** can list recipes from Mealie's API with the token from a script.

### Phase 1 — Skeleton + recipe sync + cook log + ratings
- FastAPI app, DB, migrations, `/healthz`, Dockerfile, simple auth (household user picker + shared password, or Mealie-backed login **[DECIDE]**).
- `mealie_client` with recipe list/get; nightly + on-demand sync into `recipes_cache`.
- Ingredient canonicalization for recipe ingredients (use Mealie's parsed "food" where available).
- UI: recipe list, "I cooked this" (who, when, optional rating), per-person ratings.
- **Accept:** two household members can log cooks and rate the same recipe; history visible; Mealie "last made" updated.

### Phase 2 — Inventory + Whole Foods paste
- Collect fixtures; build parser with golden tests; alias table; review screen.
- Inventory screen grouped by location and "use soon."
- Shelf-life seeding (FoodKeeper/category defaults); expected-expiry computation.
- Cooking a recipe consumes inventory.
- **Accept:** pasting a real Whole Foods order yields correct items with ≤ a few manual fixes; a second paste of similar items needs ~zero fixes.

### Phase 3 — Recommendation engine
- Implement §5.1 as pure functions with exhaustive unit tests (fatigue after repeats, novelty growth, urgency near expiry, softmax sampling determinism with a seed).
- "What should we cook?" screen with top N, score breakdown, and "you'd need" list.
- Settings for weights/τ/temperature.
- **Accept:** simulated 60-day cooking history test shows favorites recurring but not repeating back-to-back, and soon-to-expire ingredients pulling recipes up.

### Phase 4 — Confidence decay + check-ins + notifications
- §5.2 and §5.3; in-app check-in cards; ntfy (or Home Assistant) push.
- Rate limiting and user rotation.
- **Accept:** check-ins appear only for genuinely uncertain, decision-relevant items; ≤ configured daily max.

### Phase 5 — HexOS production deploy
- Publish image; compose for "Install via YAML"; data on datasets with snapshots; backups; Tailscale access; install doc.
- Optional: HexOS install-script JSON.
- **Accept:** clean install on the NAS from docs alone; survives reboot and image update with data intact.

### Phase 6 — Optional LLM layer
- `LLMClient` abstraction; Ollama container; eval set from confirmed aliases; pick model by accuracy/latency on your hardware.
- Wire into: alias mapping fallback, free-text quick add, unknown-format receipts, natural-language "what can I make" queries.
- **Accept:** LLM fallback measurably reduces manual fixes on new products; app fully functions with LLM disabled.

### Later ideas
Receipt photo OCR, barcode scanning, meal planning for the week (write plan into Mealie's meal planner), shopping list generation into Mealie, seasonal tags, "who's eating tonight" weighting, cost per meal.

---

## 9. Owner decisions (answered 2026-10-20)
These override any conflicting suggestion elsewhere in this plan.

1. **NAS hardware:** Ryzen 5 4500 (6C/12T, no iGPU), 32 GB DDR4-3200 (2×16, CL16), Intel Arc A380 6 GB. → CPU-only MoE (e.g. Qwen3 30B-A3B) is feasible; the A380 can run ~4–8B quantized models. **[VERIFY]** Ollama's Intel Arc support (IPEX-LLM / Vulkan) at Phase 6. NAS is not yet set up; develop locally until Phase 5.
2. **Users:** up to ~10. Ratings are **per-person**.
3. **Frontend:** **server-rendered Jinja + htmx**, installable as a PWA.
4. **Auth:** **individual accounts in Chef Helper**, plus a **household join code** to join an existing household. **Multiple households per instance must be supported**: every household-owned table carries `household_id`, and all queries are scoped by it. Each household maps to its own Mealie household. **[VERIFY]** Mealie's household/group API when building this.
5. **Notifications:** **in-app only.** No ntfy / Home Assistant; drop `NTFY_URL`.
6. **Cloud LLM:** **not allowed.** LLM use is local-only (or none).
7. **"Want to try" recipes:** live **in the same Mealie library, tagged `want-to-try`** (option a; Mealie is the recipe manager, we don't build one). Chef Helper reads the tag during recipe sync; the `T(r)` score term applies to tagged recipes that have no cook events yet. After the first cook, Chef Helper can remove the tag via the Mealie API. **[VERIFY]** tag add/remove endpoints in Mealie's API.

Still open (minor, decide when the relevant phase starts):
- Whether `R(r)` is weighted by who's eating tonight (§5.1).
- How check-in questions rotate between household members (§5.3).

---

## 10. Guidance for Claude Code while building
- Keep `domain/` pure (no I/O) and heavily tested; this is where the product's value lives.
- Never let the app hard-depend on Mealie being up for read paths that only need local data (inventory, check-ins); degrade gracefully.
- Never let the app hard-depend on an LLM.
- Treat pasted text as untrusted input (size limits, no HTML rendering of raw paste).
- Write a `CLAUDE.md` at repo root summarizing conventions, commands (`make test`, `make dev`), and pointing to this plan.
- When a fact here is marked **[VERIFY]**, check the live docs / OpenAPI spec before coding against it.
