"""navig dispatch / contacts / ct — unified multi-network message dispatch.

``dispatch_app``  — ``navig dispatch send / status / threads``
``navig contacts`` lives in the navig-contacts plugin: the address book and the
routing table are one store now, and dispatch resolves aliases through it.
"""

from __future__ import annotations

import asyncio

import typer

from navig.console_helper import get_console

dispatch_app = typer.Typer(help="Multi-network message dispatch", no_args_is_help=True)


# ── dispatch send ─────────────────────────────────────────────


@dispatch_app.command("send")
def dispatch_send(
    target: str = typer.Argument(..., help="Contact alias (@alice) or network:address (sms:+1234)"),
    message: str = typer.Argument(..., help="Message text to send"),
    network: str | None = typer.Option(
        None, "--network", "-n", help="Force network (sms, whatsapp, discord, telegram)"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Send a message through the unified messaging layer."""
    from navig import console_helper as ch

    async def _send() -> None:
        from navig.messaging.routing import NoRouteError
        from navig.messaging.send import AdapterUnavailableError, route_and_send

        try:
            decision, receipt = await route_and_send(target, message, network=network)
        except (NoRouteError, AdapterUnavailableError) as exc:
            ch.error(str(exc))
            raise typer.Exit(1) from exc

        if json_output:
            ch.emit_json(
                {
                    "ok": receipt.ok,
                    "status": receipt.status.value if receipt.status else None,
                    "message_id": receipt.message_id,
                    "error": receipt.error,
                    "adapter": decision.adapter_name,
                },
                indent=None,
            )
        elif receipt.ok:
            ch.success(f"Sent via {decision.adapter_name} — id={receipt.message_id}")
        else:
            ch.error(f"Send failed: {receipt.error}")
            raise typer.Exit(1)

    asyncio.run(_send())


# ── dispatch status ───────────────────────────────────────────


@dispatch_app.command("status")
def dispatch_status(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of recent deliveries"),
):
    """Show recent delivery statuses."""
    from rich.table import Table

    from navig.messaging.delivery import get_delivery_tracker

    tracker = get_delivery_tracker()
    rows = tracker.recent(limit=limit)

    if not rows:
        get_console().print("[dim]No deliveries recorded.[/dim]")
        return

    table = Table(title="Recent Deliveries")
    table.add_column("ID", style="cyan")
    table.add_column("Adapter")
    table.add_column("Target")
    table.add_column("Contact")
    table.add_column("Status", style="green")
    table.add_column("Sent", style="dim")
    for r in rows:
        table.add_row(
            str(r.get("id", "")),
            r.get("adapter", ""),
            r.get("target", ""),
            r.get("contact_alias") or "—",
            r.get("status", ""),
            r.get("created_at", ""),
        )
    get_console().print(table)


# ── dispatch threads ──────────────────────────────────────────


@dispatch_app.command("threads")
def dispatch_threads(
    adapter: str | None = typer.Option(None, "--adapter", "-a", help="Filter by adapter"),
    limit: int = typer.Option(20, "--limit", "-n"),
):
    """List active conversation threads."""
    from rich.table import Table

    from navig.store.threads import get_thread_store

    threads = get_thread_store().list_threads(adapter=adapter, limit=limit)
    if not threads:
        get_console().print("[dim]No threads.[/dim]")
        return

    table = Table(title="Threads")
    table.add_column("ID", style="cyan")
    table.add_column("Adapter")
    table.add_column("Remote ID")
    table.add_column("Contact")
    table.add_column("Status", style="green")
    table.add_column("Last Active", style="dim")
    for t in threads:
        table.add_row(
            str(t.id),
            t.adapter,
            t.remote_conversation_id,
            t.contact_alias or "—",
            t.status,
            str(t.last_active),
        )
    get_console().print(table)
