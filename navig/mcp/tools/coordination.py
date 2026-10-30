"""MCP tools over collective coordination — the durable inter-agent/inter-session mailbox.

READ-ONLY surface: a client may inspect a participant's inbox and count it by delivery state.
Sending, delegating and transitions stay on the operator deck API (and the delegation handler),
because a message is data that must never become an arbitrary prompt; the read views are all a
model needs to answer "what is waiting for whom", and how much of it.
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


def _stats_handler(_server: Any, args: dict[str, Any]) -> Any:
    from navig.coordination import CoordinationError, list_inbox  # noqa: PLC0415

    recipient = str(args.get("recipient") or "operator").strip()
    try:
        rows = list_inbox(recipient)
    except CoordinationError as exc:
        return {"error": str(exc)}
    counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get("delivery_state") or row.get("state") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return {"recipient": recipient, "total": len(rows), "by_state": counts}


def register(server: Any) -> None:
    server.tools.update(
        {
            "coordination_inbox": {
                "name": "coordination_inbox",
                "description": "List durable coordination messages waiting for a participant. "
                "Read-only; returns each message id, sender, recipient, delivery state and body. "
                "Use coordination_stats first when you only need how much is waiting."
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
            "coordination_stats": {
                "name": "coordination_stats",
                "description": "Count durable coordination messages for one participant, per delivery "
                "state. Use it before coordination_inbox when the question is how much is waiting "
                "rather than what says what; it returns the total and one count per state."
                + _SCOPE_RULE,
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "recipient": {
                            "type": "string",
                            "description": "Participant ref to count, e.g. session:abc123, agent:lead, operator.",
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
    server._tool_handlers["coordination_stats"] = _stats_handler
    server._tool_safety["coordination_stats"] = "safe"
