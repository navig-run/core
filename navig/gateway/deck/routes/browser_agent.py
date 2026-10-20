"""
NAVIG Dock browser agent — one streamed turn.

The Dock extension's side panel chats about the current page and drives it. The
extension owns the loop and the page (debugger input, takeover overlay); this route
owns the thinking: it streams the model's reply through the operator's AI
connections and returns a validated action batch for the extension to execute.

Routes:
    POST /api/deck/agent/browser/turn  → text/event-stream

Body::

    {"messages": [{"role": "user"|"assistant", "content": str}, ...],
     "observation": {"url": str, "title": str, "tree": str, "selection"?: str},
     "connection_id"?: str,
     "model"?: str}   # a model of that connection; "provider:model" when none is pinned

Frames (one JSON object per ``data:`` line)::

    {"type": "route",   "name", "provider", "model"}     which account answers
    {"type": "delta",   "text"}                           streamed prose
    {"type": "actions", "actions": [...], "errors": [...]} the parsed batch
    {"type": "done",    "text"}                           full reply, block removed
    {"type": "error",   "message", "category"}

Page text is never logged — only sizes and the outcome.
"""

from __future__ import annotations

import json
import logging
import time

try:
    from aiohttp import web
except ImportError:  # pragma: no cover - aiohttp always present at runtime
    web = None

logger = logging.getLogger(__name__)

_MAX_BODY_BYTES = 400_000


async def handle_deck_browser_agent_turn(request: "web.Request") -> "web.StreamResponse":
    """POST /api/deck/agent/browser/turn — stream one browser-agent turn."""
    from navig.agent.browser_turn import build_turn_messages, parse_actions, strip_actions_block
    from navig.gateway.middleware import cors_headers_for

    if (request.content_length or 0) > _MAX_BODY_BYTES:
        return web.json_response({"ok": False, "error": "request too large"}, status=413)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "expected object"}, status=400)

    try:
        messages = build_turn_messages(body.get("messages"), body.get("observation"))
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    connection_id = body.get("connection_id") if isinstance(body.get("connection_id"), str) else None
    model = body.get("model").strip() if isinstance(body.get("model"), str) else ""
    # With a pinned connection the model is a bare id of THAT connection — a leading
    # ":" keeps resolve_route from reading a colon inside the id (ollama "llama3:8b")
    # as a provider prefix. Without one, "provider:model" picks the connection.
    model_spec = (f":{model}" if connection_id else model) or None

    resp = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            **cors_headers_for(request),
        }
    )
    await resp.prepare(request)

    async def send(obj: dict) -> bool:
        try:
            await resp.write(f"data: {json.dumps(obj)}\n\n".encode())
            return True
        except Exception:  # client went away (Stop / panel closed)
            return False

    from navig.providers.inference import astream_via_connection

    pending_route: list[dict] = []
    t0 = time.monotonic()
    parts: list[str] = []
    try:
        async for delta in astream_via_connection(
            messages=messages,
            model_spec=model_spec,
            connection_id=connection_id,
            on_account=pending_route.append,
        ):
            while pending_route:
                await send({"type": "route", **pending_route.pop(0)})
            parts.append(delta)
            if not await send({"type": "delta", "text": delta}):
                logger.info("browser-agent turn: client disconnected mid-stream")
                return resp
    except Exception as exc:  # noqa: BLE001 — every failure becomes an honest frame
        from navig.llm.fallback_policy import categorize_error

        category = categorize_error(exc)
        logger.warning("browser-agent turn failed (%s): %s", category, type(exc).__name__)
        await send({"type": "error", "message": str(exc) or type(exc).__name__, "category": category})
        return resp

    text = "".join(parts)
    parsed = parse_actions(text)
    if parsed.found_block or parsed.errors:
        await send({"type": "actions", "actions": parsed.actions, "errors": parsed.errors})
    await send({"type": "done", "text": strip_actions_block(text)})
    logger.info(
        "browser-agent turn: %d chars in, %d out, %d action(s), %.1fs",
        sum(len(m["content"]) for m in messages), len(text), len(parsed.actions),
        time.monotonic() - t0,
    )
    return resp
