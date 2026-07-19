"""Broadcast helpers for the gateway core ``/ws`` endpoint.

The ``/ws`` route (``navig/gateway/routes/core.py``) records per-connection topic
subscriptions in ``gw._ws_subscriptions`` (``{id(ws): set[str]}``) and the live
sockets in ``gw.ws_connections`` (``set``). Both live in the gateway instance's
``__dict__`` (created lazily by the route handler), so this module reads them the
same way — it never assumes the attributes exist.

Every server-side push to ``/ws`` clients must go through :func:`broadcast_ws`
so the subscription contract holds at every broadcast site:

- A connection that never subscribed (empty topic set) receives **everything** —
  the pre-subscription behaviour, kept for backward compatibility.
- A connection that subscribed to one or more topics receives **only** events
  whose topic matches one of its patterns (``fnmatch`` globs, so ``host.*``
  works — same topic-glob convention as ``EventBridge.SubscriptionFilter``).
- A broadcast that declares no topic (``topic=None``) is delivered to every
  connection: filtering is opt-in per broadcast site, so untyped legacy pushes
  can never be silently dropped.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


def ws_topic_allowed(gw: Any, ws: Any, topic: str | None) -> bool:
    """Return True when *ws* should receive a broadcast tagged with *topic*.

    No recorded subscriptions → receive everything (backward compatible).
    ``topic=None`` (untyped broadcast) → receive everything.
    Otherwise the topic must fnmatch one of the connection's patterns.
    """
    if topic is None:
        return True
    subscriptions = gw.__dict__.get("_ws_subscriptions") or {}
    topics = subscriptions.get(id(ws))
    if not topics:
        return True
    return any(fnmatch(topic, pattern) for pattern in topics)


async def broadcast_ws(gw: Any, payload: dict, *, topic: str | None = None) -> int:
    """Send *payload* to every ``/ws`` connection whose subscription allows *topic*.

    Best-effort: a failed send never raises; the dead socket (and its
    subscription entry) is pruned so it stops consuming future broadcasts.
    Returns the number of clients the payload was delivered to.
    """
    connections = gw.__dict__.get("ws_connections")
    if not connections:
        return 0

    sent = 0
    dead: list[Any] = []
    for ws in list(connections):
        if not ws_topic_allowed(gw, ws, topic):
            continue
        try:
            await ws.send_json(payload)
            sent += 1
        except Exception:  # noqa: BLE001 — best-effort push; one dead client must not stop the rest
            dead.append(ws)

    if dead:
        subscriptions = gw.__dict__.get("_ws_subscriptions") or {}
        for ws in dead:
            connections.discard(ws)
            subscriptions.pop(id(ws), None)
        logger.debug("broadcast_ws pruned %d dead connection(s)", len(dead))

    return sent
