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
from datetime import date, datetime, timedelta
from pathlib import Path

import typer

from navig import console_helper as ch
from navig.scheduler import habit_store
from navig.scheduler.cron_service import CronParser
from navig.scheduler.habit_store import HABIT_NAME_PREFIX
from navig.spaces import habit_tracker
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
    habit_name = f"{HABIT_NAME_PREFIX}{habit_key}"
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
    # A table of "on" rows with next-run times is a lie while the extension is
    # off — they are scheduled and nothing is delivered. Say so above the table,
    # so the extension state outranks the per-habit state.
    from navig.telegram.habit_actions import extension_banner_cli

    _banner = extension_banner_cli()
    if _banner:
        ch.warning(*_banner)

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
        key = j.get("name", "").removeprefix(HABIT_NAME_PREFIX)
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
    habit_name = f"{HABIT_NAME_PREFIX}{habit_key}"
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
        key = j.get("name", "").removeprefix(HABIT_NAME_PREFIX)
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


# ── Tracker: habits.csv in the active space ───────────────────────────────────
#
# A reminder that fires and is never answered leaves no trace, so a missed week
# looks exactly like a perfect one. `log`/`today`/`streak` close that loop by
# writing to the space's own habits.csv — the same date,habit,completed,notes
# shape the file has always had, so rows written by hand stay valid.

TRACKER_FILE = habit_tracker.TRACKER_FILE
TRACKER_FIELDS = habit_tracker.TRACKER_FIELDS

#: The three that decide whether a day counts at all.
NON_NEGOTIABLES = habit_tracker.NON_NEGOTIABLES

# Thin aliases over navig.spaces.habit_tracker — the store lives there so the
# Telegram check-in card writes through the same implementation as the CLI.
_tracker_path = habit_tracker.tracker_path
_read_tracker = habit_tracker.read_tracker
_write_tracker = habit_tracker.write_tracker
_is_done = habit_tracker.is_done
_streak_for = habit_tracker.streak_for


# ── Command: log ──────────────────────────────────────────────────────────────

@habit_app.command("log")
def habit_log(
    habit_key: str = typer.Argument(..., help="Tracker label, e.g. wake, out, ship"),
    value: str | None = typer.Option(
        None, "--value", "-v", help="Value instead of yes: a count, a time, a score"
    ),
    note: str = typer.Option("", "--note", "-n", help="Short note for the row"),
    on_date: str | None = typer.Option(
        None, "--date", help="Log for another day (YYYY-MM-DD); defaults to today"
    ),
    undo: bool = typer.Option(False, "--undo", help="Mark as not done instead of done"),
    space: str | None = typer.Option(
        None, "--space", help="Space directory (or habits.csv path) to write to"
    ),
) -> None:
    """Log a habit into a space's habits.csv.

    Idempotent: logging the same label twice on the same day updates that row
    rather than adding a second one.

      navig habit log wake --note "встал 08:04"
      navig habit log deep --value 2
      navig habit log ship --undo --note "ушло на клиента"
    """
    if on_date:
        try:
            day = date.fromisoformat(on_date).isoformat()
        except ValueError:
            ch.error(f"Bad --date '{on_date}'.", "Expected YYYY-MM-DD.")
            raise typer.Exit(1) from None
    else:
        day = date.today().isoformat()

    completed = "no" if undo else (value if value is not None else "yes")

    path = _tracker_path(space)
    was = habit_tracker.upsert(path, day, habit_key, completed, note)

    ch.success(f"{habit_key} → {completed}" + (f" (was {was})" if was else ""))
    ch.dim(f"{path}")


# ── Command: journal ──────────────────────────────────────────────────────────

