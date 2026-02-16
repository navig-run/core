"""LLM chat route: POST /llm/chat

Exposes the gateway's conversational agent pipeline as an HTTP endpoint
so that external consumers (navig-bridge VS Code extension, etc.) can
send chat requests and receive structured responses.

Request / response shapes are defined in the API contract:
  navig-shared/core/src/types/api.ts  (TypeScript, canonical)

Python mirrors are kept here as plain dicts — no separate dataclass
needed because the JSON schema is trivially flat.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web
    from navig.gateway.server import NavigGateway  # noqa: F401

try:
    from aiohttp import web
except ImportError:
    pass

from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import json_error_response, json_ok, require_bearer_auth

logger = get_debug_logger()


def register(app: "web.Application", gateway: "NavigGateway") -> None:
    """Register the /llm/chat endpoint."""
    app.router.add_post("/llm/chat", _chat(gateway))


def _chat(gw: "NavigGateway"):
    """
    POST /llm/chat

    Accepts a ChatRequest JSON body and returns a ChatResponse.
    Uses the gateway's ChannelRouter → ConversationalAgent pipeline
    so that model routing, session management, and SOUL context
    all work identically to the Telegram channel path.

    Required fields:
        text          (str)   The user message.
        conversation  (list)  Previous turns [{role, content, ...}].
        scope         (str)   "project" | "personal" | "network".

    Optional fields:
        projectRoot        (str)   Workspace root path.
        formation          (str)   Formation name from .navig/profile.json.
        flags              (dict)  {treatAsInbox, autoEvolve, includeWorkspaceContext}.
        workspaceContext   (str)   Pre-built workspace context blob.
    """

    async def handler(request: "web.Request") -> "web.Response":
        start_ms = time.monotonic()

        auth = require_bearer_auth(request, gw)
        if auth is not None:
            return auth

        # ── Parse body ──────────────────────────────────────────
        try:
            body = await request.json()
        except Exception:
            return json_error_response("Invalid JSON body", status=400, code="invalid_json")

        text = (body.get("text") or "").strip()
        if not text:
            return json_error_response(
                "Missing required field: text",
                status=400,
                code="validation_error",
            )

        scope = body.get("scope", "personal")
        formation = body.get("formation")
        flags = body.get("flags", {})
        workspace_context = body.get("workspaceContext")

        # ── Build metadata for the channel router ───────────────
        metadata = {
            "scope": scope,
            "formation": formation,
            "flags": flags,
            "workspace_context": workspace_context,
            "source": "http",
        }

        # If caller requested a specific tier, forward it
        if flags.get("autoEvolve"):
            metadata["tier_override"] = "big"

        # ── Route through the gateway's conversational pipeline ─
        try:
            response_text = await gw.router.route_message(
                channel="http",
                user_id="forge",
                message=text,
                metadata=metadata,
            )
        except Exception as exc:
            logger.exception("LLM chat handler error")
            return json_error_response(
                "LLM chat handler error",
                status=500,
                code="llm_error",
                details={
                    "provider": "gateway",
                    "latencyMs": int((time.monotonic() - start_ms) * 1000),
                    "message": str(exc),
                },
            )

        latency_ms = int((time.monotonic() - start_ms) * 1000)

        return json_ok(
            {
                "text": response_text or "",
                "metadata": {
                        "provider": "gateway",
                        "latencyMs": latency_ms,
                    },
            }
        )

    return handler
