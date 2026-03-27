"""LLM chat route: POST /llm/chat

Exposes the gateway's conversational agent pipeline as an HTTP endpoint
so that external consumers (navig-bridge VS Code extension, etc.) can
send chat requests and receive structured responses.

Request / response shapes are defined in the API contract:
  navig-shared/core/src/types/api.ts  (TypeScript, canonical)

Python mirrors are kept here as plain dicts — no separate dataclass
needed because the JSON schema is trivially flat.

Synapse additions (2026-02-24):
  - Every response includes an ``ack`` envelope with the caller's ``msgId``.
  - GET /sync?after=<uuid> returns messages logged since the given UUID.
    The server maintains a bounded in-memory log (last 500 entries).
  - The ``msgId`` is read from the ``X-Msg-Id`` request header or
    the ``msgId`` field in the JSON body (body takes precedence).
"""

from __future__ import annotations

import time
import uuid as _uuid
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

    from navig.gateway.server import NavigGateway  # noqa: F401

try:
    from aiohttp import web
except ImportError as _exc:
    raise RuntimeError("aiohttp is required for gateway routes (pip install aiohttp)") from _exc

from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import (
    json_error_response,
    json_ok,
    require_bearer_auth,
)

logger = get_debug_logger()

# ---------------------------------------------------------------------------
# Synapse: bounded in-memory message log for /sync recovery
# (module-level — shared across all requests within this process)
# ---------------------------------------------------------------------------
_MESSAGE_LOG_MAX = 500
_message_log: deque = deque(maxlen=_MESSAGE_LOG_MAX)


def _log_message(msg_id: str, role: str, content: str) -> None:
    """Append a message to the bounded log."""
    _message_log.append(
        {
            "msgId": msg_id,
            "role": role,
            "content": content,
            "timestamp": int(time.time() * 1000),
        }
    )


def _messages_after(after_id: str) -> list:
    """Return messages logged after the message with the given ID (exclusive)."""
    found = False
    result = []
    for entry in list(_message_log):
        if found:
            result.append(entry)
        if entry["msgId"] == after_id:
            found = True
    # If the ID wasn't found, return nothing (safe default)
    return result


def register(app: web.Application, gateway: NavigGateway) -> None:
    """Register LLM endpoints."""
    app.router.add_post("/llm/chat", _chat(gateway))
    app.router.add_post("/llm/re-detect", _re_detect(gateway))
    app.router.add_post("/llm/providers/register", _register_provider())
    app.router.add_post("/llm/providers/unregister", _unregister_provider())
    app.router.add_get("/llm/providers", _list_providers())
    # Synapse: missed-message recovery endpoint
    app.router.add_get("/sync", _sync())


def _chat(gw: NavigGateway):
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

    async def handler(request: web.Request) -> web.Response:
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

        # Synapse: extract correlation ID (body takes precedence over header)
        msg_id = body.get("msgId") or request.headers.get("X-Msg-Id") or str(_uuid.uuid4())

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

        # Synapse: log user message for SYNC recovery
        _log_message(msg_id, "user", text)

        # ── Route through the gateway's conversational pipeline ─
        try:
            response_text = await gw.router.route_message(
                channel="http",
                user_id="bridge",
                message=text,
                metadata=metadata,
            )
        except Exception as exc:
            logger.exception("LLM chat handler error")
            err_msg = str(exc)
            # Synapse: log error response so SYNC can surface it
            err_id = str(_uuid.uuid4())
            _log_message(err_id, "assistant", f"[Error] {err_msg}")
            return json_error_response(
                "LLM chat handler error",
                status=500,
                code="llm_error",
                details={
                    "provider": "gateway",
                    "latencyMs": int((time.monotonic() - start_ms) * 1000),
                    "message": err_msg,
                    "ack": {"id": msg_id, "status": "error"},
                },
            )

        latency_ms = int((time.monotonic() - start_ms) * 1000)

        # Synapse: log assistant response
        resp_id = str(_uuid.uuid4())
        _log_message(resp_id, "assistant", response_text or "")

        return json_ok(
            {
                "text": response_text or "",
                "metadata": {
                    "provider": "gateway",
                    "latencyMs": latency_ms,
                },
                # Synapse: ACK envelope — confirms receipt and processing are done
                "ack": {
                    "id": msg_id,
                    "status": "done",
                },
            }
        )

    return handler


