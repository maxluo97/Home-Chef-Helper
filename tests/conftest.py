"""Shared pytest fixtures.

Every test that touches the database gets its own throwaway SQLite file in a
temporary folder, so tests never see each other's data or the dev database.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Settings pointing at a fresh, empty data dir for this test only."""
    # Explicit values win over env vars and .env, so the developer's own
    # configuration can't leak into tests.
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        database_url="",
        mealie_url="http://mealie.test",
    )


@pytest.fixture
def app_under_test(test_settings: Settings) -> FastAPI:
    """A fresh app instance using this test's settings (not yet started)."""
    return create_app(test_settings)


@pytest.fixture
def client(app_under_test: FastAPI) -> Iterator[TestClient]:
    """A test client for a fully started app (migrations already applied).

    Using TestClient as a context manager runs the app's startup and
    shutdown, just like uvicorn does.
    """
    with TestClient(app_under_test) as test_client:
        yield test_client


@pytest.fixture
def other_client(app_under_test: FastAPI, client: TestClient) -> TestClient:
    """A second browser on the same running app, with its own cookies.

    Depends on `client` so the app is already started; use the pair to act
    as two different people (or one person on two devices).
    """
    return TestClient(app_under_test)
