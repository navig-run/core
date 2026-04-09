"""navig dispatch / contacts / ct — unified multi-network message dispatch.

``dispatch_app``  — ``navig dispatch send / status / threads``
``contacts_app``  — ``navig contacts list / add / show / remove / route / import``
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from navig.console_helper import get_console

dispatch_app = typer.Typer(help="Multi-network message dispatch", no_args_is_help=True)
contacts_app = typer.Typer(help="Manage contacts and address book", no_args_is_help=True)


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
        from navig.messaging.adapter import DeliveryReceipt
        from navig.messaging.adapter_registry import get_adapter_registry
        from navig.messaging.delivery import get_delivery_tracker
        from navig.messaging.routing import NoRouteError, RoutingEngine
        from navig.store.contacts import get_contact_store
        from navig.store.threads import get_thread_store

        engine = RoutingEngine(get_contact_store(), get_thread_store(), get_adapter_registry())
        try:
            decision = engine.resolve(target, network=network)
        except NoRouteError as exc:
            ch.error(str(exc))
            raise typer.Exit(1) from exc

        adapter = get_adapter_registry().get(decision.adapter_name)
        if adapter is None:
            ch.error(f"Adapter '{decision.adapter_name}' is not available.")
            raise typer.Exit(1)

        tracker = get_delivery_tracker()
        delivery_id = tracker.record_send(
            adapter=decision.adapter_name,
            target=decision.resolved_target.address,
            contact_alias=decision.resolved_target.display_hint or None,
            compliance=decision.compliance_mode,
        )

        thread = await adapter.get_or_create_thread(
            f"{decision.adapter_name}:{decision.resolved_target.address}"
        )
        receipt: DeliveryReceipt = await adapter.send_message(
            thread.remote_conversation_id, message
        )
        tracker.apply_receipt(delivery_id, receipt)

        if json_output:
            import json as _json

            ch.print(
                _json.dumps(
                    {
                        "ok": receipt.ok,
                        "status": receipt.status.value if receipt.status else None,
                        "message_id": receipt.message_id,
                        "error": receipt.error,
                        "adapter": decision.adapter_name,
                    }
                )
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
    from rich.console import Console
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
    from rich.console import Console
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


# ── contacts list ─────────────────────────────────────────────


@contacts_app.command("list")
def contacts_list(
    limit: int = typer.Option(200, "--limit", "-n", help="Maximum contacts to display"),
    plain: bool = typer.Option(False, "--plain", help="Tab-separated output"),
):
    """List saved contacts."""
    from rich.console import Console
    from rich.table import Table

    from navig import console_helper as ch
    from navig.store.contacts import get_contact_store

    store = get_contact_store()
    contacts = store.list_contacts(limit=limit)

    if not contacts:
        ch.warn("No contacts found.")
        return

    if plain:
        for c in contacts:
            nets = ",".join(r.network for r in c.routes)
            ch.print(f"{c.alias}\t{c.display_name or ''}\t{nets}")
        return

    table = Table(title="NAVIG Contacts")
    table.add_column("Alias", style="cyan")
    table.add_column("Name")
    table.add_column("Default", style="green")
    table.add_column("Routes", style="magenta")
    for c in contacts:
        nets = ", ".join(f"{r.network}:{r.address}" for r in c.routes) or "—"
        table.add_row(
            f"@{c.alias}",
            c.display_name or "—",
            c.default_network or "auto",
            nets,
        )
    get_console().print(table)


# ── contacts add ──────────────────────────────────────────────


@contacts_app.command("add")
def contacts_add(
    alias: str = typer.Option(..., "--alias", "-a", help="Unique contact alias"),
    name: str | None = typer.Option(None, "--name", "-N", help="Display name"),
    route: list[str] | None = typer.Option(
        None, "--route", "-r", help="network:address (repeatable)"
    ),
    default_network: str | None = typer.Option(None, "--default", "-d", help="Default network"),
    phone: str | None = typer.Option(
        None, "--phone", "-p", help="Phone number (alias for --route sms:<phone>)"
    ),
):
    """Add a contact to the address book.

    Examples:
        navig contacts add --alias alice --name 'Alice B.' --route 'whatsapp:+33612345678'
        navig contacts add --alias bob --phone +1234567890 --route 'discord:bob#1234'
    """
    from navig import console_helper as ch
    from navig.store.contacts import get_contact_store

    store = get_contact_store()
    alias_clean = alias.lstrip("@")

    # Check for duplicate
    if store.resolve_alias(alias_clean) is not None:
        ch.error(f"Contact @{alias_clean} already exists.")
        raise typer.Exit(1)

    # Parse routes
    parsed_routes: list[tuple[str, str]] = []
    for r in route or []:
        if ":" not in r:
            ch.error(f"Invalid route format '{r}' — expected 'network:address'.")
            raise typer.Exit(1)
        net, addr = r.split(":", 1)
        parsed_routes.append((net.strip(), addr.strip()))

    if phone:
        parsed_routes.append(("sms", phone.strip()))

    route_strings = [f"{net}:{addr}" for net, addr in parsed_routes]
    contact = store.add_contact(
        alias=alias_clean,
        display_name=name or "",
        routes=route_strings,
        default_network=default_network,
    )

    ch.success(f"Contact @{contact.alias} added ({len(parsed_routes)} routes)")


# ── contacts show ─────────────────────────────────────────────


@contacts_app.command("show")
def contacts_show(
    alias: str = typer.Argument(..., help="Contact alias"),
):
    """Show full contact details."""
    from navig import console_helper as ch
    from navig.store.contacts import get_contact_store

    contact = get_contact_store().resolve_alias(alias.lstrip("@"))
    if contact is None:
        ch.error(f"Contact @{alias.lstrip('@')} not found.")
        raise typer.Exit(1)

    ch.print(f"  Alias:   @{contact.alias}")
    ch.print(f"  Name:    {contact.display_name or '(none)'}")
    ch.print(f"  Default: {contact.default_network or '(auto)'}")
    for r in contact.routes:
        ch.print(f"  Route:   {r.network}:{r.address}  (priority={r.priority})")
    if contact.fallbacks:
        ch.print(f"  Fallbacks: {', '.join(contact.fallbacks)}")


# ── contacts remove ───────────────────────────────────────────


@contacts_app.command("remove")
def contacts_remove(
    alias: str = typer.Argument(..., help="Contact alias"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Remove a contact from the address book."""
    from navig import console_helper as ch
    from navig.store.contacts import get_contact_store

    store = get_contact_store()
    alias_clean = alias.lstrip("@")
    contact = store.resolve_alias(alias_clean)
    if contact is None:
        ch.error(f"Contact @{alias_clean} not found.")
        raise typer.Exit(1)

    if not yes:
        confirm = typer.confirm(f"Remove @{alias_clean}?")
        if not confirm:
            ch.dim("Cancelled.")
            return

    store.remove_contact(alias_clean)
    ch.success(f"Contact @{alias_clean} removed.")


