"""Tests for the /healthz liveness endpoint."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_healthz_returns_ok() -> None:
    """GET /healthz answers 200 with status "ok"."""
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
