"""Collective coordination — the durable, authorized message path between participants.

One small layer over :class:`navig.store.coordination.CoordinationStore`. It owns the
*contract* (participant addressing, kinds, delivery-state ordering, payload limits) and the
*authorization boundary* (a message is data; it never executes a tool or starts an agent turn).
Callers are the deck operator surface and the MissionExecutor; both pass a validated sender.

Design rules, matching the mission:

* persisted before acknowledged — :func:`send` writes the row and returns ``persisted``;
* idempotent — one ``idempotency_key`` persists once (the store's ``INSERT OR IGNORE``);
* explicit delivery states — accepted → persisted → delivered → consumed → acknowledged → acted;
* deny by default — :func:`send` refuses an invalid participant ref or kind, and the deck route
  requires the operator; nothing here shells out or runs an agent;
* no fake delivery — an unknown recipient is a capability error, not a silent drop.

Nothing here is a conversation, a task, or a notification. Session JSONL stays history;
``navig_tasks`` / ``TaskQueue`` / ``MissionExecutor`` stay the execution authorities; ``notify``
stays the projection.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from navig.store.coordination import DELIVERY_STATES, CoordinationStore, get_coordination_store

__all__ = [
    "KINDS",
    "CoordinationError",
    "act",
    "ack",
    "cancel",
    "consume",
    "deliver",
    "expire",
    "list_inbox",
    "send",
    "valid_participant",
]

KINDS = (
    "inform", "request", "delegate", "question", "answer", "progress",
    "result", "handoff", "review", "approval_request", "alert",
)

_PARTICIPANT_PREFIXES = ("session:", "agent:", "mission:", "task:", "formation:", "operator")

#: Hard payload cap: a message carries a bounded, structured content object, never a transcript.
MAX_CONTENT_BYTES = 64 * 1024
MAX_EVIDENCE_REFS = 50

_STATE_ORDER = {state: i for i, state in enumerate(DELIVERY_STATES)}
_TERMINAL = {"acted", "cancelled", "failed", "expired"}


class CoordinationError(ValueError):
    """A refusal the caller can act on — never a silent drop."""


def valid_participant(ref: str) -> bool:
    return isinstance(ref, str) and ref.startswith(_PARTICIPANT_PREFIXES)


def _validate_content(content: Any) -> dict[str, Any]:
    if content is None:
        content = {}
    if not isinstance(content, dict):
        raise CoordinationError("content must be an object")
    try:
        payload = json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise CoordinationError("content is not JSON-serializable") from exc
    if len(payload.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise CoordinationError("content exceeds the size limit")
    return content


def send(
    sender: str,
    recipient: str,
    kind: str,
    content: dict[str, Any],
    *,
    store: CoordinationStore | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Persist one coordination message and return it in the ``persisted`` state.

    ``sender``/``recipient`` are participant refs; ``kind`` is one of :data:`KINDS`.
    ``fields`` may carry mission_id/task_id/session_id/correlation_id/causation_id/reply_to/
    priority/evidence_refs/idempotency_key. An explicit idempotency key makes a retried send
    return the original row.
    """
    if not valid_participant(sender):
        raise CoordinationError(f"invalid sender {sender!r}")
    if not valid_participant(recipient):
        raise CoordinationError(f"invalid recipient {recipient!r}")
    if sender == recipient and kind in ("question", "request", "delegate"):
        raise CoordinationError("a participant cannot delegate to itself")
    if kind not in KINDS:
        raise CoordinationError(f"invalid kind {kind!r}")

    evidence = fields.get("evidence_refs") or []
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE_REFS:
        raise CoordinationError("evidence_refs must be a list of at most 50 references")

    message = {
        "id": fields.get("id") or uuid.uuid4().hex[:16],
        "schema_version": 1,
        "kind": kind,
        "sender": sender,
        "recipient": recipient,
        "mission_id": fields.get("mission_id"),
        "task_id": fields.get("task_id"),
        "session_id": fields.get("session_id"),
        "correlation_id": fields.get("correlation_id"),
        "causation_id": fields.get("causation_id"),
        "reply_to": fields.get("reply_to"),
        "priority": fields.get("priority") or "normal",
        "content": _validate_content(content),
        "evidence_refs": [str(ref) for ref in evidence],
        "expires_at": fields.get("expires_at"),
        "idempotency_key": fields.get("idempotency_key") or uuid.uuid4().hex[:16],
    }
    row, _created = (store or get_coordination_store()).put(message)
    return row


def _transition(
    message_id: str,
    to_state: str,
    *,
    store: CoordinationStore | None = None,
) -> dict[str, Any]:
    store = store or get_coordination_store()
    current = store.get(message_id)
    if current is None:
        raise CoordinationError("no such message")
    if current["delivery_state"] in _TERMINAL:
        raise CoordinationError(f"message is terminal ({current['delivery_state']})")
    if _STATE_ORDER[current["delivery_state"]] > _STATE_ORDER[to_state]:
        raise CoordinationError(
            f"cannot move {current['delivery_state']!r} back to {to_state!r}"
        )
    updated = store.set_delivery_state(message_id, to_state)
    if updated is None:
        raise CoordinationError("message vanished during the transition")
    return updated


def list_inbox(
    recipient: str,
    *,
    states: tuple[str, ...] | None = None,
    limit: int = 100,
    store: CoordinationStore | None = None,
) -> list[dict[str, Any]]:
    if not valid_participant(recipient):
        raise CoordinationError(f"invalid recipient {recipient!r}")
    return (store or get_coordination_store()).list_inbox(recipient, states=states, limit=limit)


def deliver(message_id: str, *, store: CoordinationStore | None = None) -> dict[str, Any]:
    return _transition(message_id, "delivered", store=store)


def consume(message_id: str, *, store: CoordinationStore | None = None) -> dict[str, Any]:
    return _transition(message_id, "consumed", store=store)


def ack(message_id: str, *, store: CoordinationStore | None = None) -> dict[str, Any]:
    return _transition(message_id, "acknowledged", store=store)


def act(message_id: str, *, store: CoordinationStore | None = None) -> dict[str, Any]:
    return _transition(message_id, "acted", store=store)


def cancel(message_id: str, *, store: CoordinationStore | None = None) -> dict[str, Any]:
    """A sender cancels a message that has not been acted on yet."""
    return _transition(message_id, "cancelled", store=store)


def expire(*, now: str | None = None, store: CoordinationStore | None = None) -> list[dict[str, Any]]:
    """Move expired persisted/delivered messages to the terminal ``expired`` state.

    Returns the rows that expired. Expired requests stop retrying — this is the explicit
    boundary the delivery loop honours.
    """
    store = store or get_coordination_store()
    expired: list[dict[str, Any]] = []
    for row in store.list_expired(now=now):
        store.set_delivery_state(row["id"], "expired", error="expired")
        expired.append(store.get(row["id"]) or row)
    return expired
