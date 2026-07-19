"""
navig habit — Daily habit management powered by CronService.

Habits are persistent CronJobs with:
  - name prefix  "habit:"
  - command      "NAVIG_HABIT_REMINDER:<chat_id>:<base64_message>"

CronService fires them on schedule; _execute_job_command() detects the prefix
and writes a due-now reminder to RuntimeStore.  The existing _poll_due_reminders()
loop (navig/gateway/channels/telegram.py:615) delivers it within 15 seconds.

Storage goes through navig.scheduler.habit_store — the LIVE scheduler store:
mutations run through the daemon's /api/deck/schedule routes when the gateway
is up (race-free against the running scheduler) and fall back to a detached
CronService over the same store when it is down (picked up on the daemon's
next start). The legacy ~/.navig/daemon/cron_jobs.json store this CLI used to
write was never executed by the scheduler; habit_store migrates it once.
"""

from __future__ import annotations

import base64
from datetime import datetime

import typer

from navig import console_helper as ch
from navig.scheduler import habit_store
from navig.scheduler.cron_service import CronParser
from navig.spaces.health import (
    BUILTIN_HABITS,
    HabitTemplate,
    get_habit_template,
    list_habit_templates,
)

habit_app = typer.Typer(
    name="habit",
    help="Manage daily health habits (workout, hydration, stand breaks, wind-down)",
    invoke_without_command=True,
    no_args_is_help=False,
)

_HABIT_NAME_PREFIX = "habit:"
_HABIT_CMD_PREFIX = "NAVIG_HABIT_REMINDER"

# ── Storage helpers (live scheduler store via habit_store) ────────────────────

def _load_habit_jobs() -> list[dict]:
    """Return only job dicts that are habits (name starts with 'habit:')."""
    return habit_store.list_habit_jobs()


# ── Config helpers ─────────────────────────────────────────────────────────────

def _resolve_default_chat_id() -> int | None:
    """Try to read the first allowed_users entry as the notification chat_id."""
    try:
        from navig.config import get_config_manager
        cm = get_config_manager()
        tg_cfg = cm.global_config.get("telegram", {}) if cm.global_config else {}
        users = tg_cfg.get("allowed_users", [])
        if users:
            return int(users[0])
    except Exception:
        pass
    return None


# ── Schedule builder ───────────────────────────────────────────────────────────

_DAY_MAP: dict[str, str] = {
    "weekdays": "1-5",
    "weekday": "1-5",
    "weekends": "0,6",
    "weekend": "0,6",
    "daily": "*",
    "everyday": "*",
    "monday": "1",
    "tuesday": "2",
    "wednesday": "3",
    "thursday": "4",
    "friday": "5",
    "saturday": "6",
    "sunday": "0",
}


def _build_schedule(
    template: HabitTemplate,
    time_str: str | None,
    days: str,
    every: str | None,
) -> str:
    """Translate CLI options into a cron expression or natural-language string."""
    if every:
        every = every.strip()
        # Normalise shorthand: "90min" → "90 minutes"
        import re
        every = re.sub(r"(\d+)\s*min(utes?)?$", r"\1 minutes", every, flags=re.I)
        every = re.sub(r"(\d+)\s*h(ours?)?$", r"\1 hours", every, flags=re.I)
        return f"every {every}"

    if time_str:
        parts = time_str.strip().split(":")
        hour, minute = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        day_field = _DAY_MAP.get(days.lower(), "*")
        return f"{minute} {hour} * * {day_field}"

    return template.default_schedule


# ── Command: templates ─────────────────────────────────────────────────────────

@habit_app.callback(invoke_without_command=True)
def _habit_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _habit_list_impl()


@habit_app.command("templates")
def habit_templates() -> None:
    """Show all available built-in habit templates."""
    table = ch.create_table(
        title="Built-in Habit Templates",
        columns=[
            {"name": "Key", "style": "cyan"},
            {"name": "", "justify": "center"},
            {"name": "Name"},
            {"name": "Default Schedule", "style": "dim"},
            {"name": "Description"},
        ],
    )
    for h in list_habit_templates():
        table.add_row(h.key, h.emoji, h.display_name, h.default_schedule, h.description)
    ch.print_table(table)
    ch.dim("Add one: navig habit add workout --time 07:00 --days weekdays")


# ── Command: add ──────────────────────────────────────────────────────────────

