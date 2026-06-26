"""
Conversational "Ask NAVIG" handler for the Deck API.

Backs the universal command palette's "Ask NAVIG" row (the `navig:ask-ai`
front-end seam). Accepts a free-form prompt and returns a single assistant
reply by routing through the shared agent AI client — the only place LLM
inference is allowed to live (Architectural Law: agent boundary).

Routes:
    POST /api/deck/ask    → { reply, model } for a one-shot prompt
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover - aiohttp always present at runtime
    web = None

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are NAVIG, a precise systems assistant embedded in the NAVIG Deck. "
    "Answer the user's question directly and concisely. Prefer actionable, "
    "factual replies over filler."
)

# Guardrail: keep palette prompts short so this stays a quick-answer seam, not
# an unbounded chat transcript endpoint.
_MAX_QUERY_CHARS = 4000


async def handle_deck_ask(request: "web.Request") -> "web.Response":
    """POST /api/deck/ask — one-shot conversational reply.

    Body: { "query": "<prompt>", "temperature"?: float, "max_tokens"?: int }
    Returns: { "ok": true, "reply": "<text>", "model": "<provider>" }
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "expected object"}, status=400)

    query = str(body.get("query", "")).strip()
    if not query:
        return web.json_response({"ok": False, "error": "query required"}, status=400)
    if len(query) > _MAX_QUERY_CHARS:
        return web.json_response(
            {"ok": False, "error": f"query too long (max {_MAX_QUERY_CHARS} chars)"},
            status=400,
        )

    try:
        temperature = float(body.get("temperature", 0.7))
    except (TypeError, ValueError):
        temperature = 0.7
    try:
        max_tokens = int(body.get("max_tokens", 1024))
    except (TypeError, ValueError):
        max_tokens = 1024

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    try:
        from navig.agent.ai_client import get_ai_client

        client = get_ai_client()
        reply = await client.chat_routed(
            messages,
            user_message=query,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return web.json_response(
            {
                "ok": True,
                "reply": (reply or "").strip(),
                "model": getattr(client, "provider", "") or "",
            }
        )
    except RuntimeError as exc:
        # No provider configured — surface a clean, actionable message.
        logger.info("handle_deck_ask: no provider available: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=503)
    except Exception as exc:
        logger.warning("handle_deck_ask failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
