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

import logging

import typer

from navig import console_helper as ch
from navig.commands._async_utils import run_sync as _run

logger = logging.getLogger("navig.connectors.cli")

connector_app = typer.Typer(
    name="connector",
    help="Manage service connectors (Gmail, Calendar, …). AI providers → `navig connect`.",
    no_args_is_help=True,
)


# ── list ─────────────────────────────────────────────────────────────────


@connector_app.command("list")
def connector_list(
    domain: str | None = typer.Option(None, "--domain", "-d", help="Filter by domain"),
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
            raise typer.Exit(1) from None
        connectors = registry.list_by_domain(d)
    else:
        connectors = registry.list_all()

    # registry.list_all() derives status from in-process instances, of which a fresh
    # CLI process has none — so every row read "disconnected" even for linked
    # accounts. Overlay the vault, the durable truth, before rendering *or* emitting.
    linked = _vault_connected_ids()
    for c in connectors:
        if c["id"] in linked and c["status"] == "disconnected":
            c["status"] = "connected"
            c["account"] = linked[c["id"]]

    if json_output:
        ch.emit_json(connectors)
        return

    if not connectors:
        ch.warning("No connectors registered.")
        return

    from rich.table import Table

    table = Table(title="Connectors", show_lines=False)
    # Column discipline: no_wrap on the short, known-width columns so a narrow
    # terminal never truncates a connector id — Name/Account absorb the wrapping.
    table.add_column("", width=3)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Domain", style="dim", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Account", style="dim")

    for c in connectors:
        status_val = c["status"]
        status_style = {
            "connected": "[green]connected[/green]",
            "disconnected": "[dim]disconnected[/dim]",
            "degraded": "[yellow]degraded[/yellow]",
            "error": "[red]error[/red]",
            "connecting": "[blue]connecting…[/blue]",
        }.get(status_val, status_val)

        table.add_row(
            c["icon"],
            c["id"],
            c["display_name"],
            c["domain"],
            status_style,
            c.get("account") or "",
        )

    ch.console.print(table)
    connected_n = sum(1 for c in connectors if c["status"] == "connected")
    ch.dim(
        f"{connected_n}/{len(connectors)} connected"
        + ("" if connected_n else " · connect one with navig connector connect <id>")
    )


# ── catalog ──────────────────────────────────────────────────────────────


