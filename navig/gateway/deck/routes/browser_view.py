"""Desktop browser-pane registration for the Deck API.

navig-os advertises the CDP endpoint of its VISIBLE browser pane (a WebView2 on
Windows launched with remote-debugging) so the agent attaches to and drives the
same browser the user sees — "one browser everywhere". The agent then uses its
normal ``browser_tool`` unchanged; ``browser_session`` opens the session attached
to the pane instead of launching a headless browser.

Routes:
    POST /api/deck/browser/view/register  {session, cdp_url}  -> attach agent to the pane
    POST /api/deck/browser/view/clear      {session}          -> pane closed; drop it
"""

from __future__ import annotations

import logging

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _is_loopback_cdp(url: str) -> bool:
    """Only accept a loopback CDP endpoint — never a remote/arbitrary host."""
    u = (url or "").strip().lower()
    return u.startswith("http://127.0.0.1:") or u.startswith("http://localhost:")


async def handle_deck_browser_view_register(request: "web.Request") -> "web.Response":
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    session = str(body.get("session") or "").strip()
    cdp_url = str(body.get("cdp_url") or "").strip()
    if not session or not cdp_url:
        return web.json_response({"ok": False, "error": "session and cdp_url required"}, status=400)
    if not _is_loopback_cdp(cdp_url):
        return web.json_response(
            {"ok": False, "error": "cdp_url must be loopback (127.0.0.1/localhost)"}, status=400
        )

    from navig.agent.tools.browser_session import register_desktop_endpoint

    register_desktop_endpoint(session, cdp_url)
    logger.info("[deck] browser pane registered: session=%s", session)
    return web.json_response({"ok": True})


async def handle_deck_browser_view_clear(request: "web.Request") -> "web.Response":
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}

    session = str(body.get("session") or "").strip()
    if not session:
        return web.json_response({"ok": False, "error": "session required"}, status=400)

    from navig.agent.tools.browser_session import clear_desktop_endpoint

    clear_desktop_endpoint(session)
    return web.json_response({"ok": True})
