"""The Telegram text and keyboards for check-ins.

Callback data (Telegram caps it at 64 bytes; the longest here is
``ci:<20-char id>:y120:<yyyymmdd>:<hhmm>`` = 42 bytes)::

    ci:<id>:t:<day>:<slot>      taken now
    ci:<id>:s:<day>:<slot>      skip this dose
    ci:<id>:z:<day>:<slot>      snooze 15 minutes
    ci:<id>:e:<day>:<slot>      "taken earlier…" → quick choices
    ci:<id>:e<mins>:<day>:<slot> taken <mins> ago (30 · 60 · 120)
    ci:<id>:eo:<day>:<slot>     taken earlier, ask for the time (force_reply)
    ci:<id>:y<mins>:<day>:<slot> yes, record ANOTHER dose (the double-dose guard)
    ci:<id>:n:<day>:<slot>      no, do not record it again
    ci:<id>:b:<day>:<slot>      back to the main buttons
    ci:<id>:a1|a2:<day>         water +1 / +2
    ci:<id>:u:<day>             water: undo one
    ci:<id>:l1:<day>            water +1 from the /checkins card (redraws that card)

Strings resolve through the operator's ONE global language (``navig/locales``).
Every label is plain text; HTML is escaped here, once. Nothing in this module
says anything about how much of a medication to take.
"""

from __future__ import annotations

import html
from datetime import date
from typing import Any

PREFIX = "ci:"
SNOOZE_MINUTES = 15
EARLIER_CHOICES = (30, 60, 120)

_translator: Any = None


def t(key: str, **fields: object) -> str:
    """A localized string; English, then the key itself, as fallbacks. Never raises."""
    global _translator
    try:
        if _translator is None:
            from navig.core.i18n import Translator  # noqa: PLC0415

            _translator = Translator()
        return _translator.t(key, **fields)
    except Exception:  # noqa: BLE001 — a label must never take a check-in down
        return key


def compact(day: str) -> str:
    return day.replace("-", "")


def expand(compact_day: str) -> str:
    if len(compact_day) != 8 or not compact_day.isdigit():
        raise ValueError(f"bad day {compact_day!r}")
    return f"{compact_day[0:4]}-{compact_day[4:6]}-{compact_day[6:8]}"


def fmt_slot(slot: str | None) -> str:
    s = (slot or "").replace(":", "")
    return f"{s[:2]}:{s[2:]}" if len(s) == 4 else (slot or "")


def cb(item_id: str, action: str, day: str, slot: str | None = None) -> str:
    data = f"{PREFIX}{item_id}:{action}:{compact(day)}"
    if slot:
        data += f":{slot}"
    if len(data.encode("utf-8")) > 64:  # pragma: no cover — ids are validated short
        raise ValueError("callback data over Telegram's 64-byte limit")
    return data


def parse_cb(data: str) -> tuple[str, str, str, str | None]:
    """``ci:<id>:<action>:<yyyymmdd>[:<slot>]`` → ``(id, action, day, slot)``."""
    parts = (data or "").split(":")
    if len(parts) < 4 or parts[0] + ":" != PREFIX:
        raise ValueError(f"not a check-in callback: {data!r}")
    slot = parts[4] if len(parts) > 4 and parts[4] else None
    return parts[1], parts[2], expand(parts[3]), slot


def _esc(text: str) -> str:
    return html.escape(text or "")


def _short_day(day: str) -> str:
    try:
        return date.fromisoformat(day).strftime("%d.%m")
    except ValueError:
        return day


# ── pills ─────────────────────────────────────────────────────────────────────


def _pill_head(item: Any, slot: str | None) -> list[str]:
    lines = [f"💊 <b>{_esc(item.name)}</b>"]
    if item.dose:
        lines.append(_esc(item.dose))  # verbatim, exactly as the operator typed it
    lines.append(f"<i>{_esc(t('checkins.pills.due', time=fmt_slot(slot)))}</i>")
    return lines


def _main_buttons(item: Any, day: str, slot: str | None) -> list[list[dict[str, str]]]:
    return [
        [
            {"text": t("checkins.btn.taken"), "callback_data": cb(item.id, "t", day, slot)},
            {"text": t("checkins.btn.skip"), "callback_data": cb(item.id, "s", day, slot)},
        ],
        [
            {
                "text": t("checkins.btn.snooze", minutes=SNOOZE_MINUTES),
                "callback_data": cb(item.id, "z", day, slot),
            },
            {"text": t("checkins.btn.earlier"), "callback_data": cb(item.id, "e", day, slot)},
        ],
    ]


