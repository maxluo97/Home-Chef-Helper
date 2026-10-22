"""Recipe pages: the library list, sync, and each recipe's page with the
"I cooked this" form, ratings, and cook history; plus the cook-log page.

Actions on the recipe page are htmx requests that return the re-rendered
"activity" section (#recipe-activity), so the page updates in place.
Without JavaScript the same forms still work and redirect back.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.cooking import try_write_back
from app.api.deps import AppSettings, DbSession
from app.api.mealie import MealieClientFactoryDep
from app.api.recipes import RecipeOut
from app.domain.ratings import latest_rating_per_user
from app.integrations.mealie_client import MealieAuthError, MealieError, MealieUnavailableError
from app.models import CookEvent, Household, Recipe, User
from app.models.base import utc_now
from app.services import cook_log, mealie_link
from app.services.accounts import list_members
from app.services.cook_log import household_recipe_stats
from app.services.mealie_writeback import write_back_household
from app.services.recipe_sync import sync_household_recipes
from app.web.deps import WebActiveHousehold, WebLoginSession, redirect
from app.web.formatting import parse_datetime_local
from app.web.templating import is_htmx, page_context, templates

router = APIRouter(include_in_schema=False)

OptionalFormText = Annotated[str, Form()]

# How many entries the cook-log page shows.
COOK_LOG_PAGE_SIZE = 100

# Result messages after logging a cook, by write-back outcome.
WRITEBACK_MESSAGES = {
    "up_to_date": ("ok", "Logged · Mealie updated"),
    "pending": ("warn", "Logged · Mealie couldn't be reached; it will be updated at the next sync"),
    "not_connected": ("warn", "Logged · this household isn't connected to Mealie"),
}


# --- View models: plain data for templates ---


@dataclass
class HistoryEntry:
    """One cook-log line as shown on a page."""

    id: int
    recipe_id: int
    recipe_name: str
    cooked_at: datetime
    cook_name: str
    servings: int | None
    notes: str | None
    cooked_by_user_id: int | None


@dataclass
class PersonRating:
    """One person's current rating."""

    display_name: str
    stars: int


def display_names(db: DbSession, user_ids: set[int | None]) -> dict[int, str]:
    """Display names for these user IDs (including people who left)."""
    known_ids = {user_id for user_id in user_ids if user_id is not None}
    if not known_ids:
        return {}
    return {user.id: user.display_name for user in db.scalars(select(User).where(User.id.in_(known_ids)))}


def history_entries(db: DbSession, cook_events: list[CookEvent]) -> list[HistoryEntry]:
    """Turn cook events into display rows."""
    names = display_names(db, {cook_event.cooked_by_user_id for cook_event in cook_events})
    recipe_names = {
        recipe.id: recipe.name
        for recipe in db.scalars(select(Recipe).where(Recipe.id.in_({event.recipe_id for event in cook_events})))
    } if cook_events else {}
    return [
        HistoryEntry(
            id=cook_event.id,
            recipe_id=cook_event.recipe_id,
            recipe_name=recipe_names.get(cook_event.recipe_id, "?"),
            cooked_at=cook_event.cooked_at,
            cook_name=names.get(cook_event.cooked_by_user_id, "someone"),
            servings=cook_event.servings,
            notes=cook_event.notes,
            cooked_by_user_id=cook_event.cooked_by_user_id,
        )
        for cook_event in cook_events
    ]


def mealie_recipe_link(settings: AppSettings, household: Household, recipe: Recipe) -> str | None:
    """Link to the recipe in Mealie's own UI, if we know the group's slug."""
    if not household.mealie_group_slug:
        return None
    return f"{settings.mealie_public_url}/g/{household.mealie_group_slug}/r/{recipe.slug}"


def get_page_recipe(db: DbSession, household: Household, recipe_id: int) -> Recipe:
    """The active household's recipe, or a 404 page."""
    try:
        return cook_log.get_household_recipe(db, household.id, recipe_id)
    except cook_log.RecipeNotFoundError:
        raise HTTPException(404, "Recipe not found.") from None


# --- Recipe list ---


