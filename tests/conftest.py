"""Shared pytest fixtures.

Every test that touches the database gets its own throwaway SQLite file in a
temporary folder, so tests never see each other's data or the dev database.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
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
def client(test_settings: Settings) -> Iterator[TestClient]:
    """A test client for a fully started app (migrations already applied).

    Using TestClient as a context manager runs the app's startup and
    shutdown, just like uvicorn does.
    """
    with TestClient(create_app(test_settings)) as test_client:
        yield test_client
