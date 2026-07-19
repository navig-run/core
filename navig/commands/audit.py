"""
Audit Commands for NAVIG — terminal surface for the privileged-action audit log.

`navig audit tail` reads the gateway AuditLog (`runtime/audit.jsonl`) DIRECTLY
from disk — no daemon required, works offline (same contract as
`navig ledger show`). Every privileged action that passes the gateway's
policy/approval gates leaves one JSONL record there (#299 wired the gates;
this command makes the trail visible from the terminal — before it, the log
was queryable only via GET /audit on a running gateway).

A view, not a gate — always exits 0, including for a missing or empty log
(honest non-failure states). Malformed lines are skipped, never fatal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

audit_app = typer.Typer(
    help="Privileged-action audit trail — gateway policy/approval decisions",
    no_args_is_help=True,
)


@audit_app.callback()
def audit_callback():
    """Privileged-action audit trail (gateway policy + approval decisions).

    Explicit group callback: without it Typer collapses a single-command app
    into the command itself and `navig audit tail` stops parsing.
    """


def _resolve_audit_path(path: str | None) -> Path:
    """The audit file to inspect — explicit ``--path`` or the live gateway log.

    Resolved at CALL time via the platform paths module (never a module-level
    constant — the frozen-path tripwire bans that shape; see
    ``navig/gateway/audit_log.py:_default_path`` for the same idiom).
    """
    if path:
        return Path(path)
    from navig.platform import paths

    return paths.config_dir() / "runtime" / "audit.jsonl"


def _collect_records(audit_path: Path) -> list[dict[str, Any]]:
    """Every parseable record in file order; malformed lines are skipped."""
    records: list[dict[str, Any]] = []
    try:
        with open(audit_path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    records.append(data)
    except OSError:
        return []
    return records


_STATUS_GLYPHS = {
    "success": "[green]✓ success[/green]",
    "approved": "[green]✓ approved[/green]",
    "denied": "[red]✗ denied[/red]",
    "error": "[red]! error[/red]",
    "pending_approval": "[yellow]… pending[/yellow]",
}


def _short_hash(record: dict[str, Any]) -> str:
    """``sha256:abcdef…`` → ``abcdef123456`` (12 chars); '' when absent."""
    raw = str(record.get("input_hash") or "")
    if not raw:
        return ""
    return raw.split(":", 1)[-1][:12]


@audit_app.command("tail")
def audit_tail(
    ctx: typer.Context,
    tail: int = typer.Option(20, "--tail", "-n", help="How many recent records to show"),
    action: str | None = typer.Option(
        None, "--action", help="Filter by action prefix (e.g. 'tool.execute')"
    ),
    actor: str | None = typer.Option(
        None, "--actor", help="Filter by exact actor (e.g. 'telegram:user:123')"
    ),
    status: str | None = typer.Option(
        None,
        "--status",
        help="Filter by status (success/approved/denied/error/pending_approval)",
    ),
    path: str | None = typer.Option(
        None, "--path", help="Audit file to read (defaults to the live runtime/audit.jsonl)"
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
):
    """
    Show recent privileged-action audit records from the gateway audit log.

    Per record: time, actor, action, decision status (✓ approved/success ·
    ✗ denied · … pending · ! error), the short input hash, and the recorded
    reason. Reads runtime/audit.jsonl directly — no daemon required.

    Examples:
        navig audit tail
        navig audit tail -n 50 --status denied
        navig audit tail --action tool.execute --json
    """
    from navig import console_helper as ch
    from navig.console_helper import emit_json

    audit_path = _resolve_audit_path(path)
    records = _collect_records(audit_path)
    total = len(records)

    if action:
        records = [r for r in records if str(r.get("action", "")).startswith(action)]
    if actor:
        records = [r for r in records if r.get("actor") == actor]
    if status:
        records = [r for r in records if r.get("status") == status]
    recent = records[-max(tail, 0):] if tail else records

    if json_out or bool(ctx.obj and ctx.obj.get("json")):
        emit_json(
            {
                "path": str(audit_path),
                "total": total,
                "count": len(recent),
                "events": recent,
            }
        )
        return

    if not audit_path.exists():
        ch.info(f"No audit log at {audit_path} — nothing recorded yet")
        ch.dim("privileged actions land here once they pass the gateway's policy/approval gates")
        return
    if total == 0:
        ch.info(f"Audit log at {audit_path} is empty — nothing recorded yet")
        return
    if not recent:
        ch.info("No records match the given filters")
        ch.dim(f"{total} record(s) in the log · relax --action/--actor/--status")
        return

    from navig.console_helper import Table, get_console

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Time", no_wrap=True)
    table.add_column("Actor", no_wrap=True)
    table.add_column("Action", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Input", style="dim", no_wrap=True)
    table.add_column("Reason", overflow="fold")

    for record in recent:
        meta = record.get("metadata") or {}
        reason = str(meta.get("reason") or "")
        table.add_row(
            str(record.get("ts", ""))[:16].replace("T", " "),
            str(record.get("actor", "")),
            str(record.get("action", "")),
            _STATUS_GLYPHS.get(str(record.get("status", "")), f"[dim]{record.get('status', '?')}[/dim]"),
            _short_hash(record),
            reason,
        )
    get_console().print(table)

    denied = sum(1 for r in recent if r.get("status") == "denied")
    pending = sum(1 for r in recent if r.get("status") == "pending_approval")
    summary = f"{len(recent)}/{total} record(s)"
    if denied:
        summary += f" · [red]{denied} denied[/red]"
    if pending:
        summary += f" · {pending} pending · respond: navig approve list"
    ch.dim(f"{summary} · filter with --action/--actor/--status · verify chain: navig ledger verify")