@habit_app.command("add")
def habit_add(
    habit_key: str = typer.Argument(..., help="Habit type: workout, standup, water, sleep"),
    time_str: str | None = typer.Option(None, "--time", "-t", help="Override time (HH:MM)"),
    days: str = typer.Option("weekdays", "--days", "-d", help="weekdays, weekends, daily, or day name"),
    every: str | None = typer.Option(None, "--every", help="Interval override: '90min', '2h'"),
    message: str | None = typer.Option(None, "--message", "-m", help="Custom reminder message"),
    chat_id: int | None = typer.Option(None, "--chat-id", help="Telegram chat ID to send reminders to"),
) -> None:
    """Add a recurring habit reminder.

    Examples:
      navig habit add workout --time 07:00 --days weekdays
      navig habit add standup --every 90min
      navig habit add water --every 2h
      navig habit add sleep
    """
    template = get_habit_template(habit_key)
    if template is None:
        valid = ", ".join(BUILTIN_HABITS.keys())
        ch.error(f"Unknown habit '{habit_key}'.", f"Valid options: {valid}")
        ch.dim("Run 'navig habit templates' to see all options.")
        raise typer.Exit(1)

    # Resolve chat_id
    effective_chat_id = chat_id or _resolve_default_chat_id()
    if not effective_chat_id:
        ch.warning("Could not determine your Telegram chat ID from config.")
        ch.dim("Set telegram.allowed_users in your config or pass --chat-id.")
        raw = typer.prompt("Enter your Telegram chat ID (leave blank to skip)")
        if raw.strip():
            effective_chat_id = int(raw.strip())
        else:
            ch.error("Chat ID required for reminder delivery.")
            raise typer.Exit(1)

    # Check for existing habit
    habit_name = f"{_HABIT_NAME_PREFIX}{habit_key}"
    duplicate = next((j for j in _load_habit_jobs() if j.get("name") == habit_name), None)
    if duplicate:
        ch.warning(f"Habit '{habit_key}' already exists (id: {duplicate['id']}).")
        if not typer.confirm("Replace it?", default=False):
            ch.dim("Keeping existing habit. Use 'navig habit remove' first if needed.")
            raise typer.Exit()

    schedule = _build_schedule(template, time_str, days, every)
    reminder_text = message or template.reminder_message

    b64_msg = base64.b64encode(reminder_text.encode()).decode()
    command = f"{_HABIT_CMD_PREFIX}:{effective_chat_id}:{b64_msg}"

    job = habit_store.create_job(
        habit_name, schedule, command, timeout_seconds=30, replace=True
    )

    next_run_display = str(job.get("next_run") or "")[:16].replace("T", " ")
    if not next_run_display:
        next_run_display = CronParser.calculate_next(schedule).strftime("%Y-%m-%d %H:%M")

    ch.success(f"{template.emoji} Habit '{template.display_name}' added.")
    ch.console.print(f"  [dim]Schedule:[/dim]  {schedule}")
    ch.console.print(f"  [dim]Next run:[/dim]  {next_run_display}")
    ch.console.print(f"  [dim]Message:[/dim]   {reminder_text}")
    ch.dim("Reminder will fire via Telegram when the daemon is running.")


# ── Command: list ─────────────────────────────────────────────────────────────

def _habit_list_impl() -> None:
    habits = _load_habit_jobs()
    if not habits:
        ch.console.print("[dim]No habits configured yet.[/dim]")
        ch.dim("Run 'navig habit templates' to see options, then 'navig habit add <key>'.")
        return

    table = ch.create_table(
        title="Active Habits",
        columns=[
            {"name": "Key", "style": "cyan"},
            {"name": "Status", "justify": "center"},
            {"name": "Schedule"},
            {"name": "Next Run", "style": "dim"},
        ],
    )
    for j in sorted(habits, key=lambda x: x.get("name", "")):
        key = j.get("name", "").removeprefix(_HABIT_NAME_PREFIX)
        tmpl = get_habit_template(key)
        emoji = tmpl.emoji if tmpl else "📌"
        enabled = j.get("enabled", True)
        status = "[green]✓ on[/green]" if enabled else "[dim]⏸ off[/dim]"
        schedule = j.get("schedule", "—")
        next_run_raw = j.get("next_run")
        if next_run_raw:
            try:
                nr = datetime.fromisoformat(next_run_raw)
                next_run_str = nr.strftime("%Y-%m-%d %H:%M")
            except Exception:
                next_run_str = next_run_raw
        else:
            next_run_str = "—"
        table.add_row(f"{emoji} {key}", status, schedule, next_run_str)

    ch.print_table(table)


@habit_app.command("list")
def habit_list() -> None:
    """List all active habit jobs."""
    _habit_list_impl()


# ── Command: remove ───────────────────────────────────────────────────────────

@habit_app.command("remove")
def habit_remove(
    habit_key: str = typer.Argument(..., help="Habit key (e.g. workout) or job ID"),
) -> None:
    """Remove a habit reminder."""
    # Try by habit name first, then by job ID
    habit_name = f"{_HABIT_NAME_PREFIX}{habit_key}"
    removed = habit_store.delete_jobs(habit_name)
    if not removed:
        removed = habit_store.delete_jobs(habit_key)

    if not removed:
        ch.error(f"Habit '{habit_key}' not found.")
        ch.dim("Run 'navig habit list' to see configured habits.")
        raise typer.Exit(1)

    ch.success(f"Habit '{habit_key}' removed.")


# ── Command: status ───────────────────────────────────────────────────────────

@habit_app.command("status")
def habit_status() -> None:
    """Show habit job details including last run and next fire time."""
    habits = _load_habit_jobs()
    if not habits:
        ch.console.print("[dim]No habits configured.[/dim]")
        return

    for j in sorted(habits, key=lambda x: x.get("name", "")):
        key = j.get("name", "").removeprefix(_HABIT_NAME_PREFIX)
        tmpl = get_habit_template(key)
        emoji = tmpl.emoji if tmpl else "📌"
        enabled_str = "[green]enabled[/green]" if j.get("enabled", True) else "[dim]disabled[/dim]"
        last_status = j.get("last_status") or "never run"
        last_run = j.get("last_run") or "—"
        next_run = j.get("next_run") or "—"
        if last_run and last_run != "—":
            try:
                last_run = datetime.fromisoformat(last_run).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
        if next_run and next_run != "—":
            try:
                next_run = datetime.fromisoformat(next_run).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass

        ch.console.print(f"\n{emoji} [cyan]{key}[/cyan] — {enabled_str}")
        ch.console.print(f"   Schedule:    {j.get('schedule', '—')}")
        ch.console.print(f"   Next run:    {next_run}")
        ch.console.print(f"   Last run:    {last_run}")
        ch.console.print(f"   Last status: {last_status}")
