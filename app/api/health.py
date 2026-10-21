"""Liveness endpoint used by Docker's HEALTHCHECK and TrueNAS (plan §7.3)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.db import get_session

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(session: Annotated[Session, Depends(get_session)]) -> JSONResponse:
    """Report whether the app is up and can reach its own database.

    Deliberately does not call Mealie: the app should report healthy even
    when Mealie is down (plan §10, degrade gracefully).
    """
    # --- Database round trip ---
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        # 503 tells Docker/TrueNAS the container is unhealthy.
        return JSONResponse(status_code=503, content={"status": "error", "database": "unreachable"})

    return JSONResponse(content={"status": "ok", "database": "ok"})