# ── contacts route ────────────────────────────────────────────


@contacts_app.command("route")
def contacts_route(
    alias: str = typer.Argument(..., help="Contact alias"),
    action: str = typer.Argument(..., help="add | remove"),
    route_spec: str = typer.Argument(..., help="network:address"),
    priority: int = typer.Option(0, "--priority", "-p", help="Route priority"),
):
    """Add or remove a route for a contact.

    Examples:
        navig contacts route @alice add whatsapp:+33612345678
        navig contacts route @alice remove sms:+33612345678
    """
    from navig import console_helper as ch
    from navig.store.contacts import get_contact_store

    store = get_contact_store()
    alias_clean = alias.lstrip("@")
    contact = store.resolve_alias(alias_clean)
    if contact is None:
        ch.error(f"Contact @{alias_clean} not found.")
        raise typer.Exit(1)

    if ":" not in route_spec:
        ch.error("Route must be 'network:address' (e.g., 'whatsapp:+33612345678').")
        raise typer.Exit(1)

    net, addr = route_spec.split(":", 1)

    if action == "add":
        store.add_route(alias_clean, f"{net.strip()}:{addr.strip()}", priority=priority)
        ch.success(f"Route {net}:{addr} added to @{alias_clean}.")
    elif action == "remove":
        store.remove_route(alias_clean, f"{net.strip()}:{addr.strip()}")
        ch.success(f"Route {net}:{addr} removed from @{alias_clean}.")
    else:
        ch.error(f"Unknown action '{action}' — use 'add' or 'remove'.")
        raise typer.Exit(1)


# ── contacts import ───────────────────────────────────────────


@contacts_app.command("import")
def contacts_import(
    path: str = typer.Argument(..., help="Path to Telegram contacts.json or export ZIP"),
):
    """Import contacts from Telegram Desktop export."""
    from navig import console_helper as ch
    from navig.importers.core import UniversalImporter
    from navig.store.contacts import get_contact_store, normalize_phone

    target = Path(path)
    if not target.exists():
        ch.error(f"File not found: {path}")
        raise typer.Exit(1)

    imported = UniversalImporter().run_one("telegram", path=str(target))
    if not imported:
        ch.warn("No Telegram contacts imported.")
        return

    store = get_contact_store()
    added = 0
    skipped = 0

    for item in imported:
        phone = normalize_phone(item.value)
        alias_candidate = item.label.lower().replace(" ", "_")[:32]
        if store.resolve_alias(alias_candidate) is not None:
            skipped += 1
            continue

        routes = [f"sms:{phone}"] if phone else []
        store.add_contact(alias=alias_candidate, display_name=item.label, routes=routes)
        added += 1

    ch.success(f"Contacts import complete: {added} added, {skipped} duplicates skipped")
