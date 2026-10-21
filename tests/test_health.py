"""Tests for the /healthz liveness endpoint."""

from fastapi.testclient import TestClient

from app.core.config import Settings


def test_healthz_returns_ok(client: TestClient) -> None:
    """GET /healthz answers 200 and confirms the database is reachable."""
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_startup_creates_data_dir_and_database(client: TestClient, test_settings: Settings) -> None:
    """Starting the app on an empty data dir creates the SQLite file."""
    assert (test_settings.data_dir / "chef-helper.db").is_file()
