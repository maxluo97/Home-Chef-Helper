"""Pages for logging in, signing up, and managing households."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.api.auth import set_session_cookie
from app.api.deps import SESSION_COOKIE_NAME, AppSettings, DbSession
from app.api.mealie import MealieClientFactoryDep
from app.api.schemas import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH
from app.core.security import normalize_username
from app.integrations.mealie_client import MealieUnavailableError
from app.services import accounts, mealie_link
from app.web.deps import WebActiveHousehold, WebLoginSession, redirect, safe_next_path
from app.web.templating import page_context, templates

router = APIRouter(include_in_schema=False)  # pages aren't part of the JSON API docs

# Form text fields, trimmed by the browser's required/maxlength rules too.
FormText = Annotated[str, Form()]


# --- Home ---


@router.get("/")
def home(login_session: WebLoginSession) -> Response:
    """Start page: the recipe list (or the household picker if none yet)."""
    return RedirectResponse("/recipes" if login_session.active_household_id else "/households", status_code=303)


# --- Log in / sign up / log out ---


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: DbSession, settings: AppSettings, next: str = "/") -> Response:
    """The login form."""
    return templates.TemplateResponse(
        request, "login.html", page_context(request, db, settings, None, next_path=safe_next_path(next))
    )


@router.post("/login")
def login_submit(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    username: FormText,
    password: FormText,
    next: Annotated[str, Form()] = "/",
) -> Response:
    """Check the password; on success set the login cookie and go on."""
    user = None
    try:
        user = accounts.authenticate(db, normalize_username(username), password)
    except ValueError:
        pass  # malformed username: same vague failure as a wrong password
    if user is None:
        context = page_context(request, db, settings, None, error="Wrong username or password.",
                               username=username, next_path=safe_next_path(next))
        return templates.TemplateResponse(request, "login.html", context, status_code=400)

    session_token = accounts.start_login_session(db, user, timedelta(days=settings.session_lifetime_days))
    response = redirect(request, safe_next_path(next))
    set_session_cookie(response, session_token, settings)
    return response


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, db: DbSession, settings: AppSettings) -> Response:
    """The sign-up form."""
    return templates.TemplateResponse(request, "signup.html", page_context(request, db, settings, None))


@router.post("/signup")
def signup_submit(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    display_name: FormText,
    username: FormText,
    password: FormText,
) -> Response:
    """Create the account, log in, and go pick a household."""

    def show_error(error_message: str) -> Response:
        context = page_context(request, db, settings, None, error=error_message,
                               display_name=display_name, username=username)
        return templates.TemplateResponse(request, "signup.html", context, status_code=400)

    # --- Validate (same rules as the JSON API) ---
    try:
        normalized_username = normalize_username(username)
    except ValueError as invalid_username:
        return show_error(str(invalid_username))
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        return show_error(f"Password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters.")
    if not display_name.strip():
        return show_error("Please enter your name.")

    try:
        user = accounts.create_user(db, normalized_username, password, display_name.strip()[:100])
    except accounts.UsernameTakenError:
        return show_error("That username is taken.")

    session_token = accounts.start_login_session(db, user, timedelta(days=settings.session_lifetime_days))
    response = redirect(request, "/households")
    set_session_cookie(response, session_token, settings)
    return response


@router.post("/logout")
def logout_submit(request: Request, db: DbSession) -> Response:
    """End this device's session and return to the login page."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        accounts.end_login_session(db, session_token)
    response = redirect(request, "/login")
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


# --- Households: pick, create, join, switch ---

# Error codes passed back to the household page in the URL. Only these fixed
# messages can be shown, so a crafted link can't put arbitrary text on it.
HOUSEHOLD_ERRORS = {
    "no_name": "Please enter a name.",
    "bad_code": "No household has that join code.",
    "not_member": "You are not a member of that household.",
}


@router.get("/households", response_class=HTMLResponse)
def households_page(request: Request, login_session: WebLoginSession, db: DbSession, settings: AppSettings,
                    error: str | None = None) -> Response:
    """List my households; forms to create or join one."""
    context = page_context(request, db, settings, login_session, error=HOUSEHOLD_ERRORS.get(error or ""))
    return templates.TemplateResponse(request, "households.html", context)


