"""
Server-Sent Events endpoint — GET /api/events

Streams real-time system events to the browser.
The client (navig-deck/lib/sse.ts) subscribes via EventSource.

Event frame format:
    data: {"type": "<event_type>", "data": {...}, "ts": "<iso>"}
    \n
    (blank line terminates the frame)

Heartbeat: comment line `: heartbeat` every 20 s keeps the connection alive
through proxies and is silently ignored by EventSource.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)

# How often (seconds) to send a SSE heartbeat comment
_HEARTBEAT_INTERVAL = 20


async def handle_api_events(request: "web.Request") -> "web.Response":
    """
    SSE endpoint consumed by navig-deck/lib/sse.ts.

    Subscribes to the gateway's SystemEventQueue (if available) and forwards
    events.  Falls back to heartbeat-only streaming when the event queue is
    absent so the connection still stays open.
    """
    # CORS headers MUST be on the StreamResponse before prepare() flushes
    # the wire. The post-handler CORS middleware can't reach them on streaming
    # responses (headers are gone by the time the handler returns), which
    # caused browser-blocked SSE after ~5s when the Relay was served from
    # https://relay.navig.run and the daemon from a *.trycloudflare.com tunnel.
    from navig.gateway.middleware import cors_headers_for
    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
            **cors_headers_for(request),
        }
    )
    await response.prepare(request)

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)

    # Subscribe to all system events if available
    gateway = request.app.get("gateway") if hasattr(request, "app") else None
    event_queue = (
        getattr(gateway, "system_events", None)
        or getattr(gateway, "event_queue", None)
    ) if gateway else None

    unsubscribe_fn = None
    if event_queue and hasattr(event_queue, "subscribe"):
        def _on_event(evt) -> None:
            try:
                payload = evt.payload if hasattr(evt, "payload") else {}
                frame = {
                    "type": getattr(evt, "event_type", "status_update"),
                    "data": payload,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                # put_nowait is safe from sync context; drop if full
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                pass
            except Exception:
                pass

        try:
            event_queue.subscribe("*", _on_event)

            def _unsub():
                try:
                    subs = getattr(event_queue, "_wildcard_subscribers", None)
                    if subs is not None and _on_event in subs:
                        subs.remove(_on_event)
                except Exception:
                    pass

            unsubscribe_fn = _unsub
        except Exception:
            pass

    async def _send(text: str) -> bool:
        """Write raw SSE text; return False if the client disconnected."""
        try:
            await response.write(text.encode())
            return True
        except (ConnectionResetError, Exception):
            return False

    try:
        while True:
            # Wait up to _HEARTBEAT_INTERVAL seconds for an event
            try:
                frame = await asyncio.wait_for(
                    queue.get(), timeout=_HEARTBEAT_INTERVAL
                )
                line = f"data: {json.dumps(frame)}\n\n"
                if not await _send(line):
                    break
            except asyncio.TimeoutError:
                # Send heartbeat comment to keep the connection alive
                if not await _send(": heartbeat\n\n"):
                    break
    finally:
        if unsubscribe_fn:
            unsubscribe_fn()

    return response


def register(app: "web.Application", gateway) -> None:
    """Register /api/events on the gateway application."""
    app.router.add_get("/api/events", handle_api_events)