def recipe_list_rows(db: DbSession, household_id: int, user_id: int, search_text: str,
                     want_to_try_only: bool) -> list[RecipeOut]:
    """Library rows matching the search, by name."""
    stats_by_recipe = household_recipe_stats(db, household_id)
    household_recipes = db.scalars(
        select(Recipe)
        .where(Recipe.household_id == household_id, Recipe.removed_at.is_(None))
        .options(selectinload(Recipe.ingredients))
        .order_by(Recipe.name)
    )
    rows = [RecipeOut.from_recipe(recipe, stats_by_recipe.get(recipe.id), user_id) for recipe in household_recipes]

    # Filters: case-insensitive name search, and the want-to-try toggle.
    lowered_search = search_text.strip().lower()
    if lowered_search:
        rows = [row for row in rows if lowered_search in row.name.lower()]
    if want_to_try_only:
        rows = [row for row in rows if row.want_to_try]
    return rows


@router.get("/recipes", response_class=HTMLResponse)
def recipes_page(request: Request, login_session: WebLoginSession, household: WebActiveHousehold,
                 db: DbSession, settings: AppSettings, q: str = "", show: str = "all") -> Response:
    """The library. htmx searches get just the list back."""
    rows = recipe_list_rows(db, household.id, login_session.user_id, q, want_to_try_only=(show == "want"))
    context = page_context(request, db, settings, login_session, recipes=rows, search_text=q, show=show)
    template_name = "_recipe_list.html" if is_htmx(request) else "recipes.html"
    return templates.TemplateResponse(request, template_name, context)


@router.post("/recipes/sync", response_class=HTMLResponse)
def sync_recipes_submit(request: Request, login_session: WebLoginSession, household: WebActiveHousehold,
                        db: DbSession, settings: AppSettings, make_client: MealieClientFactoryDep) -> Response:
    """Pull recipes from Mealie, write back pending cooks, report the result."""
    message_kind, message = "ok", ""
    try:
        with mealie_link.open_mealie_client(db, settings, make_client, household.id) as mealie:
            sync_result = sync_household_recipes(db, mealie, household.id)
            try:
                write_back_household(db, mealie, household.id)
            except MealieError:
                db.rollback()  # recipes are synced; write-back retries next time
        changes = [
            f"{count} {label}"
            for count, label in ((sync_result.added, "new"), (sync_result.updated, "updated"),
                                 (sync_result.removed, "removed"), (sync_result.restored, "restored"))
            if count
        ]
        message = "Synced: " + (", ".join(changes) if changes else "no changes")
    except (mealie_link.MealieNotConnectedError, mealie_link.MealieNotConfiguredError) as not_ready:
        message_kind, message = "warn", f"{not_ready} Set it up in Household settings."
    except MealieAuthError:
        message_kind, message = "error", "Mealie no longer accepts this household's token. Reconnect it in Household settings."
    except MealieUnavailableError:
        message_kind, message = "error", "Mealie could not be reached. Is it running?"

    if not is_htmx(request):
        return redirect(request, "/recipes")
    context = page_context(request, db, settings, login_session, message=message, message_kind=message_kind)
    response = templates.TemplateResponse(request, "_sync_status.html", context)
    # Tell the list on the page to reload itself.
    response.headers["HX-Trigger"] = "recipes-changed"
    return response


# --- Recipe page ---


def activity_context(request: Request, db: DbSession, settings: AppSettings, login_session: WebLoginSession,
                     household: Household, recipe: Recipe, **extra: Any) -> dict[str, Any]:
    """Everything the recipe page's activity section shows."""
    stats = household_recipe_stats(db, household.id).get(recipe.id)
    recipe_view = RecipeOut.from_recipe(recipe, stats, login_session.user_id)

    # Each person's current rating, best first.
    latest_by_user = latest_rating_per_user(
        cook_log.as_rating_record(stored) for stored in cook_log.recipe_ratings(db, household.id, recipe.id)
    )
    names = display_names(db, set(latest_by_user))
    people_ratings = sorted(
        (PersonRating(names.get(user_id, "?"), record.stars) for user_id, record in latest_by_user.items()),
        key=lambda person_rating: (-person_rating.stars, person_rating.display_name),
    )

    return page_context(
        request, db, settings, login_session,
        recipe=recipe_view,
        recipe_record=recipe,
        mealie_link=mealie_recipe_link(settings, household, recipe),
        history=history_entries(db, cook_log.list_cooks(db, household.id, recipe_id=recipe.id)),
        people_ratings=people_ratings,
        members=list_members(db, household.id),
        **extra,
    )


