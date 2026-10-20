"""``navig receipt`` — create, list and verify signed ExecutionReceipts from the CLI.

Receipts were only ever produced as a side effect of a mission or a block run;
``receipt record`` is the direct path for a human- or agent-made verdict (a
review sign-off, an audit outcome) to become the same durable, Ed25519-signed,
append-only artifact — verifiable later with ``navig receipt verify`` or
``navig block verify-receipt``.

Signing is NOT best-effort here: a sign-off that cannot be signed is refused
rather than recorded unsigned. ``outcome`` stays the four-value
``ReceiptOutcome`` enum (verdict detail belongs in ``--metadata``, not in a new
enum member nobody else understands).
"""

from __future__ import annotations

import fnmatch
import json
from typing import Any

import typer

from navig import console_helper as ch

receipt_app = typer.Typer(
    help="Sign, list and verify execution receipts (durable verdicts).",
    no_args_is_help=True,
)

_OUTCOMES = ("succeeded", "failed", "cancelled", "timed_out")


def _parse_metadata(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        ch.error(" --metadata is not valid JSON.", str(exc))
        raise typer.Exit(1) from exc
    if not isinstance(data, dict):
        ch.error(" --metadata must be a JSON object.", 'e.g. \'{"verdict": "approve"}\'')
        raise typer.Exit(1)
    return data


@receipt_app.command("record")
def record_cmd(
    mission_id: str = typer.Option(..., "--mission-id", help="Reference, e.g. review:<task-slug>:<sha12>."),
    title: str = typer.Option(..., "--title", help="Human title (what was judged)."),
    capability: str = typer.Option(..., "--capability", help="What kind of act, e.g. review_signoff."),
    outcome: str = typer.Option("succeeded", "--outcome", help="succeeded | failed | cancelled | timed_out."),
    metadata: str = typer.Option("{}", "--metadata", help="JSON object: the payload of the verdict."),
    node_id: str = typer.Option("local", "--node-id", help="Who produced it (a review: claude:<session>)."),
    error: str = typer.Option(None, "--error", help="Failure detail (failed/cancelled/timed_out)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Record a signed receipt — refuses to persist one whose signature failed."""
    from navig.contracts.execution_receipt import (  # noqa: PLC0415
        ExecutionReceipt,
        ReceiptOutcome,
        sign_receipt,
    )
    from navig.contracts.store import get_runtime_store  # noqa: PLC0415
    from navig.core.dict_utils import now_iso  # noqa: PLC0415

    if outcome not in _OUTCOMES:
        ch.error(f"Unknown outcome {outcome!r}.", f"Outcomes: {', '.join(_OUTCOMES)}")
        raise typer.Exit(1)
    meta = _parse_metadata(metadata)

    receipt = ExecutionReceipt(
        mission_id=mission_id,
        node_id=node_id,
        title=title,
        capability=capability,
        outcome=ReceiptOutcome(outcome),
        completed_at=now_iso(),
        error=error,
        metadata=meta,
    )
    if not sign_receipt(receipt):
        ch.error(
            "Could not sign the receipt — nothing was recorded.",
            "An unsigned verdict would not be a verdict (device key unavailable).",
        )
        raise typer.Exit(1)

    store = get_runtime_store()
    store.record_receipt(receipt)
    store.flush()

    if as_json:
        ch.emit_json({
            "receipt_id": receipt.receipt_id,
            "mission_id": receipt.mission_id,
            "capability": receipt.capability,
            "outcome": receipt.outcome.value,
            "signature": "valid",
            "metadata": receipt.metadata,
        })
        return
    ch.success(f"Signed receipt {receipt.receipt_id}", f"{capability} · {outcome}")
    ch.dim(f"  verify: navig receipt verify {receipt.receipt_id}")


@receipt_app.command("list")
def list_cmd(
    mission_id: str = typer.Option(None, "--mission-id", help="Filter by mission id (supports * globs)."),
    limit: int = typer.Option(20, "--limit", "-n", help="Newest first."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List stored receipts, newest first."""
    from navig.contracts.store import get_runtime_store  # noqa: PLC0415

    rows = get_runtime_store().list_receipts(limit=max(1, int(limit)))
    if mission_id:
        rows = [r for r in rows if fnmatch.fnmatch(r.mission_id, mission_id)]

    if as_json:
        ch.emit_json({
            "receipts": [
                {
                    "receipt_id": r.receipt_id,
                    "mission_id": r.mission_id,
                    "node_id": r.node_id,
                    "capability": r.capability,
                    "outcome": r.outcome.value,
                    "recorded_at": r.recorded_at,
                    "metadata": r.metadata,
                }
                for r in rows
            ],
            "count": len(rows),
        })
        return

    if not rows:
        ch.info("No receipts match.")
        return
    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Receipt", style="dim", no_wrap=True)
    table.add_column("Mission", no_wrap=True)
    table.add_column("Outcome", no_wrap=True)
    table.add_column("Recorded", no_wrap=True)
    table.add_column("Title")
    for r in rows:
        table.add_row(
            r.receipt_id[:8],
            r.mission_id,
            r.outcome.value,
            str(r.recorded_at or ""),
            str(r.title or ""),
        )
    ch.console.print(table)
    ch.dim(f"{len(rows)} receipt(s) · verify: navig receipt verify <id>")


@receipt_app.command("verify")
def verify_cmd(
    receipt_id: str = typer.Argument(..., help="Receipt id (from record/list)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Verify a stored receipt's device signature — the verdict's tamper check."""
    from navig.contracts.store import get_runtime_store  # noqa: PLC0415
    from navig.license.device_keys import verify_receipt_dict  # noqa: PLC0415

    receipt = get_runtime_store().get_receipt(receipt_id)
    if receipt is None:
        # An exact id misses; fall back to a unique prefix — ids are uuids, and
        # listing by eye then pasting 32 chars is where typos live.
        hits = [
            r for r in get_runtime_store().list_receipts(limit=500)
            if r.receipt_id.startswith(receipt_id)
        ]
        if len(hits) == 1:
            receipt = hits[0]
        else:
            if as_json:
                ch.emit_json({
                    "error": "receipt not found",
                    "receipt_id": receipt_id,
                    "matches": len(hits),
                })
            else:
                ch.error(f"Receipt {receipt_id!r} not found." + (" (ambiguous prefix)" if hits else ""))
            raise typer.Exit(1)

    ok, reason = verify_receipt_dict(receipt.to_dict())
    signature = "valid" if ok is True else "unsigned" if ok is None else "invalid"
    payload = {
        "receipt_id": receipt.receipt_id,
        "mission_id": receipt.mission_id,
        "capability": receipt.capability,
        "outcome": receipt.outcome.value,
        "signature": signature,
        "reason": reason,
        "verdict": (receipt.metadata or {}).get("verdict"),
    }
    if as_json:
        ch.emit_json(payload)
    else:
        mark = "[green]valid[/green]" if signature == "valid" else f"[red]{signature}[/red]"
        ch.console.print(f"{receipt.receipt_id}  {mark}  ({reason})")
        if payload["verdict"]:
            ch.console.print(f"  verdict: {payload['verdict']}")

    # Exit honesty: an agent gating a landing on this command needs the verdict
    # as an exit code, not as something to parse out of prose.
    raise typer.Exit(0 if signature == "valid" else 1)
