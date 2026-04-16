"""navig snapshot — file version history and NAVIG config snapshots.

Sub-commands
------------
versions  List all stored versions of a file for the current (or named) session.
diff      Show a unified diff between two stored file versions.
restore   Roll a file back to a specific prior version.
"""
from __future__ import annotations

import typer

from navig.console_helper import get_console

app = typer.Typer(help="File version history and NAVIG config snapshots", no_args_is_help=True)
console = get_console()


# ──────────────────────────────────────────────────────────────────────
# navig snapshot versions
# ──────────────────────────────────────────────────────────────────────

@app.command("versions")
def snapshot_versions(
    filepath: str = typer.Argument(..., help="File path to inspect"),
    session: str | None = typer.Option(None, "--session", "-s", help="Session ID (default: latest)"),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON"),
):
    """List all stored versions of a file."""
    import json as _json

    from navig import console_helper as ch
    from navig.file_history import get_file_history_store

    store = get_file_history_store()

    if not store._is_enabled():
        ch.warn(
            "File history is disabled. Set [bold]file_history.enabled: true[/bold] in your config to enable it."
        )
        raise typer.Exit(1)

    session_id = session or _latest_session()
    if not session_id:
        ch.error("No session ID provided and no recent session found.")
        raise typer.Exit(1)

    versions = store.list_versions(filepath, session_id)
    if not versions:
        ch.dim(f"No stored versions found for {filepath!r} in session {session_id!r}.")
        return

    if json_out:
        data = [
            {
                "turn_id": v.turn_id,
                "captured_at": v.captured_at.isoformat(),
                "size_bytes": v.size_bytes,
                "backup_path": str(v.backup_path),
            }
            for v in versions
        ]
        print(_json.dumps(data, indent=2))
        return

    from rich.table import Table

    tbl = Table(title=f"Versions of {filepath}", show_edge=False, box=None)
    tbl.add_column("#", style="dim", justify="right")
    tbl.add_column("Turn", style="cyan")
    tbl.add_column("Captured (UTC)", style="green")
    tbl.add_column("Size", justify="right", style="yellow")
    for idx, v in enumerate(versions, 1):
        tbl.add_row(
            str(idx),
            v.turn_id,
            v.captured_at.strftime("%Y-%m-%d %H:%M:%S"),
            f"{v.size_bytes:,} B",
        )
    console.print(tbl)
    ch.dim(f"Restore with: navig snapshot restore {filepath!r} <turn-id>")


# ──────────────────────────────────────────────────────────────────────
# navig snapshot diff
# ──────────────────────────────────────────────────────────────────────

@app.command("diff")
def snapshot_diff(
    filepath: str = typer.Argument(..., help="File path to diff"),
    from_turn: str | None = typer.Option(None, "--from", help="Earlier turn ID (default: second-to-last version)"),
    to_turn: str | None = typer.Option(None, "--to", help="Later turn ID (default: latest version or live file)"),
    session: str | None = typer.Option(None, "--session", "-s", help="Session ID"),
):
    """Show a unified diff between two stored versions of a file."""
    from rich.syntax import Syntax

    from navig import console_helper as ch
    from navig.file_history import FileVersion, get_file_history_store

    store = get_file_history_store()

    if not store._is_enabled():
        ch.warn("File history is disabled — no versions to diff.")
        raise typer.Exit(1)

    session_id = session or _latest_session()
    if not session_id:
        ch.error("No session ID found.")
        raise typer.Exit(1)

    versions = store.list_versions(filepath, session_id)
    if len(versions) < 1:
        ch.error(f"No stored versions for {filepath!r}.")
        raise typer.Exit(1)

    v1: FileVersion
    v2: FileVersion

    if from_turn:
        v1_matches = [v for v in versions if v.turn_id == from_turn]
        if not v1_matches:
            ch.error(f"Turn {from_turn!r} not found.")
            raise typer.Exit(1)
        v1 = v1_matches[0]
    else:
        if len(versions) < 2:
            ch.warn("Only one stored version — comparing against live file.")
            v1 = versions[-1]
        else:
            v1 = versions[-2]

    if to_turn:
        v2_matches = [v for v in versions if v.turn_id == to_turn]
        if not v2_matches:
            ch.error(f"Turn {to_turn!r} not found.")
            raise typer.Exit(1)
        v2 = v2_matches[0]
    else:
        v2 = versions[-1]

    diff = store.diff_versions(v1, v2)
    if diff == "(no differences)":
        ch.dim("No differences between the selected versions.")
        return

    console.print(Syntax(diff, "diff", theme="monokai", word_wrap=False))


# ──────────────────────────────────────────────────────────────────────
# navig snapshot restore
# ──────────────────────────────────────────────────────────────────────

@app.command("restore")
def snapshot_restore(
    filepath: str = typer.Argument(..., help="File path to restore"),
    turn: str | None = typer.Option(None, "--turn", "-t", help="Turn ID to restore to (default: last version)"),
    session: str | None = typer.Option(None, "--session", "-s", help="Session ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Roll back a file to a previously stored version."""
    from navig import console_helper as ch
    from navig.file_history import get_file_history_store

    store = get_file_history_store()

    if not store._is_enabled():
        ch.warn("File history is disabled.")
        raise typer.Exit(1)

    session_id = session or _latest_session()
    if not session_id:
        ch.error("No session ID found.")
        raise typer.Exit(1)

    versions = store.list_versions(filepath, session_id)
    if not versions:
        ch.error(f"No stored versions for {filepath!r}.")
        raise typer.Exit(1)

    if turn:
        matches = [v for v in versions if v.turn_id == turn]
        if not matches:
            ch.error(f"Turn {turn!r} not found.")
            raise typer.Exit(1)
        target = matches[0]
    else:
        target = versions[-1]

    if not yes:
        console.print(
            f"[yellow]Restore[/yellow] [bold]{filepath}[/bold] "
            f"from turn [cyan]{target.turn_id}[/cyan] "
            f"({target.captured_at.strftime('%Y-%m-%d %H:%M:%S')} UTC, "
            f"{target.size_bytes:,} B)?"
        )
        typer.confirm("Continue?", abort=True)

    ok = store.restore(target)
    if ok:
        ch.success(f"Restored {filepath} from turn {target.turn_id}.")
    else:
        ch.error(f"Restore failed — see debug log for details.")
        raise typer.Exit(1)


# ──────────────────────────────────────────────────────────────────────
# navig snapshot create  (NAVIG config snapshot — future work)
# ──────────────────────────────────────────────────────────────────────

@app.command("create")
def snapshot_create(
    name: str = typer.Argument("", help="Snapshot name (auto-generated if omitted)"),
):
    """Create a named snapshot of the current NAVIG config and state.

    [dim]Full config-snapshot support is planned for a future release.[/dim]
    """
    from navig import console_helper as ch

    ch.warn(
        "Config snapshots are not yet implemented. "
        "To snapshot a specific file, use: [bold]navig snapshot versions <filepath>[/bold]"
    )


# ──────────────────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────────────────

def _latest_session() -> str | None:
    """Try to resolve the most recent session ID from the gateway state."""
    try:
        from navig.gateway_client import get_active_session_id  # type: ignore[import]
        return get_active_session_id()
    except Exception:
        pass
    try:
        from navig.config import get_config_manager
        cfg = get_config_manager()
        return cfg.get("session.last_id")  # type: ignore[return-value]
    except Exception:
        return None