def render_activity(request: Request, db: DbSession, settings: AppSettings, login_session: WebLoginSession,
                    household: Household, recipe: Recipe, **extra: Any) -> Response:
    """The activity section alone (htmx), or a redirect to the full page."""
    if not is_htmx(request):
        return redirect(request, f"/recipes/{recipe.id}")
    context = activity_context(request, db, settings, login_session, household, recipe, **extra)
    return templates.TemplateResponse(request, "_recipe_activity.html", context)


@router.get("/recipes/{recipe_id}", response_class=HTMLResponse)
def recipe_page(request: Request, recipe_id: int, login_session: WebLoginSession, household: WebActiveHousehold,
                db: DbSession, settings: AppSettings) -> Response:
    """One recipe: log a cook, rate it, see who made it when."""
    recipe = get_page_recipe(db, household, recipe_id)
    context = activity_context(request, db, settings, login_session, household, recipe)
    return templates.TemplateResponse(request, "recipe.html", context)


@router.get("/recipes/{recipe_id}/activity", response_class=HTMLResponse)
def recipe_activity(request: Request, recipe_id: int, login_session: WebLoginSession, household: WebActiveHousehold,
                    db: DbSession, settings: AppSettings) -> Response:
    """Re-render the activity section (e.g. to cancel an edit)."""
    recipe = get_page_recipe(db, household, recipe_id)
    return render_activity(request, db, settings, login_session, household, recipe)


def optional_int(form_value: str) -> int | None:
    """'' -> None, '4' -> 4. Raises ValueError for other text."""
    return int(form_value) if form_value.strip() else None


@router.post("/recipes/{recipe_id}/cooks", response_class=HTMLResponse)
def log_cook_submit(
    request: Request,
    recipe_id: int,
    login_session: WebLoginSession,
    household: WebActiveHousehold,
    db: DbSession,
    settings: AppSettings,
    make_client: MealieClientFactoryDep,
    cooked_by_user_id: OptionalFormText = "",
    cooked_at: OptionalFormText = "",
    stars: OptionalFormText = "",
    servings: OptionalFormText = "",
    notes: OptionalFormText = "",
) -> Response:
    """The "I cooked this" form."""
    recipe = get_page_recipe(db, household, recipe_id)
    try:
        cook_log.log_cook(
            db, household.id, recipe.id, login_session.user_id,
            cooked_at=parse_datetime_local(cooked_at, settings.timezone) if cooked_at.strip() else None,
            cooked_by_user_id=optional_int(cooked_by_user_id),
            servings=optional_int(servings),
            notes=notes or None,
            stars=optional_int(stars),
        )
    except (cook_log.CookLogError, ValueError) as invalid_input:
        message = str(invalid_input) if isinstance(invalid_input, cook_log.CookLogError) else "Check the date and numbers."
        return render_activity(request, db, settings, login_session, household, recipe,
                               message=message, message_kind="error")

    writeback_status = try_write_back(db, settings, make_client, household.id, recipe.id)
    message_kind, message = WRITEBACK_MESSAGES[writeback_status]
    return render_activity(request, db, settings, login_session, household, recipe,
                           message=message, message_kind=message_kind)


@router.post("/recipes/{recipe_id}/rating", response_class=HTMLResponse)
def rate_submit(request: Request, recipe_id: int, login_session: WebLoginSession, household: WebActiveHousehold,
                db: DbSession, settings: AppSettings, stars: OptionalFormText) -> Response:
    """Tap a star to set your rating."""
    recipe = get_page_recipe(db, household, recipe_id)
    try:
        cook_log.rate_recipe(db, household.id, recipe.id, login_session.user_id, int(stars))
    except (cook_log.CookLogError, ValueError):
        return render_activity(request, db, settings, login_session, household, recipe,
                               message="Pick 1 to 5 stars.", message_kind="error")
    return render_activity(request, db, settings, login_session, household, recipe,
                           message=f"Your rating: {int(stars)} ★", message_kind="ok")


