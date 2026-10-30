"""One card that opens the other cards in place — the menu the bot never had.

Every surface used to arrive as its own message, so moving from the reminders to the
tasks meant scrolling back through the chat and pressing a button on an old card. This
module adds the missing hub:

* ``hm:home`` renders the menu, with a one-glance sheet of what is waiting and one
  button per surface;
* ``hm:open:<surface>`` **edits the same message** into that surface's card — the
  tasks list, the agenda, the reminders list or the habit check-in;
* every surface card gets a ``🏠`` row that comes back here, so the operator can walk
  the whole bot without sending a single new message.

The surfaces are the ones core already owns. A surface is described by one callable
that returns ``(card, keyboard)``; a plugin surface joins by passing its own callable
to :func:`register_surface`. Nothing here re-implements another card's body — that is
how the two drift apart.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: The callback prefix registered with the gateway dispatcher.
CALLBACK_PREFIX = "hm:"

#: ``surface -> (label, builder)``. A builder returns ``(Card, inline_keyboard rows)``.
_SURFACES: dict[str, tuple[str, Callable[..., Any]]] = {}

#: The order the menu shows them in.
_ORDER = ("tasks", "agenda", "reminders", "habits")


def _t(key: str, fallback: str, **fields: Any) -> str:
    from navig.telegram.todo_actions import _t as todo_t  # noqa: PLC0415

    try:
        text = todo_t(key, **fields)
    except Exception:  # noqa: BLE001 — a missing locale key must not cost the card
        return fallback
    return text or fallback


def register_surface(
    key: str, label: str, builder: Callable[..., Any], *, order: int | None = None
) -> None:
    """Add a surface to the menu. *builder* returns ``(Card, rows)`` and may raise."""
    _SURFACES[key] = (label, builder)
    if order is not None:
        global _ORDER  # noqa: PLW0603 — module-level ordering, set once at import
        rest = [name for name in _ORDER if name != key]
        at = max(0, min(int(order), len(rest)))
        _ORDER = tuple(rest[:at] + [key] + rest[at:])


# ── the surfaces core owns ───────────────────────────────────────────────────


def _tasks_builder(user_id: int, now: datetime) -> Any:
    from navig.telegram import todo_actions  # noqa: PLC0415

    return todo_actions.view_card("all", now=now)


def _agenda_builder(user_id: int, now: datetime) -> Any:
    from navig.telegram import agenda_actions  # noqa: PLC0415

    return agenda_actions.agenda_card(now)


def _reminders_builder(user_id: int, now: datetime) -> Any:
    from navig.telegram import reminders_view  # noqa: PLC0415

    return reminders_view.reminder_list_card(user_id, now=now)


def _habits_builder(user_id: int, now: datetime) -> Any:
    from navig.platform.paths import config_dir  # noqa: PLC0415
    from navig.telegram import habit_actions  # noqa: PLC0415

    path = config_dir() / "habits.json"
    return habit_actions.stats_card(path, now.date(), days=7)


def _install_defaults() -> None:
    if _SURFACES:
        return
    register_surface("tasks", _t("menu.surface.tasks", "📋 Tasks"), _tasks_builder, order=0)
    register_surface("agenda", _t("menu.surface.agenda", "🗓 Agenda"), _agenda_builder, order=1)
    register_surface(
        "reminders", _t("menu.surface.reminders", "⏰ Reminders"), _reminders_builder, order=2
    )
    register_surface("habits", _t("menu.surface.habits", "✅ Habits"), _habits_builder, order=3)


# ── the menu itself ──────────────────────────────────────────────────────────


def _counts(user_id: int, now: datetime) -> list[tuple[str, str]]:
    """What is waiting, as label/value pairs. Every read is isolated: one dead source
    must not empty the menu."""
    rows: list[tuple[str, str]] = []
    try:
        from navig.telegram import todo_actions  # noqa: PLC0415

        open_ids = todo_actions.open_ids()
        rows.append((_t("menu.row.tasks", "Open tasks"), str(len(open_ids))))
    except Exception:  # noqa: BLE001
        logger.debug("menu: task count unavailable", exc_info=True)
    try:
        from navig.telegram import reminders_view  # noqa: PLC0415

        pings = reminders_view.upcoming(user_id, now)
        if pings:
            first = pings[0]
            rows.append(
                (
                    _t("menu.row.reminders", "Reminders"),
                    f"{len(pings)} · {_t('menu.row.next', 'next')} {first['at']:%d.%m %H:%M}",
                )
            )
        else:
            rows.append((_t("menu.row.reminders", "Reminders"), "0"))
    except Exception:  # noqa: BLE001
        logger.debug("menu: reminder count unavailable", exc_info=True)
    return rows


def home_card(user_id: int, now: datetime | None = None) -> tuple[Any, list[list[dict[str, str]]]]:
    """The menu: one sheet of what waits, then a button per surface."""
    from navig.pim.clock import local_now  # noqa: PLC0415
    from navig.telegram.card_doc import Card, Para, Raw, Table  # noqa: PLC0415

    _install_defaults()
    now = now or local_now()
    title = _t("menu.title", "🧭 NAVIG")
    card = Card(title, meta=f"{now:%a %d %b · %H:%M}")
    rows = _counts(user_id, now)
    if rows:
        card.add(Table(rows=rows, caption=_t("menu.caption", "Right now"), bordered=False))
    else:
        card.add(
            Para(
                _t("menu.empty", "Nothing is waiting. Pick a surface below."),
                style="italic",
            )
        )
    card.add(
        Para(
            Raw(
                _t(
                    "menu.hint",
                    "Every button opens <b>in this message</b> — 🏠 comes back here.",
                )
            ),
            style="italic",
        )
    )

    buttons: list[list[dict[str, str]]] = []
    pending: list[dict[str, str]] = []
    for key in _ORDER:
        label = _SURFACES.get(key, ("", None))[0]
        if not label:
            continue
        pending.append({"text": label, "callback_data": f"{CALLBACK_PREFIX}open:{key}"})
        if len(pending) == 2:
            buttons.append(pending)
            pending = []
    if pending:
        buttons.append(pending)
    buttons.append(
        [
            {
                "text": _t("menu.tour", "🎞 Card tour"),
                "callback_data": f"{CALLBACK_PREFIX}tour:0",
            }
        ]
    )
    buttons.append(
        [{"text": _t("menu.refresh", "🔄 Refresh"), "callback_data": f"{CALLBACK_PREFIX}home"}]
    )
    return card, buttons


def _back_row() -> list[dict[str, str]]:
    return [
        {"text": _t("menu.back", "🏠 Menu"), "callback_data": f"{CALLBACK_PREFIX}home"},
        {"text": _t("menu.refresh", "🔄 Refresh"), "callback_data": f"{CALLBACK_PREFIX}refresh"},
    ]


def _tour_keys() -> list[str]:
    """Every surface the menu can open, in menu order — the tour's running order."""
    _install_defaults()
    return [key for key in _ORDER if _SURFACES.get(key, ("", None))[0]]


