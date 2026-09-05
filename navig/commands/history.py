"""
History Commands for NAVIG - Replay, Undo, Audit

Provides commands for:
- Viewing command history
- Replaying previous commands
- Undoing reversible operations
- Exporting audit logs
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import typer

from navig import console_helper as ch
from navig.cli.options import is_dry_run
from navig.console_helper import get_console
from navig.operation_recorder import (
    OperationStatus,
    OperationType,
    get_operation_recorder,
)


def show_history(
    limit: int = 20,
    host: str | None = None,
    operation_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    since: str | None = None,
    opts: dict[str, Any] = None,
) -> None:
    """
    Show command history with filtering.

    Args:
        limit: Maximum number of entries to show
        host: Filter by host
        operation_type: Filter by operation type
        status: Filter by status (success/failed)
        search: Search in command text
        since: Filter by time (e.g., "1h", "24h", "7d")
        opts: CLI options
    """
    opts = opts or {}
    want_json = opts.get("json", False)
    want_plain = opts.get("plain", False)
    verbose = opts.get("verbose", False)

    recorder = get_operation_recorder()

    # Parse since parameter
    since_timestamp = None
    if since:
        since_timestamp = _parse_since(since)

    # Parse operation type
    op_type = None
    if operation_type:
        try:
            op_type = OperationType(operation_type)
        except ValueError:
            ch.error(f"Invalid operation type: {operation_type}")
            ch.info("Valid types: " + ", ".join(t.value for t in OperationType))
            raise typer.Exit(2) from None

    # Parse status
    op_status = None
    if status:
        try:
            op_status = OperationStatus(status)
        except ValueError:
            ch.error(f"Invalid status: {status}")
            ch.info("Valid statuses: success, failed, partial, cancelled")
            raise typer.Exit(2) from None

    # Get operations
    operations = list(
        recorder.iter_operations(
            limit=limit,
            host=host,
            operation_type=op_type,
            status=op_status,
            search=search,
            since=since_timestamp,
        )
    )

    if not operations:
        ch.info("No operations found matching criteria")
        return

    if want_json:
        print(json.dumps([op.to_dict() for op in operations], indent=2))
        return

    if want_plain:
        for op in operations:
            status_char = "+" if op.status == OperationStatus.SUCCESS else "-"
            print(f"{status_char} {op.id} {op.timestamp[:19]} {op.command}")
        return

    # Rich output
    ch.header(f"Command History (last {len(operations)})")

    from rich.table import Table

    console = get_console()

    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("ID", style="dim", width=24)
    table.add_column("Time", width=19)
    table.add_column("Status", width=8)
    table.add_column("Host", width=12)
    table.add_column("Command", overflow="fold")

    for op in operations:
        # Format status with color
        if op.status == OperationStatus.SUCCESS:
            status_str = "[green]+[/green]"
        elif op.status == OperationStatus.FAILED:
            status_str = "[red]x[/red]"
        elif op.status == OperationStatus.PARTIAL:
            status_str = "[yellow]~[/yellow]"
        else:
            status_str = "[dim]?[/dim]"

        # Format timestamp
        time_str = op.timestamp[:19].replace("T", " ")

        # Truncate command if needed
        cmd = op.command[:60] + "..." if len(op.command) > 60 else op.command

        table.add_row(
            op.id,
            time_str,
            status_str,
            op.host or "-",
            cmd,
        )

    console.print(table)

    if verbose:
        console.print()
        console.print("[dim]Use 'navig history show <id>' for details[/dim]")
        console.print("[dim]Use 'navig history replay <id>' to re-run[/dim]")


# The canonical reader for the GLOBAL --dry-run flag. Kept under the private name so
# existing call sites and tests are unchanged; the definition now lives in one place
# because `context.py` needed exactly the same check.
_is_dry_run = is_dry_run


def _resolve_operation(recorder: Any, op_id: str) -> Any | None:
    """Resolve ``op_id`` — a 1-based index or a record ID — to an operation.

    Reports the reason and returns ``None`` when it cannot be resolved.

    This was three identical copies, and each of them accepted index ``0``. Indices are
    1-based ("1 = last"), so ``0`` produced ``index = -1`` and a ``limit=0`` read; the
    bounds check ``index >= len(operations)`` is ``-1 >= 1`` → False, so it fell through
    to ``operations[-1]`` and silently selected the MOST RECENT operation. Both callers
    that reach this are destructive — ``navig history undo 0`` reversed an operation the
    user never named, and ``replay 0`` re-executed one.
    """
    if op_id.isdigit():
        index = int(op_id) - 1
        if index < 0:
            ch.error(f"Invalid index '{op_id}' — operation indices start at 1 (1 = last)")
            return None
        operations = list(recorder.iter_operations(limit=index + 1))
        if index >= len(operations):
            ch.error(f"No operation at index {op_id}")
            return None
        return operations[index]

    op = recorder.get_operation(op_id)
    if not op:
        ch.error(f"Operation not found: {op_id}")
    return op


def show_operation_details(op_id: str, opts: dict[str, Any] = None) -> None:
    """
    Show detailed information about a specific operation.

    Args:
        op_id: Operation ID or index (e.g., "1" for last, "2" for second-last)
        opts: CLI options
    """
    opts = opts or {}
    want_json = opts.get("json", False)

    recorder = get_operation_recorder()

    op = _resolve_operation(recorder, op_id)
    if not op:
        # _resolve_operation already printed the reason; naming an operation that
        # does not exist is the usage class.
        raise typer.Exit(2)

    if want_json:
        print(json.dumps(op.to_dict(), indent=2))
        return

    # Rich output
    ch.header(f"Operation Details: {op.id}")

    from rich.panel import Panel

    console = get_console()

    # Status with color
    if op.status == OperationStatus.SUCCESS:
        status_str = "[green]SUCCESS[/green]"
    elif op.status == OperationStatus.FAILED:
        status_str = "[red]FAILED[/red]"
    else:
        status_str = f"[yellow]{op.status.value.upper()}[/yellow]"

    info_lines = [
        f"[bold]Command:[/bold] {op.command}",
        f"[bold]Status:[/bold] {status_str}",
        f"[bold]Timestamp:[/bold] {op.timestamp}",
        f"[bold]Duration:[/bold] {op.duration_ms:.2f}ms",
        f"[bold]Host:[/bold] {op.host or 'N/A'}",
        f"[bold]App:[/bold] {op.app or 'N/A'}",
        f"[bold]Type:[/bold] {op.operation_type.value}",
        f"[bold]Working Dir:[/bold] {op.working_dir}",
        f"[bold]Reversible:[/bold] {'Yes' if op.reversible else 'No'}",
    ]

    if op.tags:
        info_lines.append(f"[bold]Tags:[/bold] {', '.join(op.tags)}")

    if op.exit_code != 0:
        info_lines.append(f"[bold]Exit Code:[/bold] [red]{op.exit_code}[/red]")

    console.print(Panel("\n".join(info_lines), title="Info"))

    if op.output:
        console.print()
        console.print(Panel(op.output, title="Output", border_style="green"))

    if op.error:
        console.print()
        console.print(Panel(op.error, title="Error", border_style="red"))

    if op.args:
        console.print()
        console.print(Panel(json.dumps(op.args, indent=2), title="Arguments"))


def replay_operation(
    op_id: str,
    dry_run: bool = False,
    modify: str | None = None,
    opts: dict[str, Any] = None,
) -> None:
    """
    Replay a previous operation.

    Args:
        op_id: Operation ID or index
        dry_run: If True, show what would be done without executing
        modify: Modification to apply (e.g., "--host=newhost")
        opts: CLI options
    """
    opts = opts or {}
    # `--dry-run` also arrives globally via ctx.obj; honour either spelling.
    dry_run = _is_dry_run(opts, dry_run)

    recorder = get_operation_recorder()

    op = _resolve_operation(recorder, op_id)
    if not op:
        raise typer.Exit(2)

    # Build command
    command = op.command

    # Apply modifications
    if modify:
        command = _apply_modifications(command, modify)

    ch.info(f"Replaying: {command}")

    if dry_run:
        ch.dim("DRY RUN: Would execute the command above")
        return

    # Confirm before execution
    if not opts.get("yes", False):
        from rich.prompt import Confirm

        if not Confirm.ask("Execute this command?", default=True):
            ch.info("Cancelled")
            return

    # Execute the command
    import subprocess
    import sys

    # Re-run through navig
    cmd_parts = command.split()
    if cmd_parts[0] == "navig":
        cmd_parts = cmd_parts[1:]  # Remove 'navig' prefix

    # Build full command
    full_cmd = [sys.executable, "-m", "navig"] + cmd_parts

    try:
        result = subprocess.run(
            full_cmd,
            capture_output=False,
            text=True,
            cwd=op.working_dir if Path(op.working_dir).exists() else None,
        )

        if result.returncode == 0:
            ch.success("Replay completed successfully")
        else:
            # The replay IS the command — a failed replay must not report success.
            # `navig history replay 1 && <next>` ran the next step regardless.
            ch.error(f"Replay failed with exit code {result.returncode}")
            raise typer.Exit(1)

    except typer.Exit:
        # typer.Exit is a RuntimeError, so the broad handler below catches it and
        # would turn the deliberate exit above into "Failed to replay: 1" at exit 0.
        raise
    except Exception as e:
        ch.error(f"Failed to replay: {e}")
        raise typer.Exit(1) from e


def undo_operation(op_id: str, opts: dict[str, Any] = None) -> None:
    """
    Undo a reversible operation — delegates to the T-068 undo engine
    (``navig.undo``): same green-only rule, drift detection, double-undo
    protection, and chained undo recording as the top-level ``navig undo``.

    Args:
        op_id: Operation ID or index (1 = last)
        opts: CLI options (respects ``yes`` for the confirm gate)
    """
    opts = opts or {}

    import time

    from navig.undo import (
        UndoRefused,
        check_drift,
        collect_undone,
        describe_undo,
        ensure_undoable,
        perform_undo,
        recent_records,
    )

    recorder = get_operation_recorder()

    op = _resolve_operation(recorder, op_id)
    if not op:
        raise typer.Exit(2)

    try:
        ensure_undoable(op, collect_undone(recent_records(recorder)))
        check_drift(op)
    except UndoRefused as exc:
        # The top-level `navig undo` routes every refusal through _fail(), which
        # always exits 1. This command documents itself as the same engine with the
        # same rules, so it must agree — nothing was undone either way.
        ch.error(str(exc))
        ch.dim(f"Command was: {op.command}")
        raise typer.Exit(1) from exc

    description = describe_undo(op)
    ch.info(f"Undoing: {op.command}")
    ch.info(f"  will: {description}")

    # Before start_operation() — a dry run must not leave an undo record behind either.
    if _is_dry_run(opts):
        ch.dim("DRY RUN: Nothing was undone.")
        return

    if not opts.get("yes", False):
        from rich.prompt import Confirm

        if not Confirm.ask("Undo this operation?", default=False):
            ch.info("Cancelled")
            return

    started = time.time()
    undo_record = recorder.start_operation(
        command=f"navig undo {op.id}",
        operation_type=op.operation_type,
        args={"undo_of": op.id},
        tags=["undo"],
    )
    try:
        swapped = perform_undo(op)
    except Exception as e:  # noqa: BLE001 — record the failure, then surface it
        recorder.complete_operation(
            undo_record,
            success=False,
            error=str(e),
            exit_code=1,
            duration_ms=(time.time() - started) * 1000,
        )
        # The ledger has ALREADY recorded this undo as failed. Exiting 0 here made
        # navig's two truth sources contradict each other: `navig history list`
        # showed FAILED while the shell that ran it saw success.
        ch.error(f"Undo failed: {e}")
        raise typer.Exit(1) from e

    undo_id = recorder.complete_operation(
        undo_record,
        success=True,
        output=description,
        duration_ms=(time.time() - started) * 1000,
        undo_data=swapped,
    )
    ch.success(f"Undone: {description}")
    ch.dim(f"recorded as {undo_id} (tagged undo)")


def export_history(
    output_file: str,
    format: str = "json",
    limit: int = 1000,
    opts: dict[str, Any] = None,
) -> None:
    """
    Export operation history to file.

    Args:
        output_file: Output file path
        format: Export format (json, csv)
        limit: Maximum entries to export
        opts: CLI options
    """
    opts = opts or {}

    recorder = get_operation_recorder()
    output_path = Path(output_file)

    try:
        if format == "json":
            count = recorder.export_json(output_path, limit=limit)
        elif format == "csv":
            count = recorder.export_csv(output_path, limit=limit)
        else:
            ch.error(f"Unknown format: {format}. Use 'json' or 'csv'")
            raise typer.Exit(2)

        ch.success(f"Exported {count} operations to {output_file}")

    except typer.Exit:
        # Shield the deliberate exit above from the broad handler below, which would
        # otherwise print "Export failed: 2" and exit 0.
        raise
    except Exception as e:
        # An audit export that wrote nothing must not look like a completed export —
        # this is the file a compliance check reads.
        ch.error(f"Export failed: {e}")
        raise typer.Exit(1) from e


def clear_history(opts: dict[str, Any] = None) -> None:
    """Clear all operation history."""
    opts = opts or {}

    recorder = get_operation_recorder()
    count = recorder.count()

    if _is_dry_run(opts):
        ch.info(f"[yellow]DRY RUN:[/yellow] Would clear {count} operations from history")
        ch.dim("No history was deleted.")
        return

    if not opts.get("yes", False):
        from rich.prompt import Confirm

        # Say what is actually lost. The old prompt was "Clear all operation history?",
        # which reads like tidying a log — but these records ARE the undo history, so
        # clearing them permanently removes the ability to `navig undo` any of them.
        ch.warning(f"This deletes {count} operations, including their undo records.")
        ch.dim("Anything already done can no longer be undone with 'navig undo'.")
        if not Confirm.ask("Clear all operation history?", default=False):
            ch.info("Cancelled")
            return

    cleared = recorder.clear_history()
    ch.success(f"Cleared {cleared} operations from history")


def history_stats(opts: dict[str, Any] = None) -> None:
    """Show history statistics."""
    opts = opts or {}
    want_json = opts.get("json", False)

    recorder = get_operation_recorder()

    # Count by status
    success_count = recorder.count(status=OperationStatus.SUCCESS)
    failed_count = recorder.count(status=OperationStatus.FAILED)
    total_count = recorder.count()

    # Count by type
    type_counts = {}
    for op_type in OperationType:
        count = recorder.count(operation_type=op_type)
        if count > 0:
            type_counts[op_type.value] = count

    # Count by host. The totals above come from recorder.count(), which scans up to
    # _UNBOUNDED_SCAN_LIMIT — so a bare 10000 here made the per-host numbers stop
    # adding up to the total once the history grew past it, with nothing to say so.
    from navig.operation_recorder import _UNBOUNDED_SCAN_LIMIT

    host_counts = {}
    for op in recorder.iter_operations(limit=_UNBOUNDED_SCAN_LIMIT):
        host = op.host or "local"
        host_counts[host] = host_counts.get(host, 0) + 1

    if want_json:
        result = {
            "total": total_count,
            "success": success_count,
            "failed": failed_count,
            "by_type": type_counts,
            "by_host": host_counts,
        }
        print(json.dumps(result, indent=2))
        return

    ch.header("History Statistics")

    from rich.table import Table

    console = get_console()

    # Summary
    success_rate = (success_count / total_count * 100) if total_count > 0 else 0
    console.print(f"Total operations: [bold]{total_count}[/bold]")
    console.print(
        f"Success rate: [green]{success_rate:.1f}%[/green] ({success_count} success, {failed_count} failed)"
    )
    console.print()

    # By type
    if type_counts:
        table = Table(title="By Operation Type", show_header=True, box=None)
        table.add_column("Type", style="cyan")
        table.add_column("Count", justify="right")

        for op_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            table.add_row(op_type, str(count))

        console.print(table)
        console.print()

    # By host (top 5)
    if host_counts:
        table = Table(title="By Host (Top 5)", show_header=True, box=None)
        table.add_column("Host", style="cyan")
        table.add_column("Count", justify="right")

        for host, count in sorted(host_counts.items(), key=lambda x: -x[1])[:5]:
            table.add_row(host, str(count))

        console.print(table)


# Helper functions


def _parse_since(since: str) -> str | None:
    """Parse a 'since' parameter like '1h', '24h', '7d' into ISO timestamp."""
    import re

    match = re.match(r"^(\d+)([hdwm])$", since.lower())
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2)

    if unit == "h":
        delta = timedelta(hours=value)
    elif unit == "d":
        delta = timedelta(days=value)
    elif unit == "w":
        delta = timedelta(weeks=value)
    elif unit == "m":
        delta = timedelta(days=value * 30)
    else:
        return None

    since_time = datetime.now() - delta
    return since_time.isoformat()


def _apply_modifications(command: str, modify: str) -> str:
    """Apply modifications to a command string."""
    # Simple implementation: append modifications
    return f"{command} {modify}"


# Per-type _undo_* helpers were removed in T-068: the undo engine
# (navig/undo.py) is the single implementation — drift-checked, idempotent,
# vault-safe, and shared with the top-level `navig undo`.


# ============================================================================
# TYPER SUB-APP — extracted from navig/cli/__init__.py
# ============================================================================

history_app = typer.Typer(
    help="Command history, replay, and audit trail",
    invoke_without_command=True,
    no_args_is_help=False,
)


@history_app.callback()
def history_callback(ctx: typer.Context):
    """History management - shows recent history if no subcommand."""
    # Every subcommand below writes into ctx.obj (`ctx.obj["yes"] = …`). Reached any
    # way other than through the root `navig` callback — a test, a direct sub-app
    # invocation — ctx.obj is None and that assignment dies with
    # "'NoneType' object does not support item assignment". This group callback runs
    # before every subcommand, so it is the one place that has to guarantee the dict.
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand is None:
        show_history(limit=20, opts=ctx.obj)
        raise typer.Exit()


@history_app.command("list")
def history_list(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-l", help="Number of entries to show"),
    host: str | None = typer.Option(None, "--host", "-h", help="Filter by host"),
    type_filter: str | None = typer.Option(None, "--type", "-t", help="Filter by operation type"),
    status: str | None = typer.Option(
        None, "--status", "-s", help="Filter by status (success/failed)"
    ),
    search: str | None = typer.Option(None, "--search", "-q", help="Search in command text"),
    since: str | None = typer.Option(None, "--since", help="Time filter (e.g., 1h, 24h, 7d)"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    List command history with filtering.

    Examples:
        navig history list
        navig history list --limit 50
        navig history list --host production
        navig history list --status failed --since 24h
        navig history list --search "docker" --json
    """
    ctx.obj["plain"] = plain
    if json_out:
        ctx.obj["json"] = True
    show_history(
        limit=limit,
        host=host,
        operation_type=type_filter,
        status=status,
        search=search,
        since=since,
        opts=ctx.obj,
    )


