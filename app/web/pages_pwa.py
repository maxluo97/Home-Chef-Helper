"""Files that make the site installable as an app (PWA, plan §9.3).

Served from the site root because a service worker only controls pages at
or below its own path.

Note: browsers only offer "Install" on https:// sites (or localhost). On
the NAS that means opening it through Tailscale's HTTPS address; over plain
http on the LAN, phones can still "Add to Home screen" as a shortcut.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

router = APIRouter(include_in_schema=False)

# Brand colour (matches app.css badges and the icons).
THEME_COLOR = "#d9822b"

# Minimal service worker: registering one is part of what makes browsers
# offer "Install". It deliberately does no caching yet (offline use is a
# later feature), so pages are always fresh from the server.
SERVICE_WORKER_JS = """\
// Chef Helper service worker. No offline caching yet: every request goes
// to the server as normal. Takes control immediately after an update.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
"""


@router.get("/manifest.webmanifest")
def web_manifest() -> JSONResponse:
    """App name, icons, and how it opens when installed."""
    manifest = {
        "name": "Chef Helper",
        "short_name": "Chef Helper",
        "description": "What we've cooked, how we liked it, and what to make next.",
        "start_url": "/recipes",
        "scope": "/",
        "display": "standalone",  # own window, no browser address bar
        "background_color": "#ffffff",
        "theme_color": THEME_COLOR,
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }
    return JSONResponse(manifest, media_type="application/manifest+json")


@router.get("/sw.js")
def service_worker() -> Response:
    """The service worker script (must be served from the root path)."""
    # no-cache: browsers re-check it, so updates to it take effect promptly.
    return Response(SERVICE_WORKER_JS, media_type="text/javascript", headers={"Cache-Control": "no-cache"})
