"""Open prompts and delivery marks — on disk, shared by the gateway and the scheduler.

**Keyed by (chat, prompt message id)**, so any number of check-ins can be open at
once: a pill card and the "what time did you take it?" question and tomorrow's
water card never overwrite each other. A typed reply is only ever matched to the
EXACT message it replies to — the safety property the weigh-in established, kept
here for every kind (a bare "08:30" typed at any other moment stays an ordinary
message).

The weigh-in's own open prompt stays where it always lived
(``body_metrics.pending_prompt``) so every existing reader and test keeps
working; :func:`protected_messages` reports both, which is what chat tidy needs.

Marks kept here:

* ``asked``   — ``(chat, item[:slot]) → day``, written BEFORE a scheduled send so a
  retry after a timeout (or a restart) never asks twice the same day;
* ``nudged``  — the single missed-dose nudge, claimed once per dose, never repeated;
* ``snoozes`` — a card to re-send at a given instant;
* ``water``   — the day's water card per chat, edited in place on every tap.

Never raises on a missing/corrupt file for READS (an empty state is the honest
fallback for a cache); writes are atomic.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def message_id_of(result: Any) -> int | None:
    """A Message id from either shape a send returns.

    ``TelegramChannel._api_call`` returns the Message (``result["result"]``); the
    CLI's ``commands.telegram._api_call`` returns the whole envelope. Reading the
    wrong one yields None, and a prompt recorded with no id can never be answered.
    """
    if not isinstance(result, dict):
        return None
    mid = result.get("message_id")
    if mid is None and isinstance(result.get("result"), dict):
        mid = result["result"].get("message_id")
    try:
        return int(mid) if mid is not None else None
    except (TypeError, ValueError):
        return None


def state_path() -> Path:
    from navig.platform import paths  # noqa: PLC0415

    return paths.config_dir() / "cache" / "checkins_state.json"


def _read() -> dict[str, Any]:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any]) -> None:
    from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _update(fn) -> Any:
    """Read-modify-write under a process lock; returns what *fn* returns."""
    with _LOCK:
        data = _read()
        out = fn(data)
        _write(data)
        return out


def _pkey(chat: int, mid: int) -> str:
    return f"{int(chat)}:{int(mid)}"


# ── open prompts ──────────────────────────────────────────────────────────────


def add_prompt(
    chat: int,
    message_id: int,
    *,
    item: str,
    day: str,
    slot: str | None = None,
    kind: str = "card",
) -> None:
    """Record that *message_id* in *chat* is an open question about *item*.

    ``kind``: ``card`` (a pill card with buttons) or ``time`` (a force_reply asking
    when the dose was taken).
    """

    def fn(data: dict) -> None:
        data.setdefault("prompts", {})[_pkey(chat, message_id)] = {
            "item": item,
            "day": day,
            "slot": slot,
            "kind": kind,
            "created": int(time.time()),
        }

    _update(fn)


def get_prompt(chat: int, message_id: int | None) -> dict[str, Any] | None:
    if message_id is None:
        return None
    entry = (_read().get("prompts") or {}).get(_pkey(chat, message_id))
    return dict(entry) if isinstance(entry, dict) else None


def drop_prompt(chat: int, message_id: int) -> bool:
    def fn(data: dict) -> bool:
        return (data.get("prompts") or {}).pop(_pkey(chat, message_id), None) is not None

    return bool(_update(fn))


def drop_prompts_for(chat: int, item: str, day: str, slot: str | None) -> list[tuple[int, str]]:
    """Close every open prompt about one dose; return ``[(message_id, kind)]``."""

    def fn(data: dict) -> list[tuple[int, str]]:
        prompts = data.get("prompts") or {}
        gone: list[tuple[int, str]] = []
        for key, entry in list(prompts.items()):
            c, _, m = key.partition(":")
            if (
                c == str(int(chat))
                and isinstance(entry, dict)
                and entry.get("item") == item
                and entry.get("day") == day
                and entry.get("slot") == slot
            ):
                prompts.pop(key)
                gone.append((int(m), str(entry.get("kind") or "card")))
        return gone

    return list(_update(fn))


def open_prompts() -> list[tuple[int, int, dict[str, Any]]]:
    """``[(chat, message_id, entry)]`` for every open check-in prompt."""
    out: list[tuple[int, int, dict[str, Any]]] = []
    for key, entry in (_read().get("prompts") or {}).items():
        c, _, m = key.partition(":")
        try:
            out.append((int(c), int(m), dict(entry)))
        except (TypeError, ValueError):
            continue
    return out


def protected_messages() -> set[tuple[int, int]]:
    """Every message chat tidy must NOT delete: each open check-in question.

    Includes the weigh-in's open prompt (kept in ``body_metrics``). An open
    question is not yesterday's routine until it is answered; deleting it left the
    pending record pointing at a message that no longer existed. Never raises.
    """
    out = {(c, m) for c, m, _ in open_prompts()}
    try:
        from navig.spaces import body_metrics as bm  # noqa: PLC0415

        for key, entry in bm._read_state().items():  # noqa: SLF001 — same package family
            prompt = entry.get("prompt") if isinstance(entry, dict) else None
            if isinstance(prompt, dict) and prompt.get("message_id") is not None:
                try:
                    out.add((int(key), int(prompt["message_id"])))
                except (TypeError, ValueError):
                    continue
    except Exception:  # noqa: BLE001
        pass
    return out


# ── asked / nudged marks ──────────────────────────────────────────────────────


def _mark_key(item: str, slot: str | None) -> str:
    return f"{item}:{slot}" if slot else item


def mark_asked(chat: int, item: str, slot: str | None, day: str) -> None:
    def fn(data: dict) -> None:
        data.setdefault("asked", {}).setdefault(str(int(chat)), {})[_mark_key(item, slot)] = day

    _update(fn)


def unmark_asked(chat: int, item: str, slot: str | None, day: str) -> None:
    def fn(data: dict) -> None:
        marks = (data.get("asked") or {}).get(str(int(chat))) or {}
        if marks.get(_mark_key(item, slot)) == day:
            marks.pop(_mark_key(item, slot))

    _update(fn)


def asked_on(chat: int, item: str, slot: str | None) -> str | None:
    value = ((_read().get("asked") or {}).get(str(int(chat))) or {}).get(_mark_key(item, slot))
    return str(value) if value else None


def claim_nudge(chat: int, item: str, slot: str | None, day: str) -> bool:
    """True the FIRST time this dose is nudged, False ever after — one nudge, never two."""

    def fn(data: dict) -> bool:
        marks = data.setdefault("nudged", {}).setdefault(str(int(chat)), {})
        key = _mark_key(item, slot)
        if marks.get(key) == day:
            return False
        marks[key] = day
        return True

    return bool(_update(fn))


def release_nudge(chat: int, item: str, slot: str | None, day: str) -> None:
    """Undo :func:`claim_nudge` after a send that definitely failed, so the retry can send."""

    def fn(data: dict) -> None:
        marks = (data.get("nudged") or {}).get(str(int(chat))) or {}
        if marks.get(_mark_key(item, slot)) == day:
            marks.pop(_mark_key(item, slot))

    _update(fn)


# ── snoozes ───────────────────────────────────────────────────────────────────


def add_snooze(chat: int, item: str, slot: str | None, day: str, due_ts: float) -> None:
    def fn(data: dict) -> None:
        snoozes = [
            s
            for s in data.get("snoozes") or []
            if not (
                s.get("chat") == int(chat)
                and s.get("item") == item
                and s.get("slot") == slot
                and s.get("day") == day
            )
        ]
        snoozes.append(
            {"chat": int(chat), "item": item, "slot": slot, "day": day, "due": float(due_ts)}
        )
        data["snoozes"] = snoozes

    _update(fn)


def take_due_snoozes(now_ts: float) -> list[dict[str, Any]]:
    """Remove and return every snooze whose time has come."""

    def fn(data: dict) -> list[dict[str, Any]]:
        due, keep = [], []
        for s in data.get("snoozes") or []:
            (due if float(s.get("due", 0)) <= now_ts else keep).append(s)
        data["snoozes"] = keep
        return due

    if not any(float(s.get("due", 0)) <= now_ts for s in _read().get("snoozes") or []):
        return []
    return list(_update(fn))


def cancel_snoozes(chat: int, item: str, slot: str | None, day: str) -> None:
    def fn(data: dict) -> None:
        data["snoozes"] = [
            s
            for s in data.get("snoozes") or []
            if not (
                s.get("chat") == int(chat)
                and s.get("item") == item
                and s.get("slot") == slot
                and s.get("day") == day
            )
        ]

    _update(fn)


# ── the water card ────────────────────────────────────────────────────────────


def water_card(chat: int, item: str) -> tuple[int, str] | None:
    entry = ((_read().get("water") or {}).get(str(int(chat))) or {}).get(item)
    if not isinstance(entry, dict) or entry.get("mid") is None:
        return None
    return int(entry["mid"]), str(entry.get("day") or "")


def set_water_card(chat: int, item: str, message_id: int, day: str) -> None:
    def fn(data: dict) -> None:
        data.setdefault("water", {}).setdefault(str(int(chat)), {})[item] = {
            "mid": int(message_id),
            "day": day,
        }

    _update(fn)


def forget(item: str) -> None:
    """Drop every mark and open prompt for a removed check-in."""

    def fn(data: dict) -> None:
        prompts = data.get("prompts") or {}
        for key, entry in list(prompts.items()):
            if isinstance(entry, dict) and entry.get("item") == item:
                prompts.pop(key)
        data["snoozes"] = [s for s in data.get("snoozes") or [] if s.get("item") != item]
        for per_chat in (data.get("water") or {}).values():
            if isinstance(per_chat, dict):
                per_chat.pop(item, None)
        # asked / nudged marks are keyed `item` or `item:slot` (see _mark_key) — a stale
        # one would make a re-added item with the same id skip today's first prompt.
        for marks_key in ("asked", "nudged"):
            for marks in (data.get(marks_key) or {}).values():
                if isinstance(marks, dict):
                    for key in [k for k in marks if k == item or k.startswith(f"{item}:")]:
                        marks.pop(key)

    _update(fn)
