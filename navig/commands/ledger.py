"""
Ledger Commands for NAVIG - Operations-History Integrity (T-067/T-068)

`navig ledger verify` re-walks the hash chain that OperationRecorder embeds
in every operations.jsonl entry (navig.ledger_chain) and reports intact /
broken-at-line. Exit code contract: 0 = intact (including the honest
non-failure states: missing, empty, legacy pre-chain files), 1 = broken.

`navig ledger show` is the chain-status-aware view of recent operations:
per-entry chain state (verified / legacy / broken line), the green/yellow/red
reversibility label (navig.reversibility), and whether an operation was
already undone. A view, not a gate — it always exits 0; `verify` carries the
exit-code contract.

Honesty: the chain is tamper-EVIDENT, not tamper-proof — a local attacker
who can rewrite the file can recompute the chain. All output words it that
way; never oversell.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

ledger_app = typer.Typer(
    help="Operations ledger — tamper-evident hash chain: verify + show",
    no_args_is_help=True,
)


@ledger_app.callback()
def ledger_callback():
    """Operations-ledger integrity (tamper-evident hash chain).

    Explicit group callback: without it Typer collapses a single-command app
    into the command itself and `navig ledger verify` stops parsing.
    """


def _resolve_ledger_path(path: str | None) -> Path:
    """The ledger to inspect — explicit ``--path`` or the active history file."""
    if path:
        return Path(path)
    from navig.operation_recorder import get_operation_recorder

    return get_operation_recorder().history_file


@ledger_app.command("verify")
def ledger_verify(
    ctx: typer.Context,
    path: str | None = typer.Option(
        None,
        "--path",
        help="Ledger file to verify (defaults to the active operations history)",
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
):
    """
    Walk the operations ledger and re-check every hash-chain link.

    Reports "N operations, chain intact" or "chain broken at line X" with
    honest counts (chained, legacy unchained, restarts, rotation anchor).
    Tamper-evident, not tamper-proof.

    Examples:
        navig ledger verify
        navig ledger verify --json
        navig ledger verify --path ~/.navig/history/operations.jsonl.bak
    """
    from navig import console_helper as ch
    from navig.console_helper import emit_json
    from navig.ledger_chain import verify_ledger

    ledger_path = _resolve_ledger_path(path)
    result = verify_ledger(ledger_path)

    want_json = json_out or bool(ctx.obj and ctx.obj.get("json"))
    if want_json:
        emit_json(result.to_dict())
        if not result.ok:
            raise typer.Exit(1)
        return

    status = result.status
    if status == "missing":
        ch.info(f"No ledger at {result.path} — nothing recorded yet")
        return
    if status == "empty":
        ch.info(f"Ledger at {result.path} is empty — nothing to verify")
        return
    if status == "legacy":
        ch.warning(
            f"{result.total:,} operations predate the hash chain (unchained) — "
            "new operations are chained as they are recorded"
        )
        return

    if status == "intact":
        ch.success(f"{result.chained:,} operations, chain intact")
    else:
        first = result.breaks[0]
        ch.error(f"Chain broken at line {first.line}: {first.reason}")
        for brk in result.breaks[1:10]:
            ch.error(f"  also broken at line {brk.line}: {brk.reason}")
        if len(result.breaks) > 10:
            ch.dim(f"  … and {len(result.breaks) - 10} more break(s)")
        ch.info(f"{result.verified:,}/{result.chained:,} chained operations verified clean")

    if result.unchained:
        ch.dim(f"{result.unchained:,} legacy entr(ies) predate the chain (unchained)")
    if result.anchor:
        ch.dim(
            "chain anchored to a rotated-out entry "
            f"({result.anchor[:23]}…) — expected after rotation"
        )
    for line_no in result.restarts:
        ch.dim(f"chain restarted at line {line_no} (clean restart point)")

    ch.dim("tamper-evident, not tamper-proof — integrity evidence, not a lock")
    if status != "intact":
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# ledger show — chain-status-aware view of recent operations (T-068)
# ---------------------------------------------------------------------------

_CHAIN_GLYPHS = {
    "ok": "[green]✓[/green]",
    "legacy": "[dim]○[/dim]",
    "broken": "[red]✗[/red]",
}

_STATUS_GLYPHS = {
    "success": "[green]+[/green]",
    "failed": "[red]x[/red]",
    "partial": "[yellow]~[/yellow]",
    "interrupted": "[yellow]![/yellow]",
    "cancelled": "[dim]/[/dim]",
    "pending": "[dim]?[/dim]",
}


def _safe_command(command: str) -> str:
    """Command text with secret-looking key=value material redacted."""
    try:
        from navig.core.security import redact_sensitive_text

        return redact_sensitive_text(command)
    except Exception:  # noqa: BLE001 — display fallback only
        return command


def _collect_entries(ledger_path: Path) -> tuple[list[tuple[int, dict]], dict[str, str]]:
    """((line_no, entry) for every parseable line, target-id → undo-id map)."""
    entries: list[tuple[int, dict]] = []
    try:
        with open(ledger_path, encoding="utf-8", errors="replace") as fh:
            for line_no, raw in enumerate(fh, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    entries.append((line_no, data))
    except OSError:
        return [], {}

    undone: dict[str, str] = {}
    for _line_no, data in entries:
        if data.get("status") == "success":
            target = (data.get("args") or {}).get("undo_of")
            if target:
                undone.setdefault(str(target), str(data.get("id", "")))
    return entries, undone


@ledger_app.command("show")
def ledger_show(
    ctx: typer.Context,
    tail: int = typer.Option(20, "--tail", "-n", help="How many recent operations to show"),
    path: str | None = typer.Option(
        None,
        "--path",
        help="Ledger file to show (defaults to the active operations history)",
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
):
    """
    Show recent operations with chain state and reversibility labels.

    Per entry: hash-chain state (✓ verified · ○ legacy pre-chain · ✗ broken
    line), the green/yellow/red reversibility label, status, and whether it
    was already undone. A view, not a gate — always exits 0; use
    `navig ledger verify` for the exit-code contract.

    Examples:
        navig ledger show
        navig ledger show --tail 50
        navig ledger show --json
    """
    from navig import console_helper as ch
    from navig.console_helper import emit_json
    from navig.ledger_chain import verify_ledger
    from navig.reversibility import Reversibility, classify, label_glyph

    ledger_path = _resolve_ledger_path(path)
    result = verify_ledger(ledger_path)
    entries, undone = _collect_entries(ledger_path)
    broken_lines = {b.line for b in result.breaks}
    recent = entries[-max(tail, 0):] if tail else entries

    def build_row(line_no: int, data: dict) -> dict[str, Any]:
        if "hash" not in data:
            chain = "legacy"
        elif line_no in broken_lines:
            chain = "broken"
        else:
            chain = "ok"
        label = data.get("reversibility") or classify(
            str(data.get("operation_type", "other")),
            data.get("undo_data") or None,
            data.get("tags") or [],
        ).value
        op_id = str(data.get("id", ""))
        return {
            "line": line_no,
            "id": op_id,
            "timestamp": str(data.get("timestamp", "")),
            "command": _safe_command(str(data.get("command", ""))),
            "operation_type": str(data.get("operation_type", "other")),
            "status": str(data.get("status", "")),
            "reversibility": label,
            "chain": chain,
            "undone": op_id in undone,
            "undone_by": undone.get(op_id),
        }

    rows = [build_row(line_no, data) for line_no, data in recent]

    if json_out or bool(ctx.obj and ctx.obj.get("json")):
        emit_json(
            {
                "path": result.path,
                "chain": result.to_dict(),
                "count": len(rows),
                "entries": rows,
            }
        )
        return

    status = result.status
    if status == "missing":
        ch.info(f"No ledger at {result.path} — nothing recorded yet")
        return
    if status == "empty":
        ch.info(f"Ledger at {result.path} is empty — nothing to show")
        return

    if status == "broken":
        first = result.breaks[0]
        ch.error(f"Chain broken at line {first.line}: {first.reason}")
    elif status == "legacy":
        ch.warning(f"{result.total:,} operations predate the hash chain (unchained)")
    else:
        ch.success(f"Chain intact · {result.chained:,} chained operations")

    from navig.console_helper import Table, get_console

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Time", no_wrap=True)
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Chain", no_wrap=True)
    table.add_column("Rev", no_wrap=True)
    table.add_column("St", no_wrap=True)
    table.add_column("Command", overflow="fold")

    for row in rows:
        rev = label_glyph(row["reversibility"])
        if row["undone"]:
            rev += " [dim](undone)[/dim]"
        table.add_row(
            row["timestamp"][:16].replace("T", " "),
            row["id"],
            _CHAIN_GLYPHS.get(row["chain"], row["chain"]),
            rev,
            _STATUS_GLYPHS.get(row["status"], "[dim]?[/dim]"),
            row["command"],
        )
    get_console().print(table)

    greens = sum(
        1
        for row in rows
        if row["reversibility"] == Reversibility.GREEN.value and not row["undone"]
    )
    if greens:
        ch.dim(f"{greens} green (undoable) in view · undo the latest: navig undo")
    else:
        ch.dim("no undoable operations in view · preview candidates: navig undo --list")

    # Surface interrupted operations (only for the active ledger — a --path
    # override inspects an arbitrary file that has no live in-flight registry).
    if path is None:
        _note_interrupted_inflight(ch)


def _note_interrupted_inflight(ch) -> None:
    """One dim line when the active ledger has interrupted (never-completed) ops.

    Read-only — `ledger show` never mutates the ledger (it is skip-recorded for
    the observer effect); the honest recording happens in `navig ledger reap`.
    """
    try:
        from navig import operation_inflight as _inflight
        from navig.operation_recorder import get_operation_recorder

        markers = get_operation_recorder().iter_inflight()
        interrupted = sum(
            1 for m in markers if not _inflight.pid_is_alive(m.pid, m.create_time)
        )
    except Exception:  # noqa: BLE001 — a display nudge must never crash the view
        return
    if interrupted:
        ch.dim(
            f"{interrupted} operation(s) never completed (process gone) · "
            "record them: navig ledger reap"
        )


# ---------------------------------------------------------------------------
# ledger reap — age out interrupted operations to a terminal `interrupted`
# status (Task 2: an op whose process is hard-killed leaves no ledger line)
# ---------------------------------------------------------------------------


@ledger_app.command("reap")
def ledger_reap(
    ctx: typer.Context,
    older_than: float = typer.Option(
        2.0,
        "--older-than",
        help="Only reap operations idle at least this many minutes",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be reaped without recording anything"
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
):
    """
    Record interrupted operations as `interrupted` in the ledger.

    An operation writes a ledger line only when it finishes; a process killed
    (SIGKILL, or a SIGTERM that never reaches the completer) mid-run leaves an
    in-flight marker but no line — the operation would otherwise vanish
    silently. This ages those out to a terminal `interrupted` status by
    APPENDING one record each (chain-safe — never a rewrite, so the hash chain
    stays intact). An operation whose process is still alive is never touched.

    Idempotent: a reaped operation stays reaped; running it again reaps nothing
    new.

    Examples:
        navig ledger reap
        navig ledger reap --dry-run
        navig ledger reap --older-than 5
    """
    from navig import console_helper as ch
    from navig.console_helper import Table, emit_json, get_console
    from navig.operation_recorder import get_operation_recorder

    recorder = get_operation_recorder()
    max_age = max(older_than, 0.0) * 60.0
    reaped = recorder.reap_inflight(max_age_seconds=max_age, dry_run=dry_run)

    if json_out or bool(ctx.obj and ctx.obj.get("json")):
        emit_json({"reaped": reaped, "count": len(reaped), "dry_run": dry_run})
        return

    if not reaped:
        ch.success("No interrupted operations — nothing to reap")
        return

    verb = "would reap" if dry_run else "reaped"
    ch.warning(f"{len(reaped)} interrupted operation(s) {verb}:")

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Started", no_wrap=True)
    table.add_column("Idle", no_wrap=True)
    table.add_column("Command", overflow="fold")
    for row in reaped:
        started = str(row.get("started_at", ""))[:16].replace("T", " ")
        idle_min = f"{(row.get('age_seconds') or 0) / 60:.0f}m"
        table.add_row(started, idle_min, _safe_command(str(row.get("command", ""))))
    get_console().print(table)

    if dry_run:
        ch.dim("dry run — nothing recorded · run without --dry-run to record these")
    else:
        ch.dim("recorded as interrupted · chain stays intact: navig ledger verify")