@router.post("/households/create")
def create_household_submit(request: Request, login_session: WebLoginSession, db: DbSession,
                            name: FormText) -> Response:
    """Create a household (it becomes active) and open its settings."""
    if not name.strip():
        return redirect(request, "/households?error=no_name")
    accounts.create_household(db, login_session, name.strip()[:100])
    return redirect(request, "/household/settings")


@router.post("/households/join")
def join_household_submit(request: Request, login_session: WebLoginSession, db: DbSession,
                          join_code: FormText) -> Response:
    """Join by code (it becomes active) and go to its recipes."""
    try:
        accounts.join_household(db, login_session, join_code)
    except accounts.InvalidJoinCodeError:
        return redirect(request, "/households?error=bad_code")
    return redirect(request, "/recipes")


@router.post("/households/{household_id}/switch")
def switch_household_submit(request: Request, household_id: int, login_session: WebLoginSession,
                            db: DbSession) -> Response:
    """Switch the active household on this device."""
    try:
        accounts.switch_household(db, login_session, household_id)
    except accounts.NotAMemberError:
        return redirect(request, "/households?error=not_member")
    return redirect(request, "/recipes")


# --- Household settings (active household) ---


def render_settings(request: Request, db: DbSession, settings: AppSettings, login_session: WebLoginSession,
                    household_id: int, message: str | None = None, message_kind: str = "ok",
                    status_code: int = 200) -> Response:
    """Render the settings page with an optional result message."""
    context = page_context(request, db, settings, login_session, members=accounts.list_members(db, household_id),
                           message=message, message_kind=message_kind)
    return templates.TemplateResponse(request, "household_settings.html", context, status_code=status_code)


@router.get("/household/settings", response_class=HTMLResponse)
def household_settings_page(request: Request, login_session: WebLoginSession, household: WebActiveHousehold,
                            db: DbSession, settings: AppSettings) -> Response:
    """Mealie connection, join code, and members of the active household."""
    return render_settings(request, db, settings, login_session, household.id)


@router.post("/household/join-code")
def regenerate_join_code_submit(request: Request, login_session: WebLoginSession, household: WebActiveHousehold,
                                db: DbSession, settings: AppSettings) -> Response:
    """Issue a new join code."""
    accounts.regenerate_join_code(db, login_session.user_id, household.id)
    return render_settings(request, db, settings, login_session, household.id,
                           message="New join code made. The old one no longer works.")


@router.post("/household/mealie")
def connect_mealie_submit(request: Request, login_session: WebLoginSession, household: WebActiveHousehold,
                          db: DbSession, settings: AppSettings, make_client: MealieClientFactoryDep,
                          api_token: FormText) -> Response:
    """Check a pasted Mealie token and save it."""
    try:
        mealie_link.connect_mealie(db, settings, make_client, login_session.user_id, household.id,
                                   api_token.strip())
    except mealie_link.MealieNotConfiguredError:
        message = "The server has no CHEF_SECRET_KEY set, so Mealie can't be connected. Ask whoever runs it."
    except mealie_link.MealieTokenRejectedError:
        message = "Mealie rejected that token. Copy it again (it's only shown once) or make a new one."
    except MealieUnavailableError:
        message = "Mealie could not be reached. Is it running?"
    else:
        return render_settings(request, db, settings, login_session, household.id,
                               message="Connected to Mealie. Go to Recipes and press Sync to bring them in.")
    return render_settings(request, db, settings, login_session, household.id, message=message,
                           message_kind="error", status_code=400)


@router.post("/household/mealie/disconnect")
def disconnect_mealie_submit(request: Request, login_session: WebLoginSession, household: WebActiveHousehold,
                             db: DbSession, settings: AppSettings) -> Response:
    """Forget the household's Mealie token."""
    mealie_link.disconnect_mealie(db, login_session.user_id, household.id)
    return render_settings(request, db, settings, login_session, household.id,
                           message="Disconnected from Mealie.", message_kind="warn")
