"""FastAPI application entry point.

Run locally with:
    uv run uvicorn app.main:app --reload
"""

from fastapi import FastAPI

from app.api import health


def create_app() -> FastAPI:
    """Build the FastAPI app and mount every router.

    A factory (rather than only a module-level app) lets tests build a fresh
    app with their own settings overrides.
    """
    application = FastAPI(title="Chef Helper")

    # --- Routers ---
    application.include_router(health.router)

    return application


# The app instance uvicorn serves.
app = create_app()
