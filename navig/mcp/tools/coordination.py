"""MCP tools over collective coordination — the durable inter-agent/inter-session mailbox.

READ-ONLY surface: a client may inspect a participant's inbox. Sending, delegating and
transitions stay on the operator deck API (and the delegation handler), because a message is
data that must never become an arbitrary prompt; the read view is all a model needs to
answer "what is waiting for whom".
"""

from __future__ import annotations

from typing import Any

_SCOPE_RULE = (
    " recipient is a participant ref (session:<id> | agent:<id> | mission:<id> | task:<id> | "
    "formation:<id> | operator). Delivery states: persisted, delivered, consumed, acknowledged, "
    "acted, cancelled, failed, expired."
)


def _inbox_handler(_server: Any, args: dict[str, Any]) -> Any:
    from navig.coordination import CoordinationError, list_inbox  # noqa: PLC0415

    recipient = str(args.get("recipient") or "operator").strip()
    states_raw = args.get("states") or ""
    states = tuple(s for s in (x.strip() for x in states_raw.split(",")) if s) or None
    try:
        rows = list_inbox(recipient, states=states)
    except CoordinationError as exc:
        return {"error": str(exc)}
    return {"recipient": recipient, "count": len(rows), "messages": rows}


def register(server: Any) -> None:
    server.tools.update(
        {
            "coordination_inbox": {
                "name": "coordination_inbox",
                "description": "List durable coordination messages waiting for a participant. "
                "Read-only; returns each message id, sender, recipient, delivery state and body."
                + _SCOPE_RULE,
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "recipient": {
                            "type": "string",
                            "description": "Participant ref to read, e.g. session:abc123, agent:lead, operator.",
                        },
                        "states": {
                            "type": "string",
                            "description": "Optional comma-separated delivery states to filter by.",
                        },
                    },
                    "required": [],
                },
            },
        }
    )
    server._tool_handlers["coordination_inbox"] = _inbox_handler
    if not hasattr(server, "_tool_safety"):
        server._tool_safety = {}
    server._tool_safety["coordination_inbox"] = "safe"
