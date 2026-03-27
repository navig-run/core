"""
NAVIG Blackbox Commands

CLI interface for the flight recorder / crash bundler subsystem.
All operations use the navig.blackbox module via lazy imports.
"""

import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import typer

from navig.lazy_loader import lazy_import

_ch = lazy_import("navig.console_helper")

blackbox_app = typer.Typer(
    name="blackbox",
    help="Blackbox flight recorder — events, crash reports, and .navbox bundles",
)
bundle_cmd = typer.Typer(
    name="bundle", help="Create, inspect, and export .navbox bundles"
)
blackbox_app.add_typer(bundle_cmd, name="bundle")


def _recorder():
    from navig.blackbox.recorder import get_recorder

    return get_recorder()


def _parse_hours(since_str: str) -> float:
    """Parse '24h', '30m', '7d' → hours."""
    s = since_str.strip().lower()
    if s.endswith("d"):
        return float(s[:-1]) * 24
    if s.endswith("m"):
        return float(s[:-1]) / 60
    if s.endswith("h"):
        return float(s[:-1])
    return float(s)


# ── Status ────────────────────────────────────────────────────────────────────


@blackbox_app.command("status")
def blackbox_status():
    """Show blackbox recorder status and storage statistics."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    from navig.blackbox.seal import is_sealed
    from navig.platform.paths import blackbox_dir

    con = Console()
    rec = _recorder()
    bdir = blackbox_dir()

    t = Table(expand=True, border_style="dim", show_header=False)
    t.add_column("Field", style="bold", width=22)
    t.add_column("Value", ratio=1)

    t.add_row(
        "Status",
        "[green]Enabled[/green]" if rec.is_enabled() else "[red]Disabled[/red]",
    )
    t.add_row("Directory", str(bdir))
    t.add_row("Event count", str(rec.event_count()))
    t.add_row("Log size", f"{rec.file_size_mb():.2f} MB")

    ts = rec.last_event_ts()
    t.add_row("Last event", ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "—")
    t.add_row("Sealed", "[yellow]Yes[/yellow]" if is_sealed() else "[dim]No[/dim]")

    # Count crash files
    crash_dir = bdir / "crashes"
    crash_count = len(list(crash_dir.glob("crash-*.json"))) if crash_dir.exists() else 0
    t.add_row("Crash reports", str(crash_count))

    con.print(Panel(t, title="Blackbox Status", border_style="dim"))


# ── Enable / Disable ──────────────────────────────────────────────────────────


@blackbox_app.command("enable")
def blackbox_enable():
    """Enable event recording."""
    _recorder().enable()
    _ch.success("Blackbox recording enabled.")


@blackbox_app.command("disable")
def blackbox_disable():
    """Pause event recording (events will not be written)."""
    _recorder().disable()
    _ch.warning("Blackbox recording disabled.")


# ── Record / Capture ──────────────────────────────────────────────────────────


@blackbox_app.command("record")
def blackbox_record(
    event_type: str = typer.Argument(
        ..., help="Event type: crash, error, warning, command, session, system, output"
    ),
    message: Optional[str] = typer.Option(
        None, "--message", "-m", help="Event message / payload text"
    ),
    tag: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Tags (repeatable)"
    ),
    source: str = typer.Option("cli", "--source", help="Event source identifier"),
    stdin: bool = typer.Option(False, "--stdin", help="Read message from stdin"),
):
    """Manually record an event into the blackbox."""
    from navig.blackbox.types import EventType

    try:
        et = EventType(event_type.upper())
    except ValueError as _exc:
        valid = [e.value for e in EventType]
        _ch.error(f"Unknown event type '{event_type}'. Valid: {valid}")
        raise typer.Exit(1) from _exc

    if stdin:
        msg = sys.stdin.read().strip()
    else:
        msg = message or typer.prompt("Event message")

    rec = _recorder()
    event = rec.record(et, {"message": msg}, tags=tag or [], source=source)
    if event:
        _ch.success(f"Recorded [{et.value}]  [dim]{event.id}[/dim]")
    else:
        _ch.warning("Recording skipped (blackbox disabled or error).")


@blackbox_app.command("capture")
def blackbox_capture(
    last: str = typer.Option(
        "30m", "--last", help="Capture events from last: 30m, 1h, 24h"
    ),
    limit: int = typer.Option(200, "--limit", "-n", help="Maximum events to capture"),
    output: Optional[str] = typer.Option(
        None, "-o", "--output", help="Output .navbox file path"
    ),
):
    """Capture a session snapshot to a .navbox bundle."""
    from navig.blackbox.bundle import create_bundle, write_bundle

    hours = _parse_hours(last)
    bundle = create_bundle(since_hours=hours)

    if bundle.event_count() == 0 and bundle.crash_count() == 0:
        _ch.info("No events in the capture window.")
        return

    out_path = write_bundle(bundle, output)
    _ch.success(
        f"Captured {bundle.event_count()} event(s), {bundle.crash_count()} crash(es) → "
        f"[bold]{out_path}[/bold]"
    )


# ── Timeline ──────────────────────────────────────────────────────────────────


@blackbox_app.command("timeline")
def blackbox_timeline(
    limit: int = typer.Option(50, "--limit", "-n", help="Number of events to show"),
    since: Optional[str] = typer.Option(None, "--since", help="Since: 30m, 1h, 24h"),
    event_type: Optional[str] = typer.Option(
        None, "--type", "-t", help="Filter by event type"
    ),
):
    """Render a Rich timeline table of recorded events."""
    from navig.blackbox.recorder import get_recorder
    from navig.blackbox.timeline import render_timeline
    from navig.blackbox.types import EventType

    et_filter = None
    if event_type:
        try:
            et_filter = EventType(event_type.upper())
        except ValueError as _exc:
            _ch.error(f"Unknown event type '{event_type}'")
            raise typer.Exit(1) from _exc

    since_dt: Optional[datetime] = None
    if since:
        hours = _parse_hours(since)
        since_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            hours=hours
        )

    events = get_recorder().read_events(
        since=since_dt, limit=limit, event_type=et_filter
    )
    if not events:
        _ch.info("No events found.")
        return

    render_timeline(events, limit=limit)


# ── Seal ──────────────────────────────────────────────────────────────────────


@blackbox_app.command("seal")
def blackbox_seal(
    unseal: bool = typer.Option(
        False, "--unseal", help="Remove seal instead of applying"
    ),
):
    """Seal the blackbox (prevents recording new events until unsealed)."""
    from navig.blackbox.bundle import create_bundle
    from navig.blackbox.seal import is_sealed, seal_bundle
    from navig.blackbox.seal import unseal as _unseal

    if unseal:
        ok = _unseal()
        if ok:
            _ch.success("Blackbox unsealed — recording resumed.")
        else:
            _ch.info("Already unsealed.")
        return

    if is_sealed():
        _ch.warning("Blackbox already sealed.")
        return

    bundle = create_bundle(since_hours=24)
    seal_bundle(bundle)
    _ch.success("Blackbox sealed.")


# ── Bundle sub-commands ────────────────────────────────────────────────────────


@bundle_cmd.command("create")
def bundle_create(
    since: str = typer.Option("24h", "--since", help="Events from last: 24h, 7d, …"),
    output: Optional[str] = typer.Option(
        None, "-o", "--output", help="Output .navbox path"
    ),
    encrypted: bool = typer.Option(
        False, "--encrypted", help="Encrypt the bundle using the vault master key"
    ),
):
    """Create a .navbox bundle from recent events and logs."""
    from navig.blackbox.bundle import create_bundle, write_bundle
    from navig.blackbox.export import export_bundle

    hours = _parse_hours(since)
    bundle = create_bundle(since_hours=hours)

    if bundle.event_count() == 0 and bundle.crash_count() == 0:
        _ch.info("No events to bundle.")
        return

    if encrypted:
        out_path = export_bundle(bundle, output=output, encrypted=True)
    else:
        out_path = write_bundle(bundle, output=output)

    _ch.success(
        f"Bundle ready: [bold]{out_path}[/bold]  "
        f"({bundle.event_count()} events, {bundle.crash_count()} crashes)"
    )


@bundle_cmd.command("inspect")
def bundle_inspect(
    file: str = typer.Argument(..., help="Path to a .navbox file"),
):
    """Inspect the contents of a .navbox bundle."""
    import pathlib

    from rich.console import Console
    from rich.panel import Panel

    from navig.blackbox.bundle import inspect_bundle
    from navig.blackbox.timeline import render_timeline

    path = pathlib.Path(file).expanduser()
    if not path.exists():
        _ch.error(f"File not found: {path}")
        raise typer.Exit(1)

    con = Console()
    bundle = inspect_bundle(path)

    con.print(
        Panel(
            f"[bold]ID:[/bold] {bundle.id}\n"
            f"[bold]Created:[/bold] {bundle.created_at}\n"
            f"[bold]NAVIG version:[/bold] {bundle.navig_version}\n"
            f"[bold]Events:[/bold] {bundle.event_count()}\n"
            f"[bold]Crashes:[/bold] {bundle.crash_count()}\n"
            f"[bold]Log tails:[/bold] {len(bundle.log_tails)}\n"
            f"[bold]Sealed:[/bold] {'Yes' if bundle.sealed else 'No'}\n"
            f"[bold]Hash:[/bold] [dim]{bundle.manifest_hash or '—'}[/dim]",
            title=f"Bundle: {path.name}",
            border_style="dim",
        )
    )

    if bundle.events:
        con.print("\n[bold]Events:[/bold]")
        render_timeline(bundle.events, limit=50, console=con)

    if bundle.crash_reports:
        con.print(
            f"\n[bold red]{len(bundle.crash_reports)} crash report(s):[/bold red]"
        )
        for cr in bundle.crash_reports:
            con.print(
                f"  [dim]{cr.timestamp}[/dim]  {cr.exception_type}: {cr.exception_msg}"
            )


@bundle_cmd.command("export")
def bundle_export(
    file: str = typer.Argument(..., help="Path to source .navbox bundle"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output path"),
    encrypted: bool = typer.Option(False, "--encrypted", help="Encrypt the export"),
):
    """Re-export an existing .navbox bundle, optionally with encryption."""
    import pathlib

    from navig.blackbox.bundle import inspect_bundle
    from navig.blackbox.export import export_bundle

    src = pathlib.Path(file).expanduser()
    if not src.exists():
        _ch.error(f"File not found: {src}")
        raise typer.Exit(1)

    bundle = inspect_bundle(src)
    out_path = export_bundle(bundle, output=output, encrypted=encrypted)
    _ch.success(f"Exported → [bold]{out_path}[/bold]")


# ── Crashes ────────────────────────────────────────────────────────────────────


@blackbox_app.command("crashes")
def blackbox_crashes(
    limit: int = typer.Option(
        10, "--limit", "-n", help="Number of crash reports to show"
    ),
):
    """List recent crash reports."""
    from rich.console import Console

    from navig.blackbox.crash import list_crashes

    con = Console()
    crashes = list_crashes()[:limit]

    if not crashes:
        _ch.info("No crash reports found.")
        return

    con.print(f"[bold]{len(crashes)} crash report(s):[/bold]\n")
    for cr in crashes:
        con.print(
            f"  [red]{cr.timestamp}[/red]  "
            f"[bold]{cr.exception_type}[/bold]: {cr.exception_msg}"
        )
        if cr.recent_commands:
            last_cmd = cr.recent_commands[-1]
            con.print(f"    [dim]Last command: {last_cmd}[/dim]")


# ── Tail ──────────────────────────────────────────────────────────────────────


@blackbox_app.command("tail")
def blackbox_tail(
    limit: int = typer.Option(
        50, "--limit", "-n", help="Number of most-recent events to show"
    ),
) -> None:
    """Show the most recent N events from the blackbox."""
    from navig.blackbox.timeline import render_timeline

    events = _recorder().tail(limit)
    if not events:
        _ch.info("No events recorded yet.")
        return
    render_timeline(events, limit=limit)


# ── Clear ─────────────────────────────────────────────────────────────────────


@blackbox_app.command("clear")
def blackbox_clear(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Delete all recorded blackbox events (irreversible)."""
    if not force:
        typer.confirm(
            "This will permanently delete all blackbox events. Proceed?", abort=True
        )
    _recorder().clear()
    _ch.success("Blackbox cleared — all events deleted.")
