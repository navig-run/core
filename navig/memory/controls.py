"""navig.memory.controls — the user's switches over long-term memory.

Two independent controls decide whether a conversation may read or write the
key-fact store:

* ``memory.enabled`` (config, default on) — the global "Memory" switch. Off means the
  agent neither recalls nor saves facts, and its memory tools refuse.
* **incognito** (per conversation) — a chat that leaves no trace: no recall, no new
  facts, no reloadable history. Set by the conversational agent for the duration of a
  turn through :func:`incognito_scope`, so the memory tools the agent calls mid-turn
  see it without being handed a session object.

Explicit user actions (``navig memory remember``, the deck's approve/import) are not
gated: managing memory by hand is how a user curates it, whatever the switch says.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger(__name__)

ENABLED_KEY = "memory.enabled"

_INCOGNITO: ContextVar[bool] = ContextVar("navig_memory_incognito", default=False)


def memory_enabled() -> bool:
    """The ``memory.enabled`` switch, read fresh each call.

    Read through ``coerce_bool``: ``navig config set memory.enabled false`` stores the
    string ``"false"``. An unreadable config reads as OFF for this call — skipping
    recall for one turn is harmless, saving facts the user switched off is not.
    Read LIVE: a ``navig config set`` from a terminal reaches the running daemon.
    """
    try:
        from navig.config import get_config_manager
        from navig.core.coerce import coerce_bool

        cm = get_config_manager()
        read = getattr(cm, "get_live", cm.get)
        return coerce_bool(read(ENABLED_KEY, True), default=True)
    except Exception as exc:  # noqa: BLE001 — never let a config read break a turn
        logger.debug("memory.enabled unreadable (%s); treating memory as off", exc)
        return False


def is_incognito() -> bool:
    """True while the current turn belongs to an incognito conversation."""
    return _INCOGNITO.get()


@contextmanager
def incognito_scope(on: bool) -> Iterator[None]:
    """Mark the enclosed turn (and every task/thread it spawns) as incognito or not."""
    token = _INCOGNITO.set(bool(on))
    try:
        yield
    finally:
        _INCOGNITO.reset(token)


def memory_off_reason() -> str | None:
    """Why the agent may not use memory right now, or None when it may.

    The reason is phrased for the agent to relay to the user.
    """
    if is_incognito():
        return "This is an incognito chat: memory is not read or saved."
    if not memory_enabled():
        return (
            "Memory is turned off (memory.enabled is false). "
            "Turn it on with `navig config set memory.enabled true`."
        )
    return None