@habit_app.command("journal")
def habit_journal(
    text: str = typer.Argument(..., help="The three lines, separated by newlines"),
    on_date: str | None = typer.Option(
        None, "--date", help="Write into another day (YYYY-MM-DD); defaults to today"
    ),
    space: str | None = typer.Option(
        None, "--space", help="Space directory (or habits.csv path) that owns the journal"
    ),
) -> None:
    """Write the day's three lines into the space journal.

    The same entry the check-in card asks for after the day is closed — this is
    the route that does not need Telegram, a phone, or the gateway running.

      navig habit journal "finished the release page
      lost an hour to a client
      write the pricing copy"

    Exactly three lines get the card's labels; any other number is written down
    as you wrote it.
    """
    if on_date:
        try:
            day = date.fromisoformat(on_date).isoformat()
        except ValueError:
            ch.error(f"Bad --date '{on_date}'.", "Expected YYYY-MM-DD.")
            raise typer.Exit(1) from None
    else:
        day = date.today().isoformat()

    from navig.spaces import journal

    path = _tracker_path(space)
    try:
        entry, written = journal.append_entry(path, day, text)
    except OSError as exc:
        ch.error("Could not write the journal entry.", str(exc))
        raise typer.Exit(1) from exc

    if written:
        ch.success(f"{day} — three lines written.")
    else:
        ch.dim(f"{day} — already recorded, nothing added.")
    ch.dim(f"{entry}")


# ── Command: review ───────────────────────────────────────────────────────────

@habit_app.command("review")
def habit_review(
    days: int = typer.Option(7, "--days", "-d", help="Length of the window, ending on --ending"),
    ending: str | None = typer.Option(
        None, "--ending", help="Last day of the window (YYYY-MM-DD); defaults to today"
    ),
    write: bool = typer.Option(
        False, "--write", help="Append the block into that day's journal file"
    ),
    space: str | None = typer.Option(
        None, "--space", help="Space directory (or habits.csv path) to read"
    ),
) -> None:
    """Build the weekly review from the tracker instead of from memory.

    Prints the block: the three that decide the day, day by day; whatever else was
    logged; average bedtime and score; the setbacks already written in the
    journal; and the days that have no row at all.

      navig habit review                       # the last seven days
      navig habit review --ending 2026-08-17   # the day-7 checkpoint
      navig habit review --write               # straight into the journal

    "One decision for next week" is left blank on purpose. Reading the week is
    what a machine can do; deciding is not.
    """
    if ending:
        try:
            end = date.fromisoformat(ending)
        except ValueError:
            ch.error(f"Bad --ending '{ending}'.", "Expected YYYY-MM-DD.")
            raise typer.Exit(1) from None
    else:
        end = date.today()

    if days < 1:
        ch.error("--days must be at least 1.")
        raise typer.Exit(1)

    from navig.spaces import journal, weekly_review

    path = _tracker_path(space)
    start = end - timedelta(days=days - 1)
    block = weekly_review.build(path, start, end)

    # Printed raw, not through a Rich table: this is markdown meant to be pasted
    # into a journal file, and a rendered table cannot be pasted back.
    print(block)

    if write:
        try:
            entry = journal.append_block(path, end.isoformat(), block)
        except OSError as exc:
            ch.error("Could not write the review.", str(exc))
            raise typer.Exit(1) from exc
        ch.success(f"Review written into {entry.name}.")
        ch.dim(f"{entry}")
    else:
        ch.dim("Add --write to append this into the journal.")


# ── Command: today ────────────────────────────────────────────────────────────

@habit_app.command("today")
def habit_today(
    on_date: str | None = typer.Option(None, "--date", help="Inspect another day"),
    space: str | None = typer.Option(
        None, "--space", help="Space directory (or habits.csv path) to read"
    ),
) -> None:
    """Show what is logged for today and which non-negotiables are still open."""
    day = on_date or date.today().isoformat()
    path = _tracker_path(space)
    rows = [r for r in _read_tracker(path) if r["date"] == day]

    if not rows:
        ch.console.print(f"[dim]Nothing logged for {day}.[/dim]")
    else:
        table = ch.create_table(
            title=f"Tracker — {day}",
            columns=[
                {"name": "Habit", "style": "cyan"},
                {"name": "", "justify": "center"},
                {"name": "Value"},
                {"name": "Note", "style": "dim"},
            ],
        )
        for r in sorted(rows, key=lambda x: x["habit"]):
            mark = "[green]✓[/green]" if _is_done(r["completed"]) else "[dim]·[/dim]"
            table.add_row(r["habit"], mark, r["completed"] or "—", r["notes"])
        ch.print_table(table)

    # Name the three, in words, with their state — never a bare "2/3". A count
    # says a day was incomplete without saying what to do about it.
    from navig.telegram.habit_actions import floor_line, word

    logged = {r["habit"]: r["completed"] for r in rows}
    ch.console.print(f"\n[bold]The 3 that decide the day[/bold]\n{floor_line(logged)}")

    missing = [word(h) for h in NON_NEGOTIABLES if not _is_done(logged.get(h, ""))]
    if missing:
        ch.warning("Still open: " + ", ".join(missing))
    else:
        ch.success("All three done. The day counts.")


