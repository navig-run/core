"""
Health space — built-in habit templates and their scheduling metadata.

Pure data module: no side effects, no network, no filesystem access.
All habit delivery is handled by the CronService + RuntimeStore + TelegramNotifier stack.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HabitTemplate:
    """A built-in habit that maps to a recurring CronJob."""

    key: str
    display_name: str
    description: str
    default_schedule: str   # cron expression or CronParser natural-language string
    reminder_message: str
    emoji: str


BUILTIN_HABITS: dict[str, HabitTemplate] = {
    "workout": HabitTemplate(
        key="workout",
        display_name="Morning Workout",
        description="Daily workout reminder",
        default_schedule="0 7 * * 1-5",
        reminder_message="Time to work out! 💪 Consistency beats intensity.",
        emoji="💪",
    ),
    "standup": HabitTemplate(
        key="standup",
        display_name="Stand Break",
        description="Stand and stretch every 90 minutes",
        default_schedule="every 90 minutes",
        reminder_message="Stand up and stretch! You've been sitting too long. 🧘",
        emoji="🧘",
    ),
    "water": HabitTemplate(
        key="water",
        display_name="Hydration",
        description="Drink water reminder every 2 hours",
        default_schedule="every 2 hours",
        reminder_message="Drink water! Stay hydrated. 💧",
        emoji="💧",
    ),
    "sleep": HabitTemplate(
        key="sleep",
        display_name="Wind Down",
        description="Evening wind-down reminder at 10 PM",
        default_schedule="0 22 * * *",
        reminder_message="Start winding down. Sleep is recovery. 🌙",
        emoji="🌙",
    ),
    # ── Life-rails set ────────────────────────────────────────────────────────
    # A day only holds together when the anchor, the way out of the house and the
    # one shipping block are all non-negotiable. `wake`, `out` and `ship` are that
    # floor; the rest scaffold it.
    "wake": HabitTemplate(
        key="wake",
        display_name="Wake Anchor",
        description="Fixed wake time — the anchor the whole day hangs on",
        default_schedule="0 8 * * *",
        reminder_message="Up. Feet on the floor, 10 minutes of daylight, water. Do not touch the phone. ⚓",
        emoji="⚓",
    ),
    "ship": HabitTemplate(
        key="ship",
        display_name="Ship Block",
        description="One deep block on the one thing being released",
        default_schedule="0 9 * * *",
        reminder_message="90-minute block. Phone in another room. Write one line: what will be DONE. 🚀",
        emoji="🚀",
    ),
    "out": HabitTemplate(
        key="out",
        display_name="Leave The House",
        description="Daily walk outside — not an errand",
        default_schedule="0 15 * * *",
        reminder_message="Leave the house for 30 minutes. Not an errand. Just get out. 🚶",
        emoji="🚶",
    ),
    # ── Weekend shape ─────────────────────────────────────────────────────────
    # The floor is the same seven days a week, but its TIMES are hung on a workday:
    # the walk lands after the 17:00 shutdown, the ship block after the 10:00 start.
    # On a Saturday there is no workday to hang them on, so nothing fires the day into
    # motion and it quietly evaporates — which is exactly what the tracker showed
    # (every complete day Mon–Fri, three of four weekend days blank). These two run the
    # same labels earlier, before an unstructured day has a chance to dissolve.
    "out_weekend": HabitTemplate(
        key="out_weekend",
        display_name="Weekend Walk",
        description="The walk, moved early — a weekend has no 17:00 to trigger it",
        default_schedule="0 10 * * 0,6",
        reminder_message=(
            "Out of the house, 30 minutes — now, while the day is still yours. "
            "It is the same Walk; only the hour changed. 🥾"
        ),
        emoji="🥾",
    ),
    "ship_weekend": HabitTemplate(
        key="ship_weekend",
        display_name="Weekend Ship Block",
        description="A short, honest shipping block — 45 minutes, not 90",
        default_schedule="0 11 * * 0,6",
        reminder_message=(
            "45 minutes on schema. Short on purpose — a weekend block you actually "
            "do beats a 90-minute one you skip. 🛠"
        ),
        emoji="🛠",
    ),
    "nightclose": HabitTemplate(
        key="nightclose",
        display_name="Night Is Closed",
        description="Hard stop on work; phone leaves the bedroom",
        default_schedule="0 22 * * *",
        reminder_message="NIGHT IS CLOSED. Work does not start — especially if it is flowing. Phone out of the bedroom. 🔒",
        emoji="🔒",
    ),
    "checkin": HabitTemplate(
        key="checkin",
        display_name="Evening Check-in",
        description="60-second tracker row + three journal lines",
        default_schedule="15 22 * * *",
        reminder_message="Check-in, 60 seconds: one tracker row + three journal lines. The only real failure is to stop recording. ✍️",
        emoji="✍️",
    ),
    "people": HabitTemplate(
        key="people",
        display_name="People",
        description="Weekly contact — visit someone, write to someone",
        default_schedule="0 12 * * 6",
        reminder_message="People: visit your mother + send one friend a SPECIFIC invitation. Sent counts — a reply is not your job. 👥",
        emoji="👥",
    ),
    "review": HabitTemplate(
        key="review",
        display_name="Weekly Review",
        description="Sunday review of streaks, body, money and what broke",
        default_schedule="0 19 * * 0",
        reminder_message="Weekly review, 10 minutes. Streaks, body, money, what broke, one decision. 📊",
        emoji="📊",
    ),
}


def get_habit_template(key: str) -> HabitTemplate | None:
    """Return the HabitTemplate for *key*, or None if not found."""
    return BUILTIN_HABITS.get(key)


def list_habit_templates() -> list[HabitTemplate]:
    """Return all built-in habit templates."""
    return list(BUILTIN_HABITS.values())