def _re_detect(gw: NavigGateway):
    """
    POST /llm/re-detect

    Force the AI client to re-detect the best available LLM provider.
    Called by navig-bridge after establishing reverse SSH tunnels so the
    daemon can discover the newly reachable VS Code Copilot servers.

    Returns the old and new provider names.
    """

    async def handler(request: web.Request) -> web.Response:
        auth = require_bearer_auth(request, gw)
        if auth is not None:
            return auth

        try:
            from navig.agent.ai_client import get_ai_client

            client = get_ai_client()
            old_provider = client.provider
            new_provider = client.re_detect_provider()
            return json_ok(
                {
                    "old_provider": old_provider,
                    "new_provider": new_provider,
                    "changed": old_provider != new_provider,
                }
            )
        except Exception as exc:
            logger.exception("LLM re-detect error")
            return json_error_response(
                "Failed to re-detect provider",
                status=500,
                code="re_detect_error",
                details={"message": str(exc)},
            )

    return handler


def _register_provider():
    """POST /llm/providers/register — register a dynamic LLM provider."""

    async def handler(request: web.Request) -> web.Response:
        try:
            from navig.providers.bridge_registry import get_bridge_registry

            body = await request.json()
            name = body.get("name", "")
            url = body.get("url", "")
            priority = int(body.get("priority", 0))
            if not name or not url:
                return json_error_response(
                    "name and url are required", status=400, code="invalid_request"
                )
            provider = get_bridge_registry().register(name, url, priority)
            logger.info(
                "[Bridge] Registered provider '%s' at %s (priority %d)",
                name,
                url,
                priority,
            )
            return json_ok(
                {
                    "name": provider.name,
                    "url": provider.url,
                    "priority": provider.priority,
                }
            )
        except Exception as exc:
            logger.exception("Provider register error")
            return json_error_response(str(exc), status=500, code="register_error")

    return handler


def _unregister_provider():
    """POST /llm/providers/unregister — unregister a dynamic LLM provider."""

    async def handler(request: web.Request) -> web.Response:
        try:
            from navig.providers.bridge_registry import get_bridge_registry

            body = await request.json()
            name = body.get("name", "")
            if not name:
                return json_error_response("name is required", status=400, code="invalid_request")
            removed = get_bridge_registry().unregister(name)
            logger.info("[Bridge] Unregistered provider '%s' (found=%s)", name, removed)
            return json_ok({"removed": removed})
        except Exception as exc:
            logger.exception("Provider unregister error")
            return json_error_response(str(exc), status=500, code="unregister_error")

    return handler


def _list_providers():
    """GET /llm/providers — list all dynamically registered providers."""

    async def handler(request: web.Request) -> web.Response:
        from navig.providers.bridge_registry import get_bridge_registry

        providers = get_bridge_registry().all()
        return json_ok(
            {
                "providers": [
                    {"name": p.name, "url": p.url, "priority": p.priority} for p in providers
                ]
            }
        )

    return handler


def _sync():
    """
    GET /sync?after=<uuid>

    Synapse: return all messages logged after the given UUID so that a
    reconnecting Bridge client can recover any messages it missed while
    the connection was down.

    Query param:
        after  (str, required)  The last msgId the client has seen.

    Response:
        { ok: true, data: { missed_messages: [{ msgId, role, content, timestamp }] } }
    """

    async def handler(request: web.Request) -> web.Response:
        after_id = request.rel_url.query.get("after", "").strip()
        if not after_id:
            return json_error_response(
                "Missing required query param: after",
                status=400,
                code="validation_error",
            )
        missed = _messages_after(after_id)
        logger.info("[Synapse] /sync?after=%s → %d message(s)", after_id, len(missed))
        return json_ok({"missed_messages": missed})

    return handler