@connector_app.command("catalog")
def connector_catalog(
    batch: int | None = typer.Option(None, "--batch", "-b", help="Only this batch (1 = most important)"),
    category: str | None = typer.Option(None, "--category", "-c", help="Category id, e.g. developer"),
    country: str | None = typer.Option(None, "--country", help="ISO country code, e.g. FR"),
    region: str | None = typer.Option(None, "--region", help="north-america · europe · asia-pacific · …"),
    tier: str | None = typer.Option(None, "--tier", help="navig · official · desktop · community"),
    status: str | None = typer.Option(None, "--status", "-s", help="planned · verified · live"),
    search: str | None = typer.Option(None, "--search", "-q", help="Match name or vendor"),
    limit: int = typer.Option(50, "--limit", "-n", help="Rows to show (0 = all)"),
    summary_only: bool = typer.Option(False, "--summary", help="Counts per status, tier, category, region"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Every service NAVIG knows about, ranked into batches of 25.

    The catalog is the to-do list for new connectors: resolve one batch at a time
    (verify how each service integrates, then wire it). `--summary` shows the next batch.
    """
    from navig.connectors import catalog

    for label, value, allowed in (
        ("category", category, catalog.CATEGORY_IDS),
        ("tier", tier, catalog.TIERS),
        ("status", status, catalog.STATUSES),
        ("region", region, catalog.REGION_IDS),
    ):
        if value and value not in allowed:
            ch.error(f"Unknown {label} {value!r} — one of: {', '.join(sorted(allowed))}")
            raise typer.Exit(1)

    try:
        rows = catalog.query(
            category=category, country=country, region=region, tier=tier,
            status=status, batch=batch, text=search,
        )
    except catalog.CatalogError as exc:
        ch.error(f"The connector catalog is malformed: {exc}")
        raise typer.Exit(1) from None

    if summary_only:
        s = catalog.summary(rows)
        if json_output:
            ch.emit_json(s)
            return
        _print_catalog_summary(s)
        return

    shown = rows if limit <= 0 else rows[:limit]
    if json_output:
        ch.emit_json([e.to_dict() for e in shown])
        return
    if not rows:
        ch.warning("No catalog entries match those filters.")
        raise typer.Exit(1)

    from navig.console_helper import Table

    # Below ~100 columns the fixed columns alone overflow, and Rich then collapses Name —
    # the one column that matters. Narrow terminals get the category as its icon only.
    compact = ch.console.width < 100
    labels = {c.id: c.icon if compact else f"{c.icon} {c.label}" for c in catalog.CATEGORIES}
    table = Table(box=None, show_header=True, padding=(0, 2 if not compact else 1))
    table.add_column("#", style="dim", justify="right", no_wrap=True)
    table.add_column("Batch", style="dim", justify="right", no_wrap=True)
    # Name is the one wrappable column, but never squeezed away: at 80 columns Rich
    # shrank it to zero width and the listing showed batches of nameless rows.
    table.add_column("Name", min_width=14, ratio=1)
    table.add_column("Category", no_wrap=True)
    table.add_column("Country", style="dim", no_wrap=True)
    table.add_column("Via", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    status_style = {
        "live": "[green]● live[/green]",
        "verified": "[cyan]◐ verified[/cyan]",
        "planned": "[dim]○ planned[/dim]",
    }
    for e in shown:
        table.add_row(
            str(e.rank), str(e.batch), e.name, labels.get(e.category, e.category),
            e.country or "—", _via(e), status_style.get(e.status, e.status),
        )
    ch.console.print(table)
    more = f" · showing {len(shown)} (--limit 0 for all)" if len(shown) < len(rows) else ""
    live = sum(1 for e in rows if e.status == "live")
    ch.dim(f"{len(rows)} entries · {live} live{more} · next: navig connector catalog --summary")


def _via(e) -> str:
    """How NAVIG reaches the service — and whether the vendor must approve the app first."""
    if e.integration == "native":
        return "[green]built-in[/green]"
    if e.integration == "remote_mcp":
        return "[yellow]MCP · approval[/yellow]" if "client-approval" in e.flags else "[cyan]MCP[/cyan]"
    if e.integration:
        return e.integration.replace("_", " ")
    return "[dim]—[/dim]"


def _print_catalog_summary(s: dict) -> None:
    from navig.connectors import catalog
    from navig.console_helper import Table

    labels = {c.id: f"{c.icon} {c.label}" for c in catalog.CATEGORIES}
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Category")
    table.add_column("Entries", justify="right", no_wrap=True)
    for cid, n in sorted(s["by_category"].items(), key=lambda kv: -kv[1]):
        table.add_row(labels.get(cid, cid), str(n))
    ch.console.print(table)

    def fmt(d: dict) -> str:
        return " · ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1]))

    ch.info(f"Status   {fmt(s['by_status'])}")
    ch.info(f"Tier     {fmt(s['by_tier'])}")
    ch.info(f"Region   {fmt(s['by_region'])}")
    nxt = s["next_batch"]
    ch.dim(
        f"{s['total']} entries in {s['batches']} batches"
        + (f" · next to resolve: navig connector catalog --batch {nxt}" if nxt else " · all resolved")
    )


# ── status ───────────────────────────────────────────────────────────────


@connector_app.command("status")
def connector_status(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Show status and health of connected connectors."""
    from navig.connectors.registry import get_connector_registry

    _ensure_connectors_loaded()
    registry = get_connector_registry()
    connected = _resolve_connected(registry)

    if not connected:
        ch.dim("No connectors connected. Use `navig connector connect <id>`.")
        return

    async def _check_all():
        results = []
        for c in connected:
            # health_check() hits the real API, so it needs the vault token too.
            await _hydrate(c)
            health = await c.health_check()
            results.append((c, health))
        return results

    results = _run(_check_all())

    if json_output:
        data = []
        for c, h in results:
            data.append(
                {
                    "id": c.manifest.id,
                    "ok": h.ok,
                    "latency_ms": round(h.latency_ms, 1),
                    "message": h.message,
                }
            )
        ch.emit_json(data)
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


def _prompt_api_key_fields(connector, *, force: bool = False) -> None:
    """The CLI half of an ``api_key`` Connect: prompt for key fields → vault.

    The deck's Connect form renders the same ``key_fields``; without this the CLI
    could only ever connect an api_key connector whose key was ALREADY in the
    environment — `navig connector connect cyberaigen` would fail with "token not
    found" and nothing to type it into. Fields that already resolve (env or vault)
    are left alone — unless *force* (``--reconfigure``), which re-prompts every
    field so a stored key can be ROTATED: a secret's prompt accepts Enter to keep
    the current value, an empty answer never deletes what works.
    """
    from navig.connectors.secrets import resolve
    from navig.connectors.secrets import store as store_key

    for field in connector.manifest.key_fields:
        current = resolve(field)
        if current and not force:
            continue
        if field.secret:
            # hide_input only on a real TTY: on Windows a hidden prompt reads via
            # msvcrt and BLOCKS FOREVER when stdin is a pipe (scripted connects),
            # and a pipe echoes nothing anyway — hiding matters only at a terminal.
            import sys

            prompt = (
                f"{field.label} ({field.placeholder})"
                if not current
                else f"New {field.label} — Enter keeps the current one"
            )
            value = (
                typer.prompt(
                    prompt,
                    default="" if (field.optional or (force and current)) else None,
                    hide_input=sys.stdin.isatty(),
                )
                or ""
            ).strip()
        else:
            value = (
                typer.prompt(
                    f"{field.label}", default=current or field.placeholder or None
                )
                or ""
            ).strip()
        if not value and not field.optional and not (force and current):
            ch.error(f"{field.label} is required to connect this connector.")
            raise typer.Exit(1)
        if value:
            store_key(field, value)
            ch.info(f"Stored in the vault under '{field.vault_label}'.")
        elif force and current:
            ch.info(f"Kept the existing {field.label}.")


@connector_app.command("connect")
def connector_connect(
    connector_id: str = typer.Argument(help="Connector ID (e.g. gmail, google_calendar)"),
    account: str | None = typer.Option(
        None, "--account", help="Link a SECOND account of this connector (email); the first one is kept."
    ),
    reconfigure: bool = typer.Option(
        False,
        "--reconfigure",
        help="Re-prompt api_key fields even if already stored — how you rotate a saved key.",
    ),
) -> None:
    """Authenticate and connect a service connector."""
    from navig.connectors.auth_manager import ConnectorAuthManager
    from navig.connectors.registry import get_connector_registry

    _ensure_connectors_loaded()
    registry = get_connector_registry()

    if not registry.has(connector_id):
        ch.error(f"Unknown connector: {connector_id}")
        available = [c["id"] for c in registry.list_all()]
        if available:
            ch.dim(f"Available: {', '.join(available)}")
        raise typer.Exit(1)

    connector = registry.get(connector_id)

    ch.info(f"Connecting {connector.manifest.icon}  {connector.manifest.display_name}…")

    auth = ConnectorAuthManager()

    if connector.manifest.auth_mode == "api_key" and connector.manifest.key_fields:
        _prompt_api_key_fields(connector, force=reconfigure)

    if connector.manifest.requires_oauth:
        # Register OAuth provider config
        _register_oauth_config(connector_id, auth)

        # Run OAuth flow
        token = _run(auth.authenticate(connector_id, interactive=True, account=account))
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

    if not registry.has(connector_id):
        ch.error(f"Unknown connector: {connector_id}")
        raise typer.Exit(1)

    connector = registry.get(connector_id)

    async def _disconnect():
        await connector.disconnect()

    _run(_disconnect())
    ch.success(f"{connector.manifest.display_name} disconnected.")


# ── search ───────────────────────────────────────────────────────────────


@connector_app.command("search")
def connector_search(
    query: str = typer.Argument(help="Search query"),
    source: str | None = typer.Option(None, "--source", "-s", help="Restrict to connector ID"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Search across connected connectors."""
    from navig.connectors.registry import get_connector_registry

    _ensure_connectors_loaded()
    registry = get_connector_registry()

    if source:
        if not registry.has(source):
            ch.error(f"Connector not found: {source}")
            raise typer.Exit(1)
        connectors = [registry.get(source)]
    else:
        connectors = _resolve_connected(registry)

    if not connectors:
        ch.warning("No connectors connected. Use `navig connector connect <id>` first.")
        return

    async def _search():
        all_results = []
        for c in connectors:
            cid = c.manifest.id
            if not await _hydrate(c) and getattr(c.manifest, "requires_oauth", True):
                # Without this the connector raised "has no access token" — an opaque
                # message for what is simply a connector that needs linking.
                ch.warning(f"{cid} is not connected — run: navig connector connect {cid}")
                continue
            try:
                try:
                    results = await c.search(query, limit=limit)
                except TypeError:
                    results = await c.search(query)
                all_results.extend(results[:limit])
            except Exception as exc:
                ch.warning(f"Search failed on {cid}: {exc}")
        return all_results[:limit]

    results = _run(_search())

    if json_output:
        ch.emit_json([r.to_dict() for r in results])
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

    if not registry.has(connector_id):
        ch.error(f"Connector not found: {connector_id}")
        raise typer.Exit(1)

    connector = registry.get(connector_id)

    async def _fetch():
        if not await _hydrate(connector) and getattr(
            connector.manifest, "requires_oauth", True
        ):
            ch.error(
                f"{connector_id} is not connected — "
                f"run: navig connector connect {connector_id}"
            )
            raise typer.Exit(1)
        return await connector.fetch(resource_id)

    result = _run(_fetch())

    if result is None:
        ch.error(f"Resource not found: {resource}")
        raise typer.Exit(1)

    if json_output:
        ch.emit_json(result.to_dict())
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
    connector_id: str | None = typer.Argument(None, help="Specific connector (omit for all)"),
) -> None:
    """Run health checks on connected connectors."""
    if connector_id is not None:
        # Targeted single-connector health check
        from navig.connectors.registry import get_connector_registry
        from navig.connectors.types import ConnectorStatus

        _ensure_connectors_loaded()
        registry = get_connector_registry()
        try:
            connector = registry.get(connector_id)
        except Exception:
            ch.error(f"Unknown connector: {connector_id!r}")
            raise typer.Exit(1) from None

        # A lazily-created instance is DISCONNECTED until its vault token is loaded,
        # so this guard rejected every healthy connector in a fresh CLI process.
        # _hydrate() marks it CONNECTED on success.
        _run(_hydrate(connector))

        if connector.status not in (ConnectorStatus.CONNECTED, ConnectorStatus.DEGRADED):
            ch.dim(
                f"Connector '{connector_id}' is not connected. "
                f"Use `navig connector connect {connector_id}`."
            )
            return

        async def _check_one():
            return await connector.health_check()

        health = _run(_check_one())
        ok_str = "[green]✓ healthy[/green]" if health.ok else "[red]✗ unhealthy[/red]"
        latency_str = f"{health.latency_ms:.0f}ms"
        msg = f" — {health.message}" if health.message else ""
        ch.console.print(f"{connector.manifest.icon} {connector_id}: {ok_str} ({latency_str}){msg}")
    else:
        # All connected connectors
        connector_status(json_output=False)


# ── Connector auto-registration ──────────────────────────────────────────


def _ensure_connectors_loaded() -> None:
    """Lazily register built-in connectors on first CLI access.

    Delegates to the canonical bootstrap so the MCP server and CLI share
    the same registration logic from a single source of truth.
    """
    from navig.connectors.bootstrap import ensure_connectors_loaded

    ensure_connectors_loaded()


def _vault_connected_ids() -> dict[str, str]:
    """``{connector_id: account_email}`` from the vault — see navig.connectors.connected.

    The vault is the durable source of truth for "connected": ``registry.list_connected()``
    only sees instances this process created, so a fresh CLI process reported
    "No connectors connected" on a healthy install.
    """
    from navig.connectors.connected import vault_connected_ids

    return vault_connected_ids()


def _resolve_connected(registry) -> list:
    """Usable connector instances, vault first (shared with the agent's search_all)."""
    from navig.connectors.connected import resolve_connected

    return resolve_connected(registry)


async def _hydrate(connector) -> bool:
    """Load *connector*'s vault token into the instance (a fresh one has none)."""
    from navig.connectors.connected import hydrate

    return await hydrate(connector)


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

    else:
        ch.warning(
            f"No OAuth configuration is available for connector {connector_id!r}.\n"
            "This connector may not support the OAuth flow through `navig connector connect`."
        )
