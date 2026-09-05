"""Pending approvals, written down — so a restart and a late answer are both visible.

`ApprovalManager` holds its pending requests in a plain dict and their waiters in
`asyncio.Future`s. Both die with the process, and both are popped the instant the turn gives
up. Three things followed, and all three are silent:

1. **A restart forgets.** Whatever the operator was asked to approve is simply gone, with no
   record that it was ever asked.
2. **A timeout tells nobody.** The expiry path logs at INFO and prints a narrator block — to
   a terminal. Under the daemon there is no terminal, so the operator who was pinged on
   Telegram learns nothing.
3. **A late answer is discarded.** `respond()` looks the id up in `_pending`, which the
   turn's `finally` already popped, so it logs "Approval request not found" and returns
   False. The human tapped *Approve*, and nothing anywhere says their decision arrived too
   late to matter.

This module is the small, boring half of the fix: a durable record of what is outstanding,
and enough memory of what recently expired to recognise a late answer instead of shrugging
at it. It deliberately does **not** try to resume the turn — that needs the agent loop to
become a checkpointed state machine, which is a separate design change. Knowing *what* was
lost is worth having on its own, and is what any future resume would be built on.

Every function here is best-effort and never raises: an approval must not fail because a
bookkeeping file could not be written.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

#: How many resolved/expired ids to remember, so a late answer can be named rather than
#: reported as "not found". Bounded because this is a courtesy, not an audit log — the
#: audit log is the audit log.
_RECENT_LIMIT = 64


def _store_path() -> Path:
    from navig.platform import paths

    return paths.config_dir() / "approvals" / "pending.json"


def _load() -> dict[str, Any]:
    from navig.core.json_io import load_json_safe

    data = load_json_safe(_store_path(), default={})
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    from navig.core.json_io import atomic_write_json

    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # ⚠ (data, path) — NOT (path, data). Reversed, this writes the path string as the
    # document and every read comes back empty; the `except` in each caller then hides it.
    # Caught by the tests, which is why they assert the value is readable back rather than
    # that the call did not raise.
    atomic_write_json(data, path)


def record_pending(
    request_id: str,
    *,
    command: str,
    channel: str,
    user_id: str,
    level: str,
    expires_at: float | None = None,
) -> None:
    """Note that *request_id* is outstanding. Never raises."""
    try:
        data = _load()
        data[request_id] = {
            "command": command,
            "channel": channel,
            "user_id": user_id,
            "level": level,
            "asked_at": time.time(),
            "expires_at": expires_at,
        }
        _save(data)
    except Exception:  # noqa: BLE001 — bookkeeping must never break an approval
        pass


def clear_pending(request_id: str) -> dict[str, Any] | None:
    """Drop *request_id* from the outstanding set, returning what it held (if anything)."""
    try:
        data = _load()
        entry = data.pop(request_id, None)
        if entry is not None:
            _save(data)
        return entry
    except Exception:  # noqa: BLE001
        return None


def list_pending() -> dict[str, Any]:
    """Everything still recorded as outstanding — including across a restart."""
    try:
        return _load()
    except Exception:  # noqa: BLE001
        return {}


class RecentlyResolved:
    """A bounded memory of ids the manager has already stopped waiting on.

    The point is to tell "an id we have never heard of" apart from "an id whose turn gave up
    ninety seconds ago" — the second is a human whose tap did nothing, and deserves to be
    told so rather than logged as a stray.
    """

    def __init__(self, limit: int = _RECENT_LIMIT) -> None:
        self._limit = limit
        self._items: dict[str, dict[str, Any]] = {}

    def remember(self, request_id: str, *, outcome: str, command: str = "") -> None:
        self._items[request_id] = {
            "outcome": outcome,
            "command": command,
            "at": time.time(),
        }
        while len(self._items) > self._limit:
            self._items.pop(next(iter(self._items)))

    def get(self, request_id: str) -> dict[str, Any] | None:
        return self._items.get(request_id)

    def clear(self) -> None:
        self._items.clear()
