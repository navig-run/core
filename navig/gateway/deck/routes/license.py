"""
Deck-side license HTTP endpoints.

GET  /api/deck/license/status
   Returns the verified LicenseStatus as JSON. Called by the Deck on boot
   to decide whether to render TRIAL MODE or the full UI.

GET  /api/deck/license/raw
   Returns the raw token string (for Deck-side client-side re-verification,
   so the Deck can prove to itself the daemon didn't fake the status).

POST /api/deck/license/paste
   Body: {"token": "NAVIG-LICENSE-v1:..."}
   Validates the token; on success persists it; returns the new status.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def handle_deck_license_status(request: "web.Request") -> "web.Response":
    """Return the verified entitlement for the daemon's current license."""
    try:
        from navig.license import current_status
        status = current_status()
        return web.json_response(status.as_dict())
    except Exception as exc:  # noqa: BLE001
        logger.exception("license status endpoint failed")
        return _err(str(exc))


async def handle_deck_license_raw(request: "web.Request") -> "web.Response":
    """Return the raw persisted token so the Deck can re-verify client-side.

    This is INTENTIONALLY exposed: the closed-source Deck bundles the same
    public-key set as the daemon and runs the same signature check. Giving
    it the raw token lets it prove the daemon didn't hand back a lying
    /status payload. Both sides agree or the UI shows trial mode.
    """
    try:
        from navig.license import read_raw_token
        token = read_raw_token()
        return web.json_response({"token": token})
    except Exception as exc:  # noqa: BLE001
        logger.exception("license raw endpoint failed")
        return _err(str(exc))


async def handle_deck_license_paste(request: "web.Request") -> "web.Response":
    """Accept a license token, validate, persist, return the new status."""
    try:
        body: Any = await request.json()
    except Exception:
        return _err("invalid_json", status=400)
    if not isinstance(body, dict):
        return _err("expected object", status=400)
    token = str(body.get("token") or "").strip()
    if not token:
        return _err("missing 'token'", status=400)

    from navig.license import paste_license
    status = paste_license(token)
    if not status.valid:
        # 400 so the activation page knows the paste failed
        return web.json_response(
            {"ok": False, "reason": status.reason, **status.as_dict()},
            status=400,
        )
    return web.json_response({"ok": True, **status.as_dict()})