def tour_card(
    index: int, user_id: int, now: datetime | None = None
) -> tuple[Any, list[list[dict[str, str]]]] | None:
    """One surface per press, chained ◀ ▶ inside a single message.

    The tour is chrome over the surfaces rather than a second rendering of them: each
    stop IS the real card, so what the operator flips through is exactly what the menu
    opens. The position row is added above whatever buttons that card owns, and the
    ends wrap, because a tour with a dead end reads as a broken tour.
    """
    from navig.pim.clock import local_now  # noqa: PLC0415

    keys = _tour_keys()
    if not keys:
        return None
    total = len(keys)
    try:
        wanted = int(index)
    except (TypeError, ValueError):
        wanted = 0
    at = wanted % total
    made = surface_card(keys[at], user_id, now or local_now())
    if made is None:
        return None
    card, rows = made
    label = _SURFACES.get(keys[at], ("", None))[0]
    position = [
        {"text": "◀", "callback_data": f"{CALLBACK_PREFIX}tour:{at - 1}"},
        # A press on the label reloads the same stop: a no-op is a safe target.
        {"text": f"{at + 1}/{total} · {label}", "callback_data": f"{CALLBACK_PREFIX}tour:{at}"},
        {"text": "▶", "callback_data": f"{CALLBACK_PREFIX}tour:{at + 1}"},
    ]
    return card, [position, *rows]


def surface_card(
    key: str, user_id: int, now: datetime | None = None
) -> tuple[Any, list[list[dict[str, str]]]] | None:
    """One surface's card with a way back. ``None`` when that surface cannot render."""
    from navig.pim.clock import local_now  # noqa: PLC0415

    _install_defaults()
    entry = _SURFACES.get(key)
    if entry is None:
        return None
    now = now or local_now()
    try:
        card, keyboard = entry[1](user_id, now)
    except Exception:  # noqa: BLE001 — a broken surface must not trap the menu
        logger.exception("menu: surface %r failed to render", key)
        return None
    rows = keyboard.get("inline_keyboard") if isinstance(keyboard, dict) else keyboard
    rows = [list(row) for row in (rows or [])]
    rows.append(_back_row())
    return card, rows


async def _show(
    channel: Any, chat_id: int, message_id: int, card: Any, rows: list[list[dict[str, str]]]
) -> None:
    from navig.telegram import card_transport  # noqa: PLC0415

    await card_transport.edit_card(channel, chat_id, message_id, card, keyboard=rows)


async def handle_callback(
    channel: Any, cb_data: str, chat_id: int, message_id: int, user_id: int | None = None
) -> str:
    """Apply one press and redraw the same message. Returns the toast. Never raises."""
    body = cb_data[len(CALLBACK_PREFIX) :]
    parts = body.split(":")
    action = parts[0] if parts else ""
    owner = int(user_id if user_id is not None else chat_id)
    try:
        if action == "tour":
            try:
                at = int(parts[1])
            except (IndexError, ValueError):
                at = 0  # a garbled index starts the tour again rather than failing
            made = tour_card(at, owner)
            if made is None:
                return _t("menu.toast.gone", "That view is not available right now")
            await _show(channel, chat_id, message_id, made[0], made[1])
            return ""
        if action == "open" and len(parts) >= 2:
            made = surface_card(parts[1], owner)
            if made is None:
                return _t("menu.toast.gone", "That view is not available right now")
            await _show(channel, chat_id, message_id, made[0], made[1])
            return ""
        card, rows = home_card(owner)
        await _show(channel, chat_id, message_id, card, rows)
    except Exception as exc:  # noqa: BLE001
        if "not modified" in str(exc).lower():
            return ""
        logger.exception("menu callback %r failed", cb_data)
        return _t("menu.toast.error", "Something went wrong")
    return ""
