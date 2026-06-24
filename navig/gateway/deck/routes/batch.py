"""Batch endpoint for the Deck API.

Collapses a screen's many GET reads into a single HTTP round-trip. The Deck
runs over a cloudflared tunnel from deck.navig.run, so each individual call
pays the full tunnel RTT plus the browser's ~6-connection HTTP/1.1 cap. A
batch lets a section load (status + ops + settings + monitor + …) cost one
request and one connection.

Design: the batch handler re-dispatches each requested GET *path* against the
gateway's own router via ``request.clone(rel_url=…)`` + ``router.resolve()``,
calling the matched handler directly. The outer batch request has already
passed deck auth + CORS middleware, so sub-handlers run post-auth; we copy the
authenticated ``deck_user_id`` onto each sub-request so handlers that read it
keep working.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging

try:
    from aiohttp import web
except ImportError:
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Only these prefixes are batchable — read surfaces the Deck loads per screen.
_ALLOWED_PREFIXES = ("/api/deck/", "/runtime/", "/mesh/")
_MAX_REQUESTS = 24


async def handle_deck_batch(request: "web.Request") -> "web.Response":
    """POST /api/deck/batch  — body: {"requests": ["/api/deck/status", ...]}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    paths = body.get("requests")
    if not isinstance(paths, list) or not paths or len(paths) > _MAX_REQUESTS:
        return web.json_response(
            {"ok": False, "error": f"'requests' must be a list of 1–{_MAX_REQUESTS} GET paths"},
            status=400,
        )

    deck_user_id = request.get("deck_user_id")

    async def _one(path: object) -> dict:
        if (
            not isinstance(path, str)
            or not path.startswith(_ALLOWED_PREFIXES)
            or path.startswith("/api/deck/batch")
        ):
            return {"path": path, "status": 400, "data": None, "error": "path not allowed"}
        try:
            # NB: clone() preserves the outer method (POST) — force GET so the
            # router resolves the read route instead of "Method Not Allowed".
            sub = request.clone(method="GET", rel_url=path)
            # Carry the authenticated user through to handlers that read it
            # (middleware doesn't run on a manually-dispatched sub-request).
            if deck_user_id is not None:
                sub["deck_user_id"] = deck_user_id

            match = await request.app.router.resolve(sub)
            if match is None or getattr(match, "http_exception", None) is not None:
                return {"path": path, "status": 404, "data": None}

            resp = await match.handler(sub)

            text = None
            if hasattr(resp, "text") and isinstance(resp.text, str):
                text = resp.text
            elif getattr(resp, "body", None) is not None:
                try:
                    text = resp.body.decode("utf-8", errors="replace")
                except Exception:
                    text = None
            try:
                data = _json.loads(text) if text else None
            except Exception:
                data = None

            return {"path": path, "status": int(getattr(resp, "status", 200)), "data": data}
        except Exception as exc:  # noqa: BLE001
            logger.debug("batch sub-request %s failed: %r", path, exc)
            return {"path": path, "status": 500, "data": None, "error": str(exc)}

    results = await asyncio.gather(*[_one(p) for p in paths])
    return web.json_response({"ok": True, "results": list(results)})
