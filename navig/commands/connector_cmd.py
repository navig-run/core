"""
navig connector — CLI surface for the Connector Engine.

Commands:
    navig connector list           List all registered connectors
    navig connector status         Show connection status + health
    navig connector connect <id>   Authenticate & connect a connector
    navig connector disconnect <id>  Disconnect a connector
    navig connector search <query> Search across connected connectors
    navig connector fetch <id>     Fetch a resource by connector:resource_id
    navig connector health         Run health checks on connected connectors
"""

from __future__ import annotations

import asyncio
from typing import Optional

import typer

from navig import console_helper as ch

connector_app = typer.Typer(
    name="connector",
    help="Manage service connectors (Gmail, Calendar, …).",
    no_args_is_help=True,
)


def _run(coro):
    """Run an async coroutine in a sync CLI context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


# ── list ─────────────────────────────────────────────────────────────────

@connector_app.command("list")
def connector_list(
    domain: Optional[str] = typer.Option(None, "--domain", "-d", help="Filter by domain"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """List all registered connectors."""
    from navig.connectors.registry import get_connector_registry

    _ensure_connectors_loaded()
    registry = get_connector_registry()

    if domain:
        from navig.connectors.types import ConnectorDomain

        try:
            d = ConnectorDomain(domain)
        except ValueError:
            ch.error(f"Unknown domain: {domain}")
            raise typer.Exit(1)
        connectors = registry.list_by_domain(d)
    else:
        connectors = registry.list_all()

    if json_output:
        import json

        data = []
        for c in connectors:
            data.append({
                "id": c.manifest.id,
                "display_name": c.manifest.display_name,
                "domain": c.manifest.domain.value,
                "status": c.status.value,
                "icon": c.manifest.icon,
            })
        ch.print(json.dumps(data, indent=2))
        return

    if not connectors:
        ch.warning("No connectors registered.")
        return

    from rich.table import Table

    table = Table(title="Connectors", show_lines=False)
    table.add_column("", width=3)
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Domain", style="dim")
    table.add_column("Status")

    for c in connectors:
        status_style = {
            "connected": "[green]connected[/green]",
            "disconnected": "[dim]disconnected[/dim]",
            "degraded": "[yellow]degraded[/yellow]",
            "error": "[red]error[/red]",
            "connecting": "[blue]connecting…[/blue]",
        }.get(c.status.value, c.status.value)

        table.add_row(
            c.manifest.icon,
            c.manifest.id,
            c.manifest.display_name,
            c.manifest.domain.value,
            status_style,
        )

    ch.console.print(table)


# ── status ───────────────────────────────────────────────────────────────

@connector_app.command("status")
def connector_status(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Show status and health of connected connectors."""
    from navig.connectors.registry import get_connector_registry

    _ensure_connectors_loaded()
    registry = get_connector_registry()
    connected = registry.list_connected()

    if not connected:
        ch.dim("No connectors connected. Use `navig connector connect <id>`.")
        return

    async def _check_all():
        results = []
        for c in connected:
            health = await c.health_check()
            results.append((c, health))
        return results

    results = _run(_check_all())

    if json_output:
        import json

        data = []
        for c, h in results:
            data.append({
                "id": c.manifest.id,
                "ok": h.ok,
                "latency_ms": round(h.latency_ms, 1),
                "message": h.message,
            })
        ch.print(json.dumps(data, indent=2))
        return

    from rich.table import Table

    table = Table(title="Connector Health", show_lines=False)
    table.add_column("", width=3)
    table.add_column("Connector", style="cyan")
    table.add_column("Status")
    table.add_column("Latency")
    table.add_column("Details", style="dim")

    for c, h in results:
        ok_str = "[green]✓ healthy[/green]" if h.ok else "[red]✗ unhealthy[/red]"
        latency_str = f"{h.latency_ms:.0f}ms"
        table.add_row(c.manifest.icon, c.manifest.id, ok_str, latency_str, h.message or "")

    ch.console.print(table)


# ── connect ──────────────────────────────────────────────────────────────

@connector_app.command("connect")
def connector_connect(
    connector_id: str = typer.Argument(help="Connector ID (e.g. gmail, google_calendar)"),
) -> None:
    """Authenticate and connect a service connector."""
    from navig.connectors.auth_manager import ConnectorAuthManager
    from navig.connectors.registry import get_connector_registry

    _ensure_connectors_loaded()
    registry = get_connector_registry()

    if not registry.has(connector_id):
        ch.error(f"Unknown connector: {connector_id}")
        available = [c.manifest.id for c in registry.list_all()]
        if available:
            ch.dim(f"Available: {', '.join(available)}")
        raise typer.Exit(1)

    connector = registry.get(connector_id)
    if not connector:
        ch.error("Failed to get connector instance.")
        raise typer.Exit(1)

    ch.info(f"Connecting {connector.manifest.icon}  {connector.manifest.display_name}…")

    auth = ConnectorAuthManager()

    if connector.manifest.requires_oauth:
        # Register OAuth provider config
        _register_oauth_config(connector_id, auth)

        # Run OAuth flow
        token = auth.authenticate(connector_id, interactive=True)
        if not token:
            ch.error("Authentication failed. No access token received.")
            raise typer.Exit(1)
        connector.set_access_token(token)

    async def _connect():
        await connector.connect()

    _run(_connect())
    ch.success(f"{connector.manifest.display_name} connected.")


# ── disconnect ───────────────────────────────────────────────────────────