# ── Command: checkin ──────────────────────────────────────────────────────────

@habit_app.command("checkin")
def habit_checkin(
    send: bool = typer.Option(False, "--send", help="Send the card to Telegram"),
    morning: bool = typer.Option(
        False, "--morning", help="Morning framing — mark Wake when it actually happens"
    ),
    close: bool = typer.Option(
        False, "--close", help="Close the day: strip the buttons, post the verdict"
    ),
    chat_id: int | None = typer.Option(None, "--chat-id", help="Telegram chat to send to"),
    on_date: str | None = typer.Option(None, "--date", help="Card for another day"),
    space: str | None = typer.Option(
        None, "--space", help="Space directory (or habits.csv path) the card tracks"
    ),
) -> None:
    """Show — or send to Telegram — a one-tap check-in card.

    The check-in only survives if it costs seconds. Typing a command on a phone
    does not qualify, so the card carries a button per habit and each tap edits it
    in place.

      navig habit checkin                     # preview it here
      navig habit checkin --send              # evening card
      navig habit checkin --send --morning    # morning card
      navig habit checkin --close             # finalise the day

    Waking up is the one thing that can only be marked as it happens — by 22:15 it
    is a memory. Hence a morning card. And a day nobody closes stays ambiguous
    forever, so `--close` runs on a schedule at the end of the day and settles it.

    Deliver on a schedule by pointing cron jobs at this command:
      navig habit checkin --send --morning --space <space>   # 08:00
      navig habit checkin --send --space <space>             # 22:15
      navig habit checkin --close --space <space>            # 23:55
    """
    from navig.telegram import habit_actions

    day = (on_date or date.today().isoformat()).strip()
    try:
        day = date.fromisoformat(day).isoformat()
    except ValueError:
        ch.error(f"Bad --date '{day}'.", "Expected YYYY-MM-DD.")
        raise typer.Exit(1) from None

    path = _tracker_path(space)

    # The SENDING paths only. This process is not the gateway — cron shells out
    # to `navig habit checkin --send`, which builds the card and calls
    # sendMessage directly — so the extension gate has to be applied here too or
    # "off" would still write to the operator every morning.
    #
    # Preview (no --send) stays allowed: reading your own tracker in your own
    # terminal is not a bot surface, and blocking it would be the switch
    # overreaching.
    if send or close:
        from navig.gateway.channels.telegram_extensions import is_enabled

        if not is_enabled("habits"):
            ch.warning(
                "Nothing sent — the Habits extension is off.",
                "Turn it on with /extensions in Telegram, or: "
                "navig telegram extensions enable habits",
            )
            return

    if close:
        _close_day(path, day, chat_id)
        return

    text, keyboard = habit_actions.build_card(path, day, morning=morning)

    if not send:
        plain = text.replace("<b>", "").replace("</b>", "")
        plain = plain.replace("<i>", "").replace("</i>", "")
        ch.console.print(plain)
        ch.console.print("")
        for row in keyboard["inline_keyboard"]:
            ch.console.print("  " + "   ".join(b["text"] for b in row))
        ch.dim(f"\n{path}")
        ch.dim("Send it with: navig habit checkin --send")
        return

    target = chat_id or _resolve_default_chat_id()
    if not target:
        ch.error("No Telegram chat id.", "Set telegram.allowed_users or pass --chat-id.")
        raise typer.Exit(1)

    from navig.commands.telegram import _api_call, _load_telegram_token

    try:
        result = _api_call(
            _load_telegram_token(),
            "sendMessage",
            {
                "chat_id": target,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": keyboard,
            },
        )
    except Exception as exc:  # noqa: BLE001
        ch.error("Could not send the check-in card.", str(exc))
        raise typer.Exit(1) from exc

    # The card is sent from here but its buttons are handled by the gateway — a
    # different process whose active space is very likely something else. Record
    # which tracker this chat's card writes into so the taps land in the right file,
    # plus the message id so the day can later be closed by editing THIS card
    # instead of posting a second one.
    habit_tracker.remember_target(
        int(target), path, message_id=(result.get("result") or {}).get("message_id"), day=day
    )

    ch.success(f"{'Morning' if morning else 'Check-in'} card sent to {target}.")
    ch.dim(f"{path}")


