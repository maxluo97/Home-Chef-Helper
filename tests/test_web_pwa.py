"""Tests for the installable-app (PWA) files."""

from fastapi.testclient import TestClient


def test_manifest_lists_existing_icons(client: TestClient) -> None:
    """The manifest is valid JSON with the right type, and every icon exists."""
    response = client.get("/manifest.webmanifest")

    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = response.json()
    assert (manifest["display"], manifest["start_url"]) == ("standalone", "/recipes")
    for icon in manifest["icons"]:
        icon_response = client.get(icon["src"])
        assert icon_response.status_code == 200
        assert icon_response.content.startswith(b"\x89PNG")
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])


def test_service_worker_served_from_root(client: TestClient) -> None:
    """The service worker is JavaScript at /sw.js and isn't cached stale."""
    response = client.get("/sw.js")

    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "no-cache"
    assert "addEventListener" in response.text


def test_pages_link_the_manifest(client: TestClient) -> None:
    """Every page (here: login) points browsers at the manifest and registers the worker."""
    page = client.get("/login").text

    assert '<link rel="manifest" href="/manifest.webmanifest">' in page
    assert 'serviceWorker.register("/sw.js")' in page
