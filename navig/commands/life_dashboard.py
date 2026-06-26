"""
navig day / navig life — Unified life + ops dashboard.

Shows a single-screen overview of:
  - Server fleet status  (how many hosts, any unreachable)
  - Active reminders     (from RuntimeStore)
  - Today's habits       (habit: cron jobs and their next fire times)
  - Next scheduled jobs  (upcoming 3 cron jobs)
  - Current space        (active space + completion %)

Designed to be fast: no blocking I/O on the main path; all heavy queries
are wrapped in try/except so a misconfigured env never crashes the view.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import typer
from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from navig import console_helper as ch
from navig.spaces.health import BUILTIN_HABITS, get_habit_template

life_dashboard_app = typer.Typer(
    name="life",
    help="Unified life + ops dashboard (alias: navig day)",
    invoke_without_command=True,
    no_args_is_help=False,
)

_HABIT_NAME_PREFIX = "habit:"


# ── Data fetchers (all silent-fail) ───────────────────────────────────────────

def _get_fleet_summary() -> dict:
    """Return {hosts: list[str], reachable: int}. Never raises."""
    try:
        from navig.config import get_config_manager
        cm = get_config_manager()
        hosts = cm.list_hosts()
        return {"hosts": hosts, "count": len(hosts)}
    except Exception:
        return {"hosts": [], "count": 0}


def _get_pending_reminders(user_id: int = 0) -> list[dict]:
    """Return upcoming (not-yet-due) reminders from RuntimeStore. Never raises."""
    try:
        from navig.store.runtime import get_runtime_store
        store = get_runtime_store()
        if user_id:
            return store.get_user_reminders(user_id)
        # Return due reminders + upcoming (union)
        due = store.get_due_reminders()
        return due
    except Exception:
        return []


def _get_habit_jobs() -> list[dict]:
    """Return raw habit job dicts from cron_jobs.json. Never raises."""
    try:
        p = Path.home() / ".navig" / "daemon" / "cron_jobs.json"
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        return [j for j in data.get("jobs", []) if j.get("name", "").startswith(_HABIT_NAME_PREFIX)]
    except Exception:
        return []


def _get_all_cron_jobs() -> list[dict]:
    """Return all cron jobs sorted by next_run. Never raises."""
    try:
        p = Path.home() / ".navig" / "daemon" / "cron_jobs.json"
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        jobs = [j for j in data.get("jobs", []) if j.get("enabled", True)]
        return sorted(jobs, key=lambda x: x.get("next_run") or "")
    except Exception:
        return []


def _get_active_space() -> str:
    """Return name of the active space. Never raises."""
    try:
        from navig.config import get_config_manager
        cm = get_config_manager()
        return cm.get("spaces.active", default="personal")
    except Exception:
        return "personal"


def _get_space_completion() -> float | None:
    """Return completion % of the active space, or None if not available."""
    try:
        from navig.spaces.progress import collect_spaces_progress
        rows = collect_spaces_progress()
        if rows:
            active = _get_active_space()
            for sp in rows:
                if sp.name == active:
                    return sp.completion_pct
            return rows[0].completion_pct
    except Exception:
        pass
    return None


# ── Panel builders ─────────────────────────────────────────────────────────────

def _fleet_panel(fleet: dict) -> Panel:
    hosts = fleet["hosts"]
    count = fleet["count"]
    if count == 0:
        body = Text("No hosts configured.\nRun: navig host add", style="dim")
    else:
        lines = Text()
        for h in hosts[:8]:
            lines.append(f"  • {h}\n", style="cyan")
        if count > 8:
            lines.append(f"  … and {count - 8} more\n", style="dim")
        body = lines

    title = f"[bold]🖥  Fleet[/bold]  ({count} host{'s' if count != 1 else ''})"
    return Panel(body, title=title, border_style="blue", padding=(0, 1))


def _reminders_panel(reminders: list[dict]) -> Panel:
    if not reminders:
        body = Text("No pending reminders.\n/remindme in 30 minutes check logs", style="dim")
    else:
        t = Table.grid(padding=(0, 1))
        t.add_column(style="yellow", no_wrap=True)
        t.add_column()
        for r in reminders[:6]:
            msg = str(r.get("message", ""))[:50]
            when_raw = r.get("remind_at", "")
            try:
                when = datetime.fromisoformat(str(when_raw)).strftime("%H:%M")
            except Exception:
                when = "—"
            t.add_row(f"⏰ {when}", msg)
        if len(reminders) > 6:
            t.add_row("[dim]…[/dim]", f"[dim]+{len(reminders) - 6} more[/dim]")
        body = t

    return Panel(body, title="[bold]⏰  Reminders[/bold]", border_style="yellow", padding=(0, 1))


def _habits_panel(habit_jobs: list[dict]) -> Panel:
    if not habit_jobs:
        body = Text("No habits set up yet.\nRun: navig habit add workout", style="dim")
    else:
        t = Table.grid(padding=(0, 1))
        t.add_column(no_wrap=True)
        t.add_column(style="dim")
        for j in habit_jobs[:6]:
            key = j.get("name", "").removeprefix(_HABIT_NAME_PREFIX)
            tmpl = get_habit_template(key)
            emoji = tmpl.emoji if tmpl else "📌"
            next_raw = j.get("next_run")
            try:
                next_str = datetime.fromisoformat(str(next_raw)).strftime("%a %H:%M")
            except Exception:
                next_str = "—"
            enabled = j.get("enabled", True)
            status = "" if enabled else " [dim]off[/dim]"
            t.add_row(f"{emoji} {key}{status}", next_str)
        body = t

    return Panel(body, title="[bold]💪  Habits[/bold]", border_style="green", padding=(0, 1))


def _schedule_panel(all_jobs: list[dict]) -> Panel:
    upcoming = [j for j in all_jobs if not j.get("name", "").startswith(_HABIT_NAME_PREFIX)][:3]
    if not upcoming:
        body = Text("No jobs scheduled.\nRun: navig cron add", style="dim")
    else:
        t = Table.grid(padding=(0, 1))
        t.add_column(style="cyan", no_wrap=True)
        t.add_column(style="dim")
        for j in upcoming:
            name = j.get("name", "job")[:30]
            next_raw = j.get("next_run")
            try:
                next_str = datetime.fromisoformat(str(next_raw)).strftime("%a %H:%M")
            except Exception:
                next_str = "—"
            t.add_row(f"• {name}", next_str)
        body = t

    return Panel(body, title="[bold]📅  Next Jobs[/bold]", border_style="magenta", padding=(0, 1))


def _space_panel(space_name: str, completion: float | None) -> Panel:
    pct_str = f" — {completion:.0f}% done" if completion is not None else ""
    body = Text(f"Space: {space_name}{pct_str}\n", style="bold cyan")
    body.append("navig space use <name>  ·  navig spaces list", style="dim")
    return Panel(body, title="[bold]🗂  Context[/bold]", border_style="cyan", padding=(0, 1))


# ── Main render ───────────────────────────────────────────────────────────────

def build_dashboard(user_id: int = 0) -> dict:
    """Aggregate the life surfaces (reminders, habits, space progress, fleet)
    into a plain-text summary for the daily briefing. Never raises — every
    underlying helper degrades to an empty result.

    Returns ``{"text", "reminders", "habits", "space", "completion", "hosts"}``.
    """
    reminders = _get_pending_reminders(user_id)
    habits = _get_habit_jobs()
    space = _get_active_space()
    completion = _get_space_completion()
    fleet = _get_fleet_summary()
    host_count = int(fleet.get("count", 0) or 0)

    lines: list[str] = []
    lines.append(
        f"{len(reminders)} pending reminder{'s' if len(reminders) != 1 else ''}"
        if reminders
        else "No pending reminders"
    )
    if habits:
        lines.append(f"{len(habits)} active habit{'s' if len(habits) != 1 else ''}")
    if completion is not None:
        lines.append(f"Space '{space}' — {completion:.0f}% complete")
    else:
        lines.append(f"Active space: {space}")
    if host_count:
        lines.append(f"{host_count} host{'s' if host_count != 1 else ''} in fleet")

    return {
        "text": "\n".join(lines),
        "reminders": len(reminders),
        "habits": len(habits),
        "space": space,
        "completion": completion,
        "hosts": host_count,
    }


def run_life_dashboard(user_id: int = 0) -> None:
    """Render the unified life + ops dashboard as a snapshot."""
    console = ch.get_console()

    fleet = _get_fleet_summary()
    reminders = _get_pending_reminders(user_id=user_id)
    habit_jobs = _get_habit_jobs()
    all_jobs = _get_all_cron_jobs()
    space_name = _get_active_space()
    completion = _get_space_completion()

    now_str = datetime.now().strftime("%A, %d %B %Y  %H:%M")
    console.print(f"\n[bold white]NAVIG — Command Center[/bold white]  [dim]{now_str}[/dim]\n")

    top_row = Columns(
        [_fleet_panel(fleet), _reminders_panel(reminders)],
        equal=True,
        expand=True,
    )
    mid_row = Columns(
        [_habits_panel(habit_jobs), _schedule_panel(all_jobs)],
        equal=True,
        expand=True,
    )

    console.print(top_row)
    console.print(mid_row)
    console.print(_space_panel(space_name, completion))
    console.print()


# ── Typer commands ─────────────────────────────────────────────────────────────

@life_dashboard_app.callback(invoke_without_command=True)
def _life_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        run_life_dashboard()


@life_dashboard_app.command("show")
def life_show(
    user_id: int = typer.Option(0, "--user-id", help="Telegram user ID for reminder lookup"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Refresh every N seconds"),
    interval: int = typer.Option(30, "--interval", help="Refresh interval in seconds (with --watch)"),
) -> None:
    """Show the unified life + ops dashboard."""
    if watch:
        import time
        try:
            while True:
                run_life_dashboard(user_id=user_id)
                ch.print(f"[dim]Refreshing in {interval}s… Ctrl+C to exit[/dim]")
                time.sleep(interval)
        except KeyboardInterrupt:
            ch.print("\n[dim]Stopped.[/dim]")
    else:
        run_life_dashboard(user_id=user_id)