def _close_day(path: Path, day: str, chat_id: int | None) -> None:
    """Finalise the day: strip the card's buttons and leave the verdict.

    Deliberately records nothing. A day the owner never closed is a day with
    missing marks, and missing already counts as not-done everywhere else —
    writing `no` on their behalf would be the system inventing data about a life
    it did not witness.
    """
    from navig.telegram import habit_actions

    target = chat_id or _resolve_default_chat_id()
    if not target:
        ch.error("No Telegram chat id.", "Set telegram.allowed_users or pass --chat-id.")
        raise typer.Exit(1)
    target = int(target)

    if habit_tracker.is_day_closed(target, day):
        ch.dim(f"{day} already closed — nothing to do.")
        return

    text = habit_actions.build_closing_text(path, day)
    message_id, card_day = habit_tracker.last_card(target)

    from navig.commands.telegram import _api_call, _load_telegram_token

    token = _load_telegram_token()
    edited = False
    if message_id and card_day == day:
        try:
            _api_call(
                token,
                "editMessageText",
                {
                    "chat_id": target,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": []},
                },
            )
            edited = True
        except Exception as exc:  # noqa: BLE001
            # Editing fails when the card was deleted or is too old to edit. That is
            # not a reason to leave the day unsettled — fall through and post it.
            ch.dim(f"Could not edit the card ({exc}); sending the verdict instead.")

    if not edited:
        try:
            _api_call(token, "sendMessage", {"chat_id": target, "text": text, "parse_mode": "HTML"})
        except Exception as exc:  # noqa: BLE001
            ch.error("Could not close the day.", str(exc))
            raise typer.Exit(1) from exc

    habit_tracker.mark_day_closed(target, day)
    ch.success(f"{day} closed.")


# ── Command: streak ───────────────────────────────────────────────────────────

@habit_app.command("spark")
def habit_spark(
    send: bool = typer.Option(False, "--send", help="Send it to Telegram"),
    chat_id: int | None = typer.Option(None, "--chat-id", help="Telegram chat to send to"),
    category: str | None = typer.Option(
        None, "--category", "-c", help="Force a category instead of reading the day"
    ),
    space: str | None = typer.Option(
        None, "--space", help="Space directory the tracker and sparks.txt live in"
    ),
) -> None:
    """Send one short line — chosen from the day, not at random.

    The only message of the day that asks for nothing back. A notification with a
    task attached is a debt; a day made of debts is one you start avoiding.

    Selection reads the tracker first: nothing logged yesterday picks a line about
    starting again, an unmarked walk in the afternoon picks one about getting out,
    and a day that is going fine gets an ambient one. Nothing repeats inside a
    fortnight.

      navig habit spark                    # preview here
      navig habit spark --send             # send it
      navig habit spark -c body --send     # force a category

    Lines live in `sparks.txt` beside habits.csv — edit that file, not navig.
    """
    from navig.spaces import sparks

    path = _tracker_path(space)
    pool = sparks.load(space)

    if category:
        if category not in pool:
            ch.error(f"No category '{category}'.", "Have: " + ", ".join(sorted(pool)))
            raise typer.Exit(1)

    now = datetime.now()
    today, yesterday = now.date(), now.date() - timedelta(days=1)
    rows = _read_tracker(path)

    def done_on(d: date) -> set[str]:
        iso = d.isoformat()
        return {r["habit"] for r in rows if r["date"] == iso and _is_done(r["completed"])}

    target = chat_id or _resolve_default_chat_id()
    recent = habit_tracker.recent_sparks(int(target)) if target else []

    if category:
        fresh = [ln for ln in pool[category] if sparks.key_of(ln) not in recent]
        import random  # noqa: PLC0415

        line, chosen = random.choice(fresh or list(pool[category])), category
    else:
        chosen, line = sparks.pick(
            pool,
            day=today,
            today_done=done_on(today),
            yesterday_done=done_on(yesterday),
            hour=now.hour,
            recent=recent,
        )

    if not line:
        ch.console.print("[dim]The spark pool is empty.[/dim]")
        return

    if not send:
        ch.console.print(f"\n[dim]{chosen or 'ambient'}[/dim]\n{line}\n")
        ch.dim("Send it with: navig habit spark --send")
        return

    if not target:
        ch.error("No Telegram chat id.", "Set telegram.allowed_users or pass --chat-id.")
        raise typer.Exit(1)

    from navig.commands.telegram import _api_call, _load_telegram_token

    try:
        # No parse_mode key at all: Telegram rejects an explicit null with a 400,
        # and a spark is plain text by design — the owner writes these lines, and
        # an apostrophe or a stray < must never cost him the message.
        _api_call(
            _load_telegram_token(),
            "sendMessage",
            {"chat_id": int(target), "text": f"💬 {line}"},
        )
    except Exception as exc:  # noqa: BLE001
        ch.error("Could not send the spark.", str(exc))
        raise typer.Exit(1) from exc

    habit_tracker.remember_spark(int(target), sparks.key_of(line))
    ch.success(f"Sent ({chosen or 'ambient'}).")
    ch.dim(line)


