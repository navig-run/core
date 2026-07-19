"""
Conversational "Ask NAVIG" handler for the Deck API.

Backs the universal command palette's "Ask NAVIG" row (the `navig:ask-ai`
front-end seam). Accepts a free-form prompt and returns a single assistant
reply by routing through ``navig.llm.run_llm`` — the canonical core LLM
dispatch (the same seam agents use). Going through ``run_llm`` gives the deck
the operator's real **AI connections** (claude-max OAuth, API-key, local) plus
**connection-aware fallback**: if the chosen model rate-limits or a Claude Max
account is capped, it rotates to a sibling account / fallback model and reports
that back so the UI can show "answered with <model>" instead of a silent swap.

Routes:
    POST /api/deck/ask    → { reply, model, provider, fallback? } for one prompt
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
    Returns: { "ok": true, "reply": "<text>", "model": "<model-id>",
               "provider": "<provider>",
               "fallback"?: {from,to,reason,reason_label,connection_id} }
    (reason is the raw category; reason_label is a ready-to-render phrase.)
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
        import asyncio

        from navig.llm.generate import run_llm

        result = await asyncio.to_thread(
            run_llm,
            messages,
            user_input=query,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("handle_deck_ask dispatch failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)

    finish = str(getattr(result, "finish_reason", "") or "")
    reply = (result.content or "").strip()

    # No provider / every provider failed → actionable 503 (the front-end maps
    # any 5xx to a "configure a provider" hint).
    if not reply and (finish.startswith("error") or finish.startswith("all_fallbacks_failed")):
        detail = (
            "all AI providers failed"
            if finish.startswith("all_fallbacks_failed")
            else "no AI provider available"
        )
        logger.info("handle_deck_ask: %s (%s)", detail, finish[:200])
        return web.json_response(
            {"ok": False, "error": f"{detail} — add a provider in Settings → Vault or run `navig connect`."},
            status=503,
        )

    payload: dict[str, Any] = {
        "ok": True,
        "reply": reply,
        "model": result.model or getattr(result, "provider", "") or "",
        "provider": getattr(result, "provider", "") or "",
    }
    # Surface a connection-aware rotation (e.g. a capped Claude Max account →
    # a sibling subscription or a fallback model) so it's never a silent swap.
    _fb = (getattr(result, "raw", None) or {}).get("fallback")
    if _fb:
        from navig.llm.fallback_policy import describe_category

        payload["fallback"] = {
            "from": _fb.get("from"),
            "to": _fb.get("to"),
            "reason": _fb.get("reason"),
            # A ready-to-render phrase (same one the CLI/chat use) so the frontend
            # doesn't re-implement the category→prose map in TS and drift from it.
            "reason_label": describe_category(_fb.get("reason")),
            "connection_id": _fb.get("connection_id"),
        }
    return web.json_response(payload)
