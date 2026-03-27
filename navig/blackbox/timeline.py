"""NAVIG Blackbox Timeline — Rich table rendering of event streams."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .types import BlackboxEvent, EventType

__all__ = ["render_timeline", "format_event_summary"]

_TYPE_STYLES: dict[EventType, tuple[str, str]] = {
    EventType.COMMAND: ("blue", "CMD"),
    EventType.SESSION: ("cyan", "SES"),
    EventType.OUTPUT: ("dim", "OUT"),
    EventType.WARNING: ("yellow", "WRN"),
    EventType.ERROR: ("red", "ERR"),
    EventType.CRASH: ("bold red", "CRH"),
    EventType.SYSTEM: ("dim cyan", "SYS"),
}


def render_timeline(
    events: list[BlackboxEvent],
    limit: int = 50,
    console: Optional[Console] = None,
) -> None:
    """Render events as a Rich table to *console* (default: stdout).

    Parameters
    ----------
    events  : List of events (order preserved; newest-last convention).
    limit   : Max rows to display.
    console : Rich console to render to.  ``None`` → default stdout console.
    """
    if console is None:
        console = Console()

    display = events[:limit] if len(events) > limit else events

    if not display:
        console.print("[dim]No blackbox events recorded.[/dim]")
        return

    table = Table(
        title=f"NAVIG Blackbox — {len(display)} event(s)",
        show_header=True,
        header_style="bold",
        expand=True,
        border_style="dim",
    )
    table.add_column("Time", style="dim", no_wrap=True, width=20)
    table.add_column("Type", no_wrap=True, width=5)
    table.add_column("Source", style="dim", width=12)
    table.add_column("Summary", ratio=1)

    for event in display:
        style, abbr = _TYPE_STYLES.get(event.event_type, ("white", "???"))
        ts_str = _format_ts(event.timestamp)
        summary = format_event_summary(event)

        table.add_row(
            ts_str,
            Text(abbr, style=style),
            event.source,
            Text(
                summary,
                style=(
                    style
                    if event.event_type in (EventType.ERROR, EventType.CRASH)
                    else ""
                ),
            ),
        )

    console.print(table)


def format_event_summary(event: BlackboxEvent) -> str:
    """Produce a one-line human-readable summary of an event payload."""
    p = event.payload

    if event.event_type == EventType.COMMAND:
        cmd = p.get("command", "")
        args = p.get("args", "")
        return f"{cmd} {args}".strip() or "(unknown command)"

    if event.event_type == EventType.CRASH:
        exc = p.get("exception_type", "Exception")
        msg = p.get("exception_msg", "")
        return f"{exc}: {msg}"[:120] if msg else exc

    if event.event_type == EventType.ERROR:
        return str(p.get("message") or p.get("error") or p)[:120]

    if event.event_type == EventType.WARNING:
        return str(p.get("message") or p)[:120]

    if event.event_type == EventType.SESSION:
        action = p.get("action", "start")
        return f"Session {action}"

    if event.event_type == EventType.OUTPUT:
        lines = str(p.get("stdout") or p.get("output", "")).strip().splitlines()
        return lines[0][:120] if lines else ""

    if event.event_type == EventType.SYSTEM:
        return str(p.get("message") or p)[:120]

    return str(p)[:120]


def _format_ts(ts: datetime) -> str:
    """Format timestamp as 'HH:MM:SS' (today) or 'MM-DD HH:MM:SS' (older)."""
    now = datetime.now(timezone.utc)
    local = ts.astimezone()
    if local.date() == now.astimezone().date():
        return local.strftime("%H:%M:%S")
    return local.strftime("%m-%d %H:%M:%S")
