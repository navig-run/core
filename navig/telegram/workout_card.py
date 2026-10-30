"""`/workout` as cards: the two forms, the reminder it set, and the habit it created.

The command had four plain answers (usage, bad time, one-time reminder, weekday habit) and a
usage block written as two bare lines with no code fence — nothing an operator could tap or
copy. The cards keep every fact the text carried (the time, whether it landed today or
tomorrow, the reminder id) and add the press that turns a one-off into the recurring habit.
"""

from __future__ import annotations

from navig.telegram.card_doc import Button, Card, Code, Para, Raw, Table

_FALLBACK = {
    "workout.card.usage_title": "Workout reminder",
    "workout.card.forms": "Two forms",
    "workout.card.one_off": "One-time reminder, today (or tomorrow if that time has passed)",
    "workout.card.daily": "Recurring weekday habit, firing every Mon–Fri",
    "workout.card.copy": "📋 Copy the command",
    "workout.card.set_title": "Workout reminder set",
    "workout.card.when": "When",
    "workout.card.day": "Day",
    "workout.card.today": "today",
    "workout.card.tomorrow": "tomorrow",
    "workout.card.id": "Reminder",
    "workout.card.recurring": "Make it recurring",
    "workout.card.habit_title": "Daily workout habit",
    "workout.card.schedule": "Schedule",
    "workout.card.weekdays": "weekdays (Mon–Fri)",
    "workout.card.manage": "Manage with navig habit list",
    "workout.card.habits": "💪 Habits",
    "workout.card.bad_time": "Time must be 24h",
    "workout.card.bad_time_hint": "Use a time like 07:30 — hours 0–23, minutes 0–59.",
}

ONE_OFF = "/workout 07:30"
DAILY = "/workout daily 07:30"


def _t(key: str, **fields: object) -> str:
    text = _FALLBACK.get(key, "")
    for name, value in fields.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def usage_card(*, note: str = "") -> Card:
    """No argument: both forms as a table of examples, each with a copyable command."""
    card = Card(_t("workout.card.usage_title"), glyph="💪")
    if note:
        card.add(Para(Raw(f"<b>{_t('workout.card.bad_time')}</b> — {_t(note)}")))
    card.add(Para(Raw(f"<b>{_t('workout.card.forms')}</b>")))
    card.add(
        Table(
            header=["Command", "What it does"],
            rows=[
                [Raw(f"<code>{ONE_OFF}</code>"), _t("workout.card.one_off")],
                [Raw(f"<code>{DAILY}</code>"), _t("workout.card.daily")],
            ],
            bordered=False,
        )
    )
    card.add(Code(f"{ONE_OFF}\n{DAILY}", language="text"))
    card.buttons(Button(_t("workout.card.copy"), copy=ONE_OFF, style="primary"))
    card.buttons(Button(_t("workout.card.habits"), data="nav:cmd:/habits"))
    return card


def reminder_card(*, when: str, rolled: bool, reminder_id: object = "") -> Card:
    """The one-time reminder that was just created, with the press that makes it recurring."""
    day = _t("workout.card.tomorrow") if rolled else _t("workout.card.today")
    card = Card(_t("workout.card.set_title"), glyph="⏰", meta=f"{when} {day}")
    rows: list[list[object]] = [
        [Raw(_t("workout.card.when")), Raw(f"<b>{when}</b>")],
        [Raw(_t("workout.card.day")), Raw(day)],
    ]
    if reminder_id not in ("", None):
        rows.append([Raw(_t("workout.card.id")), Raw(f"<code>{reminder_id}</code>")])
    card.add(Table(rows=rows, bordered=False))
    card.buttons(
        Button(
            _t("workout.card.recurring"),
            data=f"nav:cmd:/workout daily {when}",
            style="primary",
        )
    )
    return card


def habit_card(*, when: str) -> Card:
    """The weekday habit that now exists, and where to manage it."""
    card = Card(_t("workout.card.habit_title"), glyph="💪", meta=when)
    card.add(
        Table(
            rows=[
                [Raw(_t("workout.card.when")), Raw(f"<b>{when}</b>")],
                [Raw(_t("workout.card.schedule")), Raw(_t("workout.card.weekdays"))],
            ],
            bordered=False,
        )
    )
    card.add(Para(_t("workout.card.manage"), style="italic"))
    card.buttons(Button(_t("workout.card.habits"), data="nav:cmd:/habits", style="primary"))
    return card


__all__ = ["DAILY", "ONE_OFF", "habit_card", "reminder_card", "usage_card"]