@connector_app.command("disconnect")
def connector_disconnect(
    connector_id: str = typer.Argument(help="Connector ID"),
) -> None:
    """Disconnect a service connector."""
    from navig.connectors.registry import get_connector_registry

    _ensure_connectors_loaded()
    registry = get_connector_registry()

    connector = registry.get(connector_id)
    if not connector:
        ch.error(f"Unknown or not connected: {connector_id}")
        raise typer.Exit(1)

    async def _disconnect():
        await connector.disconnect()

    _run(_disconnect())
    ch.success(f"{connector.manifest.display_name} disconnected.")


# ── search ───────────────────────────────────────────────────────────────

@connector_app.command("search")
def connector_search(
    query: str = typer.Argument(help="Search query"),
    source: Optional[str] = typer.Option(None, "--source", "-s", help="Restrict to connector ID"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Search across connected connectors."""
    from navig.connectors.registry import get_connector_registry

    _ensure_connectors_loaded()
    registry = get_connector_registry()

    if source:
        connectors = []
        c = registry.get(source)
        if c:
            connectors = [c]
        else:
            ch.error(f"Connector not found or not connected: {source}")
            raise typer.Exit(1)
    else:
        connectors = registry.list_connected()

    if not connectors:
        ch.warning("No connectors connected. Use `navig connector connect <id>` first.")
        return

    async def _search():
        all_results = []
        for c in connectors:
            try:
                results = await c.search(query)
                all_results.extend(results[:limit])
            except Exception as exc:
                ch.warning(f"Search failed on {c.manifest.id}: {exc}")
        return all_results[:limit]

    results = _run(_search())

    if json_output:
        import json

        ch.print(json.dumps([r.to_dict() for r in results], indent=2))
        return

    if not results:
        ch.dim("No results found.")
        return

    from rich.table import Table

    table = Table(title=f"Search: {query}", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Source", style="cyan", width=16)
    table.add_column("Title")
    table.add_column("Preview", style="dim", max_width=40)

    for i, r in enumerate(results, 1):
        table.add_row(
            str(i),
            r.source,
            r.title[:60],
            (r.preview[:80] + "…") if len(r.preview) > 80 else r.preview,
        )

    ch.console.print(table)


# ── fetch ────────────────────────────────────────────────────────────────

@connector_app.command("fetch")
def connector_fetch(
    resource: str = typer.Argument(help="connector_id:resource_id (e.g. gmail:18f2a3b4c5)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Fetch a single resource by connector:resource_id."""
    from navig.connectors.registry import get_connector_registry

    _ensure_connectors_loaded()

    if ":" not in resource:
        ch.error("Format: <connector_id>:<resource_id>  (e.g. gmail:18f2a3b4c5)")
        raise typer.Exit(1)

    connector_id, resource_id = resource.split(":", 1)
    registry = get_connector_registry()
    connector = registry.get(connector_id)

    if not connector:
        ch.error(f"Connector not found or not connected: {connector_id}")
        raise typer.Exit(1)

    async def _fetch():
        return await connector.fetch(resource_id)

    result = _run(_fetch())

    if json_output:
        import json

        ch.print(json.dumps(result.to_dict(), indent=2))
        return

    ch.console.print(f"\n[bold]{result.title}[/bold]")
    ch.console.print(f"[dim]Source: {result.source} | ID: {result.id}[/dim]")
    if result.url:
        ch.console.print(f"[dim]URL: {result.url}[/dim]")
    if result.timestamp:
        ch.console.print(f"[dim]Time: {result.timestamp}[/dim]")
    if result.preview:
        ch.console.print(f"\n{result.preview}")
    ch.console.print()


# ── health ───────────────────────────────────────────────────────────────

@connector_app.command("health")
def connector_health(
    connector_id: Optional[str] = typer.Argument(None, help="Specific connector (omit for all)"),
) -> None:
    """Run health checks on connected connectors."""
    # Delegates to the status command with same logic
    connector_status(json_output=False)


# ── Connector auto-registration ──────────────────────────────────────────

_CONNECTORS_LOADED = False


def _ensure_connectors_loaded() -> None:
    """Lazily register built-in connectors on first CLI access."""
    global _CONNECTORS_LOADED
    if _CONNECTORS_LOADED:
        return
    _CONNECTORS_LOADED = True

    from navig.connectors.registry import get_connector_registry

    registry = get_connector_registry()

    # Gmail
    try:
        from navig.connectors.gmail.connector import GmailConnector

        registry.register(GmailConnector)
    except Exception as exc:
        import logging

        logging.getLogger("navig.connectors").debug("Gmail load failed: %s", exc)

    # Google Calendar
    try:
        from navig.connectors.google_calendar.connector import GoogleCalendarConnector

        registry.register(GoogleCalendarConnector)
    except Exception as exc:
        import logging

        logging.getLogger("navig.connectors").debug("Calendar load failed: %s", exc)


def _register_oauth_config(connector_id: str, auth) -> None:
    """Register OAuth config for known connectors."""
    import os

    if connector_id == "gmail":
        from navig.connectors.gmail.oauth_config import build_gmail_oauth_config

        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
        if not client_id:
            ch.error(
                "GOOGLE_CLIENT_ID environment variable required.\n"
                "Get credentials at: https://console.cloud.google.com/apis/credentials"
            )
            raise typer.Exit(1)
        config = build_gmail_oauth_config(client_id, client_secret)
        auth.register_provider(connector_id, config)

    elif connector_id == "google_calendar":
        from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config

        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
        if not client_id:
            ch.error(
                "GOOGLE_CLIENT_ID environment variable required.\n"
                "Get credentials at: https://console.cloud.google.com/apis/credentials"
            )
            raise typer.Exit(1)
        config = build_calendar_oauth_config(client_id, client_secret)
        auth.register_provider(connector_id, config)
