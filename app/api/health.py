"""Liveness endpoint used by Docker's HEALTHCHECK and TrueNAS (plan §7.3)."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Report that the app process is up and serving requests.

    Deliberately does not call Mealie: the app should report healthy even
    when Mealie is down (plan §10, degrade gracefully). A database check
    will be added once the database layer exists.
    """
    return {"status": "ok"}
