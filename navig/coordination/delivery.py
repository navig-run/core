"""Reliable local delivery — at-least-once routing of persisted coordination messages.

The delivery worker is the only thing that moves a message from ``persisted`` to
``delivered``, and it does so through a small handler registry keyed by participant
prefix. It is deliberately boring:

* a handler is registered per recipient prefix (``session:``, ``agent:``, …);
* an unregistered prefix is NOT a delivery — it is an explicit pending state with an
  error, so an offline/unwired recipient never looks delivered;
* failures retry with exponential backoff up to ``MAX_RETRIES``, then dead-letter
  (``failed``);
* expiry is checked before any handler runs, and an expired message never fires;
* the scan is idempotent: a successful delivery flips the row to ``delivered`` and
  the worker never selects it again.

Nothing here starts an agent turn or executes a tool — it only calls a registered
handler, which owns what "delivered" means for that surface.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from navig.store.coordination import CoordinationStore, get_coordination_store

__all__ = [
    "MAX_RETRIES",
    "DeliveryOutcome",
    "handlers",
    "register_handler",
    "tick",
    "unregister_handler",
]

MAX_RETRIES = 5
RETRY_BASE_SECONDS = 2.0

_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {}


def register_handler(prefix: str, fn: Callable[[dict[str, Any]], Any]) -> None:
    """Register the handler that owns delivery for ``prefix`` (e.g. ``"session"``)."""
    _HANDLERS[str(prefix)] = fn


def unregister_handler(prefix: str) -> None:
    _HANDLERS.pop(str(prefix), None)


def handlers() -> dict[str, Callable[[dict[str, Any]], Any]]:
    return dict(_HANDLERS)


def _prefix(recipient: str) -> str:
    return str(recipient).split(":", 1)[0]


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return None


def _expired(message: dict[str, Any], now_ts: float) -> bool:
    expires = _epoch(message.get("expires_at"))
    return expires is not None and expires <= now_ts


def _due_for_retry(message: dict[str, Any], now_ts: float) -> bool:
    """True when this message is eligible for a delivery attempt now.

    A never-attempted message is always due; a failed attempt waits
    ``RETRY_BASE_SECONDS * 2 ** retry_count`` before the next try.
    """
    last = _epoch(message.get("last_attempt_at"))
    if last is None:
        return True
    delay = RETRY_BASE_SECONDS * (2 ** int(message.get("retry_count") or 0))
    return (now_ts - last) >= delay


@dataclass
class DeliveryOutcome:
    message_id: str
    recipient: str
    state: str
    error: str = ""


def _deliver_one(message: dict[str, Any], store: CoordinationStore, now_ts: float) -> DeliveryOutcome:
    mid = message["id"]
    recipient = message["recipient"]

    if _expired(message, now_ts):
        store.set_delivery_state(mid, "expired", error="expired")
        return DeliveryOutcome(mid, recipient, "expired", "expired")

    handler = _HANDLERS.get(_prefix(recipient))
    if handler is None:
        # Not delivered, not lost: the row stays persisted with an explicit reason.
        store.set_delivery_state(mid, "persisted", error=f"no delivery handler for {_prefix(recipient)!r}")
        return DeliveryOutcome(mid, recipient, "pending", f"no handler for {_prefix(recipient)!r}")

    try:
        handler(message)
    except Exception as exc:  # noqa: BLE001 — a failing surface must not kill the scan
        store.touch_retry(mid)
        if int(message.get("retry_count") or 0) + 1 >= MAX_RETRIES:
            store.set_delivery_state(mid, "failed", error=str(exc))
            return DeliveryOutcome(mid, recipient, "failed", str(exc))
        return DeliveryOutcome(mid, recipient, "pending", str(exc))

    store.set_delivery_state(mid, "delivered")
    return DeliveryOutcome(mid, recipient, "delivered")


def tick(
    *,
    store: CoordinationStore | None = None,
    now_ts: float | None = None,
    limit: int = 50,
) -> list[DeliveryOutcome]:
    """One delivery pass: expire due messages, then attempt due ``persisted`` ones.

    Returns one outcome per message processed. Call it on a schedule (existing cron)
    or after enqueue; it is safe to call concurrently because the store's write lock
    and idempotent state transition prevent double delivery.
    """
    store = store or get_coordination_store()
    now_ts = time.time() if now_ts is None else now_ts
    outcomes: list[DeliveryOutcome] = []

    for message in store.list_by_state(("persisted",), limit=1000):
        if not _due_for_retry(message, now_ts):
            continue
        outcomes.append(_deliver_one(message, store, now_ts))
        if len(outcomes) >= max(1, int(limit)):
            break
    return outcomes
