"""Ingest actions: a verified signal that DOES something instead of ringing.

A signal source normally becomes a notification (``notify.dispatch``). A plugin
may claim a source name for an action instead — a voice assistant asking OBS to
switch scene, say. The route still owns every check (per-source HMAC,
timestamp window, replay dedupe); only the sink changes. The handler receives
the verified JSON payload and returns a small JSON-able dict that becomes the
response (e.g. ``{"speech": "Switched to the Architect."}``).

Registered in-process by a plugin's ``gateway:register_routes`` hook; nothing
is persisted. A source with no action keeps the notification behaviour.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

Action = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_ACTIONS: dict[str, Action] = {}


def register(source: str, action: Action) -> None:
    """Route verified events of ``source`` to ``action`` (last one wins)."""
    _ACTIONS[source] = action


def unregister(source: str) -> None:
    _ACTIONS.pop(source, None)


def get(source: str) -> Action | None:
    return _ACTIONS.get(source)