def pill_card(
    item: Any, day: str, slot: str | None, *, nudge: bool = False
) -> tuple[str, dict[str, Any]]:
    lines = _pill_head(item, slot)
    if nudge:
        lines.insert(0, f"<b>{_esc(t('checkins.pills.nudge'))}</b>")
    return "\n".join(lines), {"inline_keyboard": _main_buttons(item, day, slot)}


def pill_earlier(item: Any, day: str, slot: str | None) -> tuple[str, dict[str, Any]]:
    lines = _pill_head(item, slot) + ["", _esc(t("checkins.pills.earlier_q"))]
    labels = {30: "checkins.btn.ago30", 60: "checkins.btn.ago60", 120: "checkins.btn.ago120"}
    keyboard = [
        [
            {"text": t(labels[m]), "callback_data": cb(item.id, f"e{m}", day, slot)}
            for m in EARLIER_CHOICES
        ],
        [
            {"text": t("checkins.btn.other_time"), "callback_data": cb(item.id, "eo", day, slot)},
            {"text": t("checkins.btn.back"), "callback_data": cb(item.id, "b", day, slot)},
        ],
    ]
    return "\n".join(lines), {"inline_keyboard": keyboard}


def pill_double(
    item: Any, day: str, slot: str | None, *, first_at: str, offset: int
) -> tuple[str, dict[str, Any]]:
    """The double-dose guard: a second "taken" for one slot asks first."""
    lines = _pill_head(item, slot) + [
        "",
        f"⚠️ {_esc(t('checkins.pills.double', time=first_at))}",
    ]
    keyboard = [
        [
            {
                "text": t("checkins.btn.double_yes"),
                "callback_data": cb(item.id, f"y{offset}", day, slot),
            },
            {"text": t("checkins.btn.double_no"), "callback_data": cb(item.id, "n", day, slot)},
        ]
    ]
    return "\n".join(lines), {"inline_keyboard": keyboard}


def pill_settled(item: Any, slot: str | None, status: str, *, at: str = "", day: str = "") -> str:
    """What the card becomes once answered — no buttons left to press twice."""
    label = item.label()
    when = fmt_slot(slot)
    if status == "taken":
        text = t("checkins.pills.taken_at", name=label, time=at or when)
    elif status == "skipped":
        text = t("checkins.pills.skipped", name=label, time=when)
    elif status == "snoozed":
        text = t("checkins.pills.snoozed", name=label, time=at)
    else:
        text = t("checkins.pills.missed", name=label, time=when, date=_short_day(day))
    return _esc(text)


# ── water ─────────────────────────────────────────────────────────────────────


def water_card(
    item: Any, day: str, total: int, *, reminder: bool = False
) -> tuple[str, dict[str, Any]]:
    lines = []
    if reminder:
        lines.append(f"<b>{_esc(t('checkins.water.reminder'))}</b>")
    bar = "●" * min(total, item.target) + "○" * max(0, item.target - total)
    lines.append(f"💧 {_esc(t('checkins.water.today', total=total, target=item.target))}")
    lines.append(f"<code>{bar}</code>")
    if total >= item.target:
        lines.append(_esc(t("checkins.water.done")))
    row = [
        {"text": t("checkins.btn.add1"), "callback_data": cb(item.id, "a1", day)},
        {"text": t("checkins.btn.add2"), "callback_data": cb(item.id, "a2", day)},
    ]
    if total > 0:
        row.append({"text": t("checkins.btn.undo"), "callback_data": cb(item.id, "u", day)})
    return "\n".join(lines), {"inline_keyboard": [row]}


# ── /checkins ─────────────────────────────────────────────────────────────────


def list_card(entries: list[dict[str, Any]], day: str) -> tuple[str, dict[str, Any] | None]:
    """The /checkins card: one line per check-in with today's state."""
    lines = [f"<b>{_esc(t('checkins.list.title'))}</b> · {_esc(_short_day(day))}", ""]
    if not entries:
        lines.append(_esc(t("checkins.list.empty")))
        return "\n".join(lines), None
    keyboard: list[list[dict[str, str]]] = []
    for e in entries:
        state = e.get("state") or ""
        paused = "" if e.get("enabled", True) else f" · <i>{_esc(t('checkins.list.paused'))}</i>"
        lines.append(f"{e.get('emoji', '')} {_esc(e.get('label', ''))} — {_esc(state)}{paused}")
        if e.get("template") == "water" and e.get("enabled", True):
            keyboard.append(
                [
                    {
                        "text": f"💧 {t('checkins.btn.add1')}",
                        "callback_data": cb(e["id"], "l1", day),
                    },
                ]
            )
    lines += ["", f"<i>{_esc(t('checkins.list.hint'))}</i>"]
    return "\n".join(lines), ({"inline_keyboard": keyboard} if keyboard else None)
