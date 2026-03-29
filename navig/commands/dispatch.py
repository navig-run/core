"""navig dispatch / contacts / ct — multi-network reliable message dispatch."""

from pathlib import Path

import typer

dispatch_app = typer.Typer(help="Multi-network message dispatch", no_args_is_help=True)
contacts_app = typer.Typer(help="Manage contacts and address book", no_args_is_help=True)


@dispatch_app.command("send")
def dispatch_send(
    message: str = typer.Argument(..., help="Message to dispatch"),
    channel: str = typer.Option("telegram", "--channel", "-c", help="Channel (telegram|matrix|email)"),
):
    """Send a message via the configured dispatch channel."""
    from navig import console_helper as ch

    ch.warn("navig dispatch is not yet implemented in this build.")


@contacts_app.command("list")
def contacts_list(limit: int = typer.Option(200, "--limit", "-n", help="Max rows to show")):
    """List saved contacts."""
    from rich.console import Console
    from rich.table import Table

    from navig import console_helper as ch
    from navig.comms.contacts_store import get_contacts_store

    rows = get_contacts_store().list_all(limit=limit)
    if not rows:
        ch.warn("No contacts found.")
        return

    table = Table(title="NAVIG Contacts")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Phone", style="green")
    table.add_column("Source", style="magenta")
    for row in rows:
        table.add_row(str(row.get("id", "")), str(row.get("name", "")), str(row.get("phone") or "—"), str(row.get("source") or "—"))
    Console().print(table)


@contacts_app.command("add")
def contacts_add(
    name: str = typer.Argument(..., help="Contact name"),
    phone: str | None = typer.Option(None, "--phone", "-p", help="Phone number"),
):
    """Add a contact."""
    from navig import console_helper as ch
    from navig.comms.contacts_store import get_contacts_store, normalize_phone

    normalized_phone = normalize_phone(phone)
    contact_id = get_contacts_store().add(name=name, phone=normalized_phone or None, source="manual")
    ch.success(f"Contact added: {contact_id}")


@contacts_app.command("import")
def contacts_import(path: str = typer.Argument(..., help="Path to Telegram contacts.json or export zip")):
    """Import contacts from Telegram Desktop export."""
    from navig import console_helper as ch
    from navig.comms.contacts_store import get_contacts_store, normalize_phone
    from navig.importers.core import UniversalImporter

    target = Path(path)
    if not target.exists():
        ch.error(f"File not found: {path}")
        raise typer.Exit(1)

    imported = UniversalImporter().run_one("telegram", path=str(target))
    if not imported:
        ch.warn("No Telegram contacts imported.")
        return

    store = get_contacts_store()
    added = 0
    skipped = 0
    for item in imported:
        phone = normalize_phone(item.value)
        if phone and store.find_by_phone(phone):
            skipped += 1
            continue
        store.add(name=item.label, phone=phone or None, source="telegram")
        added += 1

    ch.success(f"Contacts import complete: {added} added, {skipped} duplicates skipped")
