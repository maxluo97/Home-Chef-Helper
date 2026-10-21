"""FastAPI application entry point.

Run locally with:
    uv run uvicorn app.main:app --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import auth, health, households, mealie, recipes
from app.core.config import Settings, get_settings
from app.core.db import make_engine, make_session_factory
from app.core.migrate import upgrade_to_head


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app and mount every router.

    A factory (rather than only a module-level app) lets tests build a fresh
    app with their own settings, e.g. a throwaway database per test.
    """
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Startup work before serving requests; cleanup on shutdown."""
        # --- Startup: make sure the data dir exists, then bring the DB
        # schema up to date (plan §7.3: migrations run on boot). ---
        app_settings.data_dir.mkdir(parents=True, exist_ok=True)
        upgrade_to_head(app_settings.database_url)

        # One engine (connection pool) for the life of the process.
        engine = make_engine(app_settings.database_url)
        application.state.session_factory = make_session_factory(engine)

        yield

        # --- Shutdown: close pooled connections. ---
        engine.dispose()

    application = FastAPI(title="Chef Helper", lifespan=lifespan)

    # Routes that depend on get_settings() see this app's settings.
    application.dependency_overrides[get_settings] = lambda: app_settings

    # --- Routers ---
    application.include_router(health.router)
    application.include_router(auth.router)
    application.include_router(households.router)
    application.include_router(mealie.router)
    application.include_router(recipes.router)

    return application


# The app instance uvicorn serves.
app = create_app()
