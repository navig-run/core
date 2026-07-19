"""
`navig undo` — confirm-gated replay of the last green (undoable) operation.

T-068 (plan-evidence-ledger.md). The engine lives in ``navig.undo``; this
module is only the CLI surface:

- ``navig undo``            → undo the LAST green operation (confirm-gated)
- ``navig undo <op-id>``    → undo a specific operation by id
- ``navig undo --list``     → preview candidates without touching anything
- ``--yes`` skips the confirmation; ``--json`` keeps stdout machine-pure
  (an un-confirmed undo under ``--json`` is refused — prompts would corrupt
  the stream).

Safety renders here, but is enforced in the engine: green-only, drift
detection, double-undo protection, and no plaintext secrets. Every undo is
itself recorded on the hash chain (tagged ``undo``, capped at yellow).

Registered as a FLAT top-level command (like `navig wire` / `navig apply`):
a single-command Typer group would be collapsed by Typer, and options after
the optional positional id must parse.
"""

from __future__ import annotations

import time

import typer


def undo_command(
    ctx: typer.Context,
    op_id: str | None = typer.Argument(
        None, help="Operation ID to undo (default: the last green operation)"
    ),
    list_only: bool = typer.Option(
        False, "--list", "-l", help="Preview undo candidates without undoing anything"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
):
    """
    Undo the last green (undoable) operation — confirm-gated.

    Shows exactly what will be undone and asks before touching anything.
    Refuses honestly when the target is not green, was already undone, or
    its target changed since (drift). The undo itself is recorded on the
    tamper-evident ledger.

    Examples:
        navig undo --list
        navig undo
        navig undo op-20260716120000-abcd1234 --yes
        navig undo --list --json
    """
    from navig import console_helper as ch
    from navig.console_helper import emit_json
    from navig.operation_recorder import claim_cli_operation, get_operation_recorder
    from navig.undo import (
        UndoRefused,
        check_drift,
        collect_undone,
        describe_undo,
        ensure_undoable,
        find_candidates,
        perform_undo,
        recent_records,
    )

    recorder = get_operation_recorder()

    # ------------------------------------------------------------------
    # Preview mode — read-only
    # ------------------------------------------------------------------
    if list_only:
        candidates = find_candidates(recorder, limit=10)
        if json_out:
            emit_json(
                {
                    "candidates": [
                        {
                            "id": c.record.id,
                            "timestamp": c.record.timestamp,
                            "command": _safe_command(c.record.command),
                            "operation_type": c.record.operation_type.value,
                            "state": c.state,
                            "detail": c.detail,
                            "would": describe_undo(c.record),
                        }
                        for c in candidates
                    ],
                    "ready": sum(1 for c in candidates if c.state == "ready"),
                }
            )
            return
        if not candidates:
            ch.info("No green (undoable) operations in the ledger")
            ch.dim("green = undo_data captured at execution time — e.g. navig config set")
            return
        _render_candidates(candidates, describe_undo)
        ready = sum(1 for c in candidates if c.state == "ready")
        if ready:
            ch.dim(f"{ready} ready · undo the latest with: navig undo")
        else:
            ch.dim("nothing ready to undo")
        return

    # ------------------------------------------------------------------
    # Resolve the target
    # ------------------------------------------------------------------
    records = recent_records(recorder)
    undone = collect_undone(records)

    if op_id:
        target = recorder.get_operation(op_id)
        if target is None:
            _fail(f"Operation not found: {op_id}", json_out)
    else:
        # THE last green operation — never silently skip past it to an older
        # one: if it is undone/drifted, say so and stop (explicit id targets
        # older operations).
        candidates = find_candidates(recorder, limit=1)
        if not candidates:
            _fail(
                "Nothing to undo — no green (undoable) operations in the ledger",
                json_out,
                hint="preview candidates with: navig undo --list",
            )
        cand = candidates[0]
        if cand.state != "ready":
            _fail(
                f"The last green operation {cand.record.id} cannot be undone: {cand.detail}",
                json_out,
                hint="see other candidates with: navig undo --list",
            )
        target = cand.record

    # ------------------------------------------------------------------
    # Refusal checks (engine-enforced)
    # ------------------------------------------------------------------
    try:
        ensure_undoable(target, undone)
        check_drift(target)
    except UndoRefused as exc:
        _fail(str(exc), json_out)

    description = describe_undo(target)

    # ------------------------------------------------------------------
    # Confirm gate
    # ------------------------------------------------------------------
    if json_out and not yes:
        emit_json(
            {
                "error": "confirmation required — pass --yes to undo non-interactively",
                "id": target.id,
                "would": description,
            }
        )
        raise typer.Exit(1)

    if not json_out:
        when = target.timestamp[:19].replace("T", " ")
        ch.info(f"Undo target: {target.id} ({when})")
        ch.dim(f"  command: {_safe_command(target.command)}")
        ch.dim(f"  type:    {target.operation_type.value} · label: green (undoable)")
        ch.info(f"  will:    {description}")
        if not yes:
            from rich.prompt import Confirm

            if not Confirm.ask("Undo this operation?", default=False):
                ch.info("Cancelled")
                return

    # ------------------------------------------------------------------
    # Perform + record (the undo rides the same hash chain, tagged `undo`)
    # ------------------------------------------------------------------
    record, start = claim_cli_operation(match=("navig undo",))
    if record is None:
        record = recorder.start_operation(command=f"navig undo {target.id}")
    record.operation_type = target.operation_type
    record.args = {**(record.args or {}), "undo_of": target.id}
    record.tags = sorted({*(record.tags or []), "undo"})
    started = start or time.time()

    try:
        swapped = perform_undo(target)
    except Exception as exc:  # noqa: BLE001 — record the failure, then surface it
        recorder.complete_operation(
            record,
            success=False,
            error=str(exc),
            exit_code=1,
            duration_ms=(time.time() - started) * 1000,
        )
        _fail(f"Undo failed: {exc}", json_out)

    undo_id = recorder.complete_operation(
        record,
        success=True,
        output=description,
        duration_ms=(time.time() - started) * 1000,
        undo_data=swapped,
    )

    if json_out:
        emit_json({"undone": target.id, "did": description, "recorded": undo_id})
        return
    ch.success(f"Undone: {description}")
    ch.dim(f"recorded as {undo_id} (tagged undo) · verify the chain: navig ledger verify")


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

_STATE_GLYPHS = {
    "ready": "[green]● ready[/green]",
    "undone": "[dim]○ undone[/dim]",
    "drift": "[yellow]⚠ drift[/yellow]",
}


def _safe_command(command: str) -> str:
    """Command text with secret-looking key=value material redacted."""
    try:
        from navig.core.security import redact_sensitive_text

        return redact_sensitive_text(command)
    except Exception:  # noqa: BLE001 — display fallback only
        return command


def _render_candidates(candidates, describe_undo) -> None:
    """House-style table of undo candidates (one wrappable column)."""
    from navig.console_helper import Table, get_console

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Time", no_wrap=True)
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Would undo", overflow="fold")

    for cand in candidates:
        rec = cand.record
        would = describe_undo(rec) if cand.state == "ready" else (cand.detail or "—")
        table.add_row(
            rec.timestamp[:16].replace("T", " "),
            rec.id,
            _STATE_GLYPHS.get(cand.state, cand.state),
            would,
        )
    get_console().print(table)


def _fail(message: str, json_out: bool, hint: str = "") -> None:
    """Refusal path for both output modes; always exits 1."""
    if json_out:
        from navig.console_helper import emit_json

        payload = {"error": message}
        if hint:
            payload["hint"] = hint
        emit_json(payload)
    else:
        from navig import console_helper as ch

        ch.error(message)
        if hint:
            ch.dim(hint)
    raise typer.Exit(1)