@history_app.command("show")
def history_show_cmd(
    ctx: typer.Context,
    op_id: str = typer.Argument(..., help="Operation ID or index (1=last, 2=second-last)"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show detailed information about an operation.

    Examples:
        navig history show 1              # Show last operation
        navig history show op-20260208... # Show by ID
        navig history show 1 --json       # JSON output
    """
    if json_out:
        ctx.obj["json"] = True
    show_operation_details(op_id, opts=ctx.obj)


@history_app.command("replay")
def history_replay(
    ctx: typer.Context,
    op_id: str = typer.Argument(..., help="Operation ID or index to replay"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be done"),
    modify: str | None = typer.Option(None, "--modify", "-m", help="Modify command before replay"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Replay a previous operation.

    Examples:
        navig history replay 1                    # Replay last command
        navig history replay 1 --dry-run          # Preview only
        navig history replay 1 --modify "--host staging"
    """
    ctx.obj["yes"] = yes
    replay_operation(op_id, dry_run=dry_run, modify=modify, opts=ctx.obj)


@history_app.command("undo")
def history_undo(
    ctx: typer.Context,
    op_id: str = typer.Argument(..., help="Operation ID or index to undo"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Undo a reversible (green) operation — confirm-gated.

    Same engine as the top-level `navig undo`: green-only, drift-checked,
    double-undo protected; the undo itself is recorded on the ledger.

    Examples:
        navig history undo 1
        navig history undo op-20260716... --yes
    """
    ctx.obj["yes"] = yes or ctx.obj.get("yes", False)
    undo_operation(op_id, opts=ctx.obj)


@history_app.command("export")
def history_export_cmd(
    ctx: typer.Context,
    output: str = typer.Argument(..., help="Output file path"),
    format: str = typer.Option("json", "--format", "-f", help="Export format (json, csv)"),
    limit: int = typer.Option(1000, "--limit", "-l", help="Max entries to export"),
):
    """
    Export operation history to file.

    Examples:
        navig history export audit.json
        navig history export audit.csv --format csv
        navig history export all.json --limit 10000
    """
    export_history(output, format=format, limit=limit, opts=ctx.obj)


@history_app.command("clear")
def history_clear_cmd(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Clear all operation history.

    Examples:
        navig history clear
        navig history clear --yes
    """
    ctx.obj["yes"] = yes
    clear_history(opts=ctx.obj)


@history_app.command("stats")
def history_stats_cmd(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show history statistics.

    Examples:
        navig history stats
        navig history stats --json
    """
    if json_out:
        ctx.obj["json"] = True
    history_stats(opts=ctx.obj)