@habit_app.command("streak")
def habit_streak(
    habit_key: str | None = typer.Argument(None, help="Only this label"),
    space: str | None = typer.Option(
        None, "--space", help="Space directory (or habits.csv path) to read"
    ),
) -> None:
    """Show consecutive-day streaks, non-negotiables first."""
    path = _tracker_path(space)
    rows = _read_tracker(path)
    if not rows:
        ch.console.print(f"[dim]No tracker data in {path}.[/dim]")
        ch.dim("Log the first row: navig habit log wake")
        return

    today = date.today()
    labels = [habit_key] if habit_key else sorted({r["habit"] for r in rows})
    ordered = [h for h in NON_NEGOTIABLES if h in labels]
    ordered += [h for h in labels if h not in ordered]

    table = ch.create_table(
        title="Streaks",
        columns=[
            {"name": "Habit", "style": "cyan"},
            {"name": "Streak", "justify": "right"},
            {"name": "Done", "justify": "right", "style": "dim"},
            {"name": "", "style": "dim"},
        ],
    )
    for label in ordered:
        streak = _streak_for(rows, label, today)
        total = sum(1 for r in rows if r["habit"] == label and _is_done(r["completed"]))
        flag = "decides the day" if label in NON_NEGOTIABLES else ""
        table.add_row(label, f"{streak}d", str(total), flag)
    ch.print_table(table)
    ch.dim(f"{path}")


# ── Command: pause / resume ───────────────────────────────────────────────────

def _set_reminders(habit_key: str | None, *, enabled: bool) -> None:
    from navig.telegram.habit_actions import word

    changed = habit_store.set_jobs_enabled(habit_key, enabled=enabled)
    if not changed:
        if habit_key:
            ch.error(f"No habit reminder called '{habit_key}'.")
            ch.dim("Run 'navig habit list' to see what is configured.")
            raise typer.Exit(1)
        ch.console.print("[dim]No habit reminders are configured.[/dim]")
        return

    names = ", ".join(sorted(word(k) for k in changed))
    if enabled:
        ch.success(f"Reminders back on: {names}")
        ch.dim("Next card arrives at its normal time.")
    else:
        ch.success(f"Reminders paused: {names}")
        ch.dim("Nothing is deleted — your tracker and streaks are untouched.")
        ch.dim("Turn them back on with: navig habit resume")


