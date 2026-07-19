"""
A2A JSON-RPC message layer — let peers actually *call* a NAVIG agent.

The Agent Card (see :mod:`navig.mesh.agent_card`) is discovery: "who is this
agent". This module is the *action* half of A2A: a minimal JSON-RPC 2.0 message
interface so an A2A client can send a task and get a reply.

A2A messaging is plain **JSON-RPC 2.0 over HTTP POST** to the agent's advertised
``url``. v0 implements the one method that matters — ``message/send`` — and
returns an A2A *Message* directly (the spec allows a simple agent to answer with
a Message instead of a long-lived Task). Streaming (``message/stream``) and Task
lifecycle (``tasks/get`` / ``tasks/cancel``) are deferred; adding them later is
additive and does not change this contract.

This module is pure (no I/O, no LLM): it parses/builds JSON-RPC + A2A envelopes.
The gateway route layer wires it to the existing agent via ``router.route_message``
so all inference stays inside ``navig/agent/`` (Architectural Law: agent boundary).
"""

from __future__ import annotations

import uuid
from typing import Any

JSONRPC_VERSION = "2.0"

# Standard JSON-RPC 2.0 error codes (https://www.jsonrpc.org/specification).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# The single supported method in v0.
METHOD_MESSAGE_SEND = "message/send"


def new_message_id() -> str:
    """Generate a fresh A2A messageId."""
    return uuid.uuid4().hex


def extract_text(message: dict[str, Any]) -> str:
    """Join the text of every ``kind == "text"`` part of an A2A Message.

    Non-text parts (files, structured data) are ignored in v0 — the agent
    speaks text. Returns an empty string when there is no text content.
    """
    parts = message.get("parts") or []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("kind") == "text":
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks)


def build_message(text: str, *, message_id: str | None = None) -> dict[str, Any]:
    """Build an A2A agent Message wrapping ``text`` in a single text part."""
    return {
        "role": "agent",
        "parts": [{"kind": "text", "text": text}],
        "messageId": message_id or new_message_id(),
        "kind": "message",
    }


def rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    """Wrap a result in a JSON-RPC 2.0 success envelope."""
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def rpc_error(
    request_id: Any,
    code: int,
    message: str,
    *,
    data: Any | None = None,
) -> dict[str, Any]:
    """Wrap an error in a JSON-RPC 2.0 error envelope."""
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": err}


def validate_request(body: Any) -> tuple[bool, str]:
    """Validate the shape of a JSON-RPC 2.0 request.

    Returns ``(ok, reason)``. ``reason`` is human-readable when ``ok`` is False.
    """
    if not isinstance(body, dict):
        return False, "request must be a JSON object"
    if body.get("jsonrpc") != JSONRPC_VERSION:
        return False, "jsonrpc must be '2.0'"
    if not isinstance(body.get("method"), str) or not body["method"]:
        return False, "method must be a non-empty string"
    return True, ""
