"""`navig missions` — the operator's handle on distributed work.

One verb today, and it is the one that was missing: ``work``. It claims a queued Mission
addressed to this machine, runs it, and finishes it with a signed receipt. On the brain the
store is local; anywhere else the brain is reached over the runtime API it already serves.

The verb exists because a Mission whose ``node_id`` is recorded but never claimed is not
distributed work — it is a row in a table. ``runtime`` is HTTP-only by design (there is no
``navig runtime`` CLI), so this is where a node acts on that API.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time

import typer

missions_app = typer.Typer(
    name="missions",
    help="Work the distributed-mission queue for this machine.",
    no_args_is_help=True,
)


def _config_value(key: str, default: str = "") -> str:
    """Read one config key, or ``default`` — never raise on a missing/failed config."""
    try:
        from navig.config import ConfigManager

        value = ConfigManager().get(key, "")
    except Exception:  # noqa: BLE001 - a config problem must not break the worker
        return default
    text = str(value or "").strip()
    return text or default


def _transport(url: str, token: str):
    from navig.missions.claimer import HttpTransport, StoreTransport

    if url:
        return HttpTransport(url, token)
    from navig.contracts.store import get_runtime_store

    return StoreTransport(get_runtime_store())


@missions_app.command("work")
def missions_work(
    node_id: str = typer.Option(
        "",
        "--node-id",
        help="Node to claim for. Defaults to this machine's hostname.",
    ),
    url: str = typer.Option(
        "",
        "--url",
        help="Brain base URL (e.g. http://127.0.0.1:8789). Empty reads this install's store.",
    ),
    token: str = typer.Option(
        "",
        "--token",
        help="Bearer token. Empty reads gateway.auth.token.",
    ),
    once: bool = typer.Option(
        True,
        "--once/--loop",
        help="Claim at most one mission (default), or keep polling.",
    ),
    interval: float = typer.Option(30.0, "--interval", help="Seconds between polls with --loop."),
    json_output: bool = typer.Option(False, "--json", help="Print one JSON object per poll."),
) -> None:
    """Claim a queued mission for this node, run it, and finish it with a receipt."""
    from navig.missions.claimer import MissionClaimer, known_capabilities

    resolved_node = node_id or socket.gethostname()
    resolved_url = url or _config_value("remote_dev.brain_url")
    resolved_token = token or _config_value("gateway.auth.token")
    claimer = MissionClaimer(_transport(resolved_url, resolved_token), node_id=resolved_node)

    if not json_output:
        from navig import console_helper as ch

        where = resolved_url or "this install's runtime store"
        ch.dim(
            f"claiming as [bold]{resolved_node}[/bold] from {where} · "
            f"capabilities: {', '.join(known_capabilities())}"
        )

    while True:
        outcome = asyncio.run(claimer.run_once())
        if json_output:
            typer.echo(json.dumps(outcome.as_dict(), default=str))
        elif outcome.succeeded or outcome.idle:
            _report(outcome, resolved_node)
        else:
            # The failure line belongs to the code path that also exits: a helper that prints
            # an error and returns leaves the shell seeing success
            # (tests/quality/test_command_exit_honesty.py refuses exactly that).
            _report(outcome, resolved_node)
            from navig import console_helper as ch

            ch.error(f"{outcome.mission_id} closed as failed — {outcome.error}")
            ch.dim("next: fix the capability on this node, or reassign the mission")
        if once:
            raise typer.Exit(0 if outcome.succeeded or outcome.idle else 1)
        time.sleep(max(1.0, interval))


def _report(outcome, node_id: str) -> None:  # noqa: ANN001 - ClaimOutcome
    from navig import console_helper as ch
    from navig.console_helper import Table

    console = ch.console
    if outcome.idle:
        ch.dim(f"○ no queued mission for {node_id} — nothing to do")
        return

    verdict = (
        "[green]● succeeded[/green]" if outcome.succeeded else "[red]✗ failed[/red]"
    )
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Field", style="dim", no_wrap=True)
    table.add_column("Value")
    table.add_row("Mission", outcome.mission_id)
    table.add_row("Capability", outcome.capability or "[dim]—[/dim]")
    table.add_row("Verdict", verdict)
    table.add_row("Receipt", outcome.receipt_id or "[dim]none[/dim]")
    if outcome.error:
        table.add_row("Error", f"[red]{outcome.error}[/red]")
    console.print(table)

    if outcome.succeeded:
        ch.success(f"completed {outcome.mission_id} with receipt {outcome.receipt_id}")
        ch.dim("next: `navig missions work --loop` keeps this machine available for work")
