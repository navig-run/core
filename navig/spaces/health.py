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
}


def get_habit_template(key: str) -> HabitTemplate | None:
    """Return the HabitTemplate for *key*, or None if not found."""
    return BUILTIN_HABITS.get(key)


def list_habit_templates() -> list[HabitTemplate]:
    """Return all built-in habit templates."""
    return list(BUILTIN_HABITS.values())
