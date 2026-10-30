"""Where a recurring card lives: one message per (chat, surface), remembered across runs.

The defect this exists for: scheduled surfaces repost instead of editing. A campaign card,
the tasks digest and the reminders sheet each went out twice within half an hour on
2026-10-26/27 — 22:00 and 22:28, 22:03 and 22:29, 00:57 and 00:58 — because a run that
builds a card has no memory of the message its previous run sent. Without an anchor the only
options are "send a new one" (the duplicate) or "send nothing" (a stale card), so every
scheduled surface drifted toward duplicates.

An anchor is deliberately tiny: the message id, a fingerprint of what that message currently
says, and a failure counter. All three matter:

* the **message id** is what ``edit_card`` needs;
* the **fingerprint** is what makes "nothing changed" free — an identical nightly card skips
  the API call entirely, which is also what stops a "message is not modified" error from ever
  being mistaken for "the message is gone", the trap that turns a skip into a duplicate;
* the **failure counter** lets a genuinely deleted message be recovered on the second
  consecutive failure instead of being stranded forever.

Storage is one JSON file. ``NAVIG_CARD_ANCHOR_PATH`` overrides it (tests, or a space that
wants its own ledger); the default lives under ``~/.navig/cache`` because losing it costs a
single duplicate card, not data.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["forget", "get", "key", "path", "set", "clear_all"]

_ENTRY_KEYS = ("message_id", "sha", "fails")


def path() -> Path:
    override = os.environ.get("NAVIG_CARD_ANCHOR_PATH")
    if override:
        return Path(override).expanduser()
    from navig.platform.paths import config_dir  # noqa: PLC0415 — one import at call time

    return config_dir() / "cache" / "card-anchors.json"


def key(surface: str, chat_id: int) -> str:
    """The ledger key: one card per surface per chat, never shared between chats."""
    return f"{int(chat_id)}:{str(surface).strip()}"


def _load() -> dict[str, dict[str, Any]]:
    target = path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.debug("card anchors unreadable (%s); starting empty", exc)
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        # A corrupt ledger must not take a scheduled surface down with it: an empty one
        # costs one duplicate card, a crash costs every card that run.
        logger.warning("card anchors are corrupt; ignoring %s", target)
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(k): v
        for k, v in data.items()
        if isinstance(v, dict) and all(field in v for field in _ENTRY_KEYS)
    }


def _save(data: dict[str, dict[str, Any]]) -> None:
    target = path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=str(target.parent), prefix=".anchors-")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=0, sort_keys=True)
        os.replace(temp, target)
    except OSError as exc:  # a full disk must not stop the card being sent
        logger.warning("could not persist card anchors to %s: %s", target, exc)


def get(surface: str, chat_id: int) -> dict[str, Any] | None:
    """The stored anchor for this surface and chat, or ``None``."""
    entry = _load().get(key(surface, chat_id))
    return dict(entry) if entry else None


def set(
    surface: str,
    chat_id: int,
    message_id: int,
    *,
    sha: str = "",
    fails: int = 0,
) -> None:
    """Record (or refresh) the anchor for this surface and chat."""
    data = _load()
    data[key(surface, chat_id)] = {
        "message_id": int(message_id),
        "sha": str(sha),
        "fails": int(fails),
    }
    _save(data)


def forget(surface: str, chat_id: int) -> bool:
    """Drop the anchor. True when one was there."""
    data = _load()
    removed = data.pop(key(surface, chat_id), None) is not None
    if removed:
        _save(data)
    return removed


def clear_all() -> int:
    """Forget every anchor. Returns how many went (tests, and a manual reset)."""
    data = _load()
    count = len(data)
    if count:
        _save({})
    return count
