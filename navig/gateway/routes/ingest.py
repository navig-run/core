"""Inbound Signals ingest: POST /api/ingest/{source}

Your own website/backend fires HMAC-signed events here; each verified event is
mapped onto a notification type and handed to ``notify.dispatch``, so it lands in
the deck (bell + Inbox + toast) and every channel you enabled for that type in
Settings → Notifications.

Self-authenticating: no deck/Bearer auth sits on ``/api/*`` (only ``/api/deck/*``
is gated), so this route validates the per-source HMAC itself — exactly like the
SMS/Telegram webhook routes do for their providers.

Reachability without a tunnel is Lighthouse: the edge forwards the opaque public
path ``/ingest/<tenant>/<source>`` down the uplink, which rewrites it to this
loopback route. See ``navig/cloud/uplink.py``.
"""

from __future__ import annotations

import collections
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web
    from navig.gateway.server import NavigGateway  # noqa: F401

try:
    from aiohttp import web
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("aiohttp is required for gateway routes") from exc

logger = logging.getLogger("navig.notify")

_MAX_BODY = 256 * 1024  # 256 KB — signal payloads are small JSON
# Replay-dedupe within the timestamp window: (source, signature) seen recently.
_SEEN: "collections.OrderedDict[str, None]" = collections.OrderedDict()
_SEEN_CAP = 2000


def register(app: "web.Application", gateway: "NavigGateway") -> None:
    """Register the ingest route (public — your websites POST here, signed)."""
    app.router.add_post("/api/ingest/{source}", _handler)


def _seen(key: str) -> bool:
    if key in _SEEN:
        _SEEN.move_to_end(key)
        return True
    _SEEN[key] = None
    while len(_SEEN) > _SEEN_CAP:
        _SEEN.popitem(last=False)
    return False


async def _handler(request: "web.Request") -> "web.Response":
    from navig.notify import signals

    source_name = request.match_info.get("source", "")
    src = signals.get_source(source_name)
    if src is None or not src.get("enabled", True):
        return web.json_response({"ok": False, "error": "unknown_source"}, status=404)

    body = await request.read()
    if len(body) > _MAX_BODY:
        return web.json_response({"ok": False, "error": "payload_too_large"}, status=413)

    result = signals.verify_and_render(src, dict(request.headers), body)
    if not result.ok:
        return web.json_response(
            {"ok": False, "error": result.error}, status=result.http_status
        )

    # Replay defence #2: drop a re-fired identical signature inside the window.
    if _seen(f"{source_name}:{result.signature}"):
        return web.json_response({"ok": True, "duplicate": True})

    try:
        from navig.notify import dispatch as notify_dispatch

        outcome = await notify_dispatch(
            result.notify_type,
            result.title,
            result.body,
            priority=result.priority,
            data=result.data,
        )
        signals.record_hit(source_name)
    except Exception:  # noqa: BLE001 — never 500 the caller on a delivery hiccup
        logger.debug("signal dispatch failed for %s", source_name, exc_info=True)
        return web.json_response({"ok": False, "error": "dispatch_failed"}, status=502)

    channels = [c.get("channel") for c in (outcome.get("channels") or []) if c.get("ok")]
    return web.json_response(
        {"ok": True, "type": result.notify_type, "delivered": channels}
    )
