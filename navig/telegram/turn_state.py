"""Per-chat turn state — a scheduled card must wait for an in-flight reply.

Incident 2026-10-22: the daily review card arrived at 08:30 while the operator's
question was still being answered (question 08:29, answer 08:32), so a scheduled
message visually replaced the answer. The fix is a small durable marker: the channel
marks a chat "turn in flight" while an inbound message is being handled, digest
(SUMMARY) cards are deferred instead of sent, and the channel drains them when the
turn ends.

Durable because the sender is a different process: the review cron writes, the
gateway drains. Writes are atomic (temp + replace) and best-effort — a missing or
unreadable file must never hold a reply.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "build_card",
    "defer_summary",
    "in_flight",
    "mark_finished",
    "mark_started",
    "screen_payload",
    "take_deferred",
]

#: A turn older than this is treated as dead (a crashed worker must not block digests).
TURN_STALE_S: float = 15 * 60.0


def _path() -> Path:
    from navig.platform.paths import config_dir

    return config_dir() / "telegram" / "turn_state.json"


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logger.debug("turn_state write failed", exc_info=True)


def _chat_key(chat_id: int | str) -> str:
    return str(int(chat_id))


def mark_started(chat_id: int | str, *, now: float | None = None) -> None:
    data = _load()
    data.setdefault("turns", {})[_chat_key(chat_id)] = {
        "started_at": float(now if now is not None else time.time()),
        "pid": os.getpid(),
    }
    _save(data)


def mark_finished(chat_id: int | str) -> None:
    data = _load()
    turns = data.get("turns")
    if isinstance(turns, dict) and turns.pop(_chat_key(chat_id), None) is not None:
        _save(data)


def in_flight(chat_id: int | str, *, now: float | None = None) -> bool:
    """True while a turn is being handled; a stale marker (crashed worker) is ignored."""
    data = _load()
    turn = (data.get("turns") or {}).get(_chat_key(chat_id))
    if not isinstance(turn, dict):
        return False
    moment = float(now if now is not None else time.time())
    try:
        started = float(turn.get("started_at") or 0)
    except (TypeError, ValueError):
        return False
    return 0 <= moment - started <= TURN_STALE_S


def screen_payload(screen: Any, context: Any) -> dict[str, Any]:
    """Serialize one card for later delivery by another process (no callables)."""
    rows = []
    for row in getattr(screen, "rows", ()) or ():
        buttons = []
        for button in row:
            buttons.append(
                {
                    "text": getattr(button, "text", ""),
                    "action": getattr(button, "action", None),
                    "url": getattr(button, "url", None),
                    "callback_data": getattr(button, "callback_data", None),
                }
            )
        rows.append(buttons)
    return {
        "title": getattr(screen, "title", ""),
        "body": getattr(screen, "body", ""),
        "kind": getattr(screen, "kind", "summary"),
        "open_label": getattr(screen, "open_label", "Open"),
        "open_action": getattr(screen, "open_action", "open"),
        "rows": rows,
        "context": {
            "prefix": getattr(context, "prefix", ""),
            "key": getattr(context, "key", ""),
        },
    }


def build_card(item: dict[str, Any]) -> tuple[Any, Any]:
    """Rebuild (Screen, Context) from :func:`screen_payload`."""
    from navig.telegram import cards  # noqa: PLC0415 — avoid an import cycle

    buttons = [
        [
            cards.CardButton(
                text=str(button.get("text") or ""),
                action=button.get("action"),
                url=button.get("url"),
                callback_data=button.get("callback_data"),
            )
            for button in row
        ]
        for row in item.get("rows") or []
    ]
    context_data = item.get("context") or {}
    screen = cards.Screen(
        title=str(item.get("title") or "NAVIG"),
        body=str(item.get("body") or ""),
        kind=str(item.get("kind") or cards.SUMMARY),
        rows=tuple(tuple(row) for row in buttons),
        open_label=str(item.get("open_label") or "Open"),
        open_action=str(item.get("open_action") or "open"),
    )
    return screen, cards.Context(
        prefix=str(context_data.get("prefix") or ""),
        key=str(context_data.get("key") or ""),
    )


def defer_summary(chat_id: int | str, screen: Any, context: Any) -> int:
    """Park a digest card until the turn ends. Returns the pending count."""
    data = _load()
    pending = data.setdefault("pending", {})
    queue = pending.setdefault(_chat_key(chat_id), [])
    queue.append(screen_payload(screen, context))
    # Bounded: a broken drain loop must not grow the state file forever.
    del queue[:-10]
    _save(data)
    return len(queue)


def take_deferred(chat_id: int | str) -> list[dict[str, Any]]:
    data = _load()
    pending = data.get("pending")
    if not isinstance(pending, dict):
        return []
    queue = pending.pop(_chat_key(chat_id), None)
    if queue:
        _save(data)
    return list(queue or [])