@habit_app.command("pause")
def habit_pause(
    habit_key: str | None = typer.Argument(
        None, help="One habit (e.g. out); omit to pause every habit reminder"
    ),
) -> None:
    """Stop habit reminders without losing anything.

    Pausing keeps the schedule and every logged row — resuming picks up exactly
    where you left off. Use it for a week away, not as a way to quit.

      navig habit pause          # all of them
      navig habit pause out      # just the walk reminder
    """
    _set_reminders(habit_key, enabled=False)


@habit_app.command("resume")
def habit_resume(
    habit_key: str | None = typer.Argument(
        None, help="One habit (e.g. out); omit to resume every habit reminder"
    ),
) -> None:
    """Turn habit reminders back on after a pause."""
    _set_reminders(habit_key, enabled=True)


# ── Command: stats ────────────────────────────────────────────────────────────

_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@habit_app.command("stats")
def habit_stats(
    days: int = typer.Option(14, "--days", "-d", help="How many recent days to show day by day"),
    all_days: bool = typer.Option(False, "--all", help="Show every day since the first row"),
    space: str | None = typer.Option(
        None, "--space", help="Space directory (or habits.csv path) to read"
    ),
) -> None:
    """Show the whole cycle: streaks, complete days, and which days you lose.

    Counts alone say "you missed some days" and tell you nothing to act on. The
    day-of-week breakdown is the part worth reading — it turns "bad week" into a
    specific day with a specific fix.
    """
    from navig.telegram.habit_actions import MARK_DONE, MARK_OPEN, word

    path = _tracker_path(space)
    rows = _read_tracker(path)
    if not rows:
        ch.console.print(f"[dim]No tracker data in {path}.[/dim]")
        ch.dim("Log the first row: navig habit log wake")
        return

    today = date.today()
    s = habit_tracker.summarize(rows, today)

    ch.console.print(
        f"\n[bold]Progress[/bold] — day {s['days']} "
        f"({s['start'].strftime('%d %b')} → {s['end'].strftime('%d %b')})\n"
    )

    table = ch.create_table(
        title="The 3 that decide the day",
        columns=[
            {"name": "", "style": "cyan"},
            {"name": "Done", "justify": "right"},
            {"name": "Missed", "justify": "right", "style": "dim"},
            {"name": "Now", "justify": "right"},
            {"name": "Best", "justify": "right", "style": "dim"},
        ],
    )
    for label in NON_NEGOTIABLES:
        st = s["habits"][label]
        table.add_row(
            word(label),
            f"{st['done']}/{s['days']}",
            str(st["missed"]),
            f"{st['streak']}d",
            f"{st['best']}d",
        )
    ch.print_table(table)

    pct = round(100 * s["complete"] / s["days"]) if s["days"] else 0
    ch.console.print(
        f"\n[bold]Complete days: {s['complete']} of {s['days']}[/bold] ({pct}%) "
        "[dim]— all three done[/dim]"
    )

    # Day of week: where the days are actually lost.
    ch.console.print("\n[bold]By day of week[/bold]  [dim](complete days)[/dim]")
    for i, name in enumerate(_WEEKDAY_NAMES):
        bucket = s["by_weekday"].get(i, {"days": 0, "complete": 0})
        total = bucket["days"]
        if not total:
            continue
        hit = bucket["complete"]
        bar = "█" * hit + "·" * (total - hit)
        colour = "green" if hit == total else ("yellow" if hit else "red")
        ch.console.print(f"  {name}  [{colour}]{bar}[/{colour}]  {hit}/{total}")

    # Day by day — blank days included, because a missing day is the thing to see.
    start = s["start"] if all_days else max(s["start"], today - timedelta(days=days - 1))
    ch.console.print(f"\n[bold]Day by day[/bold]  [dim]({word('wake')} · {word('out')} · {word('ship')})[/dim]")
    for day, marks in habit_tracker.day_rows(rows, start, today):
        cells = "  ".join(MARK_DONE if marks[h] else MARK_OPEN for h in NON_NEGOTIABLES)
        hit = sum(marks.values())
        note = "[green]complete[/green]" if hit == 3 else ("[dim]—[/dim]" if hit else "[red]blank[/red]")
        ch.console.print(f"  {day.strftime('%a %d %b')}   {cells}   {note}")

    ch.dim(f"\n{path}")