# --- Editing cook-log entries (from the recipe page) ---


@router.get("/cooks/{cook_event_id}/edit", response_class=HTMLResponse)
def edit_cook_form(request: Request, cook_event_id: int, login_session: WebLoginSession,
                   household: WebActiveHousehold, db: DbSession, settings: AppSettings) -> Response:
    """Inline edit form for one history row."""
    try:
        cook_event = cook_log.get_household_cook(db, household.id, cook_event_id)
    except cook_log.CookEventNotFoundError:
        raise HTTPException(404, "Cook-log entry not found.") from None
    context = page_context(request, db, settings, login_session, entry=history_entries(db, [cook_event])[0],
                           members=list_members(db, household.id))
    return templates.TemplateResponse(request, "_cook_edit.html", context)


@router.post("/cooks/{cook_event_id}/edit", response_class=HTMLResponse)
def edit_cook_submit(
    request: Request,
    cook_event_id: int,
    login_session: WebLoginSession,
    household: WebActiveHousehold,
    db: DbSession,
    settings: AppSettings,
    make_client: MealieClientFactoryDep,
    cooked_by_user_id: OptionalFormText = "",
    cooked_at: OptionalFormText = "",
    servings: OptionalFormText = "",
    notes: OptionalFormText = "",
) -> Response:
    """Save an edited history row."""
    try:
        cook_event = cook_log.get_household_cook(db, household.id, cook_event_id)
    except cook_log.CookEventNotFoundError:
        raise HTTPException(404, "Cook-log entry not found.") from None
    recipe = db.get(Recipe, cook_event.recipe_id)

    try:
        changes = {
            "cooked_at": parse_datetime_local(cooked_at, settings.timezone),
            "cooked_by_user_id": optional_int(cooked_by_user_id),
            "servings": optional_int(servings),
            "notes": notes.strip() or None,
        }
        cook_log.update_cook(db, household.id, cook_event_id, changes)
    except (cook_log.CookLogError, ValueError) as invalid_input:
        message = str(invalid_input) if isinstance(invalid_input, cook_log.CookLogError) else "Check the date and numbers."
        return render_activity(request, db, settings, login_session, household, recipe,
                               message=message, message_kind="error")

    try_write_back(db, settings, make_client, household.id, recipe.id)
    return render_activity(request, db, settings, login_session, household, recipe,
                           message="Entry updated.", message_kind="ok")


@router.post("/cooks/{cook_event_id}/delete", response_class=HTMLResponse)
def delete_cook_submit(request: Request, cook_event_id: int, login_session: WebLoginSession,
                       household: WebActiveHousehold, db: DbSession, settings: AppSettings,
                       make_client: MealieClientFactoryDep) -> Response:
    """Delete a history row (the page asks for confirmation first)."""
    try:
        cook_event = cook_log.get_household_cook(db, household.id, cook_event_id)
    except cook_log.CookEventNotFoundError:
        raise HTTPException(404, "Cook-log entry not found.") from None
    recipe = db.get(Recipe, cook_event.recipe_id)

    cook_log.delete_cook(db, household.id, cook_event_id)
    try_write_back(db, settings, make_client, household.id, recipe.id)
    return render_activity(request, db, settings, login_session, household, recipe,
                           message="Entry deleted.", message_kind="warn")


# --- Cook log page ---


@router.get("/cooks", response_class=HTMLResponse)
def cook_log_page(request: Request, login_session: WebLoginSession, household: WebActiveHousehold,
                  db: DbSession, settings: AppSettings) -> Response:
    """Everything the household cooked recently, newest first."""
    entries = history_entries(db, cook_log.list_cooks(db, household.id, limit=COOK_LOG_PAGE_SIZE))
    context = page_context(request, db, settings, login_session, entries=entries, now=utc_now())
    return templates.TemplateResponse(request, "cooks.html", context)
