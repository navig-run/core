"""MCP Management Commands"""

from typing import Any

from navig import console_helper as ch


def _get_mcp_manager():
    """Lazily import and instantiate MCPManager to avoid startup cost."""
    from navig.mcp_manager import MCPManager  # noqa: PLC0415

    return MCPManager()


def search_mcp_cmd(query: str, options: dict[str, Any]):
    """Search MCP directory for servers."""
    mcp_manager = _get_mcp_manager()
    results = mcp_manager.search_directory(query)

    if not results:
        ch.warning(f"No MCP servers found matching: {query}")
        return

    # create_table takes COLUMN DICTS, not bare strings — passing strings made
    # it crash with "'str' object has no attribute 'get'", so `navig mcp search`
    # blew up the moment it found a result. (Nothing caught it because the only
    # caller was the legacy interactive shell.)
    table = ch.create_table(
        title=f"🔍 MCP Server Search Results: {query}",
        columns=[
            {"name": "Name", "style": "cyan"},
            {"name": "Type", "style": "magenta"},
            {"name": "Description"},
        ],
        show_header=True,
    )

    for server in results:
        table.add_row(server["name"], server["type"].upper(), server["description"])

    ch.print_table(table)
    ch.newline()
    ch.info("Install with: navig mcp install <name>")


def install_mcp_cmd(name: str, options: dict[str, Any]):
    """Install an MCP server."""
    if options.get("dry_run"):
        ch.dim(f"Would install MCP server: {name}")
        return

    mcp_manager = _get_mcp_manager()

    # Search directory for server details
    results = mcp_manager.search_directory(name)

    if not results:
        ch.error(f"MCP server '{name}' not found in directory")
        ch.info("Search available servers with: navig mcp search <query>")
        return

    # Use first exact match or first result
    server_info = None
    for result in results:
        if result["name"] == name:
            server_info = result
            break

    if not server_info:
        server_info = results[0]

    # Install
    mcp_manager.install_server(
        name=server_info["name"],
        package=server_info["package"],
        server_type=server_info["type"],
    )


def uninstall_mcp_cmd(name: str, options: dict[str, Any]):
    """Uninstall an MCP server."""
    if options.get("dry_run"):
        ch.dim(f"Would uninstall MCP server: {name}")
        return

    mcp_manager = _get_mcp_manager()

    if not options.get("yes"):
        # ch.confirm_action() does not exist — this raised AttributeError, so `remove`
        # crashed instead of asking. The helper is confirm_action().
        if not ch.confirm_action(f"Uninstall MCP server '{name}'?", default=False):
            ch.warning("Cancelled")
            return

    mcp_manager.uninstall_server(name)


def _server_public_dict(server) -> dict[str, Any]:
    """A secret-free, machine-readable view of a configured MCP server.

    Deliberately omits `running`/`pid`: an MCP server's process is spawned by the
    CLIENT that uses it (an editor, the agent), so from a one-shot CLI `is_running()`
    is always False — reporting it would be a lie, not a status (see `list_mcp_cmd`).
    And it exposes only the env KEY NAMES, never the values — server `env` routinely
    holds API keys (BRAVE_API_KEY, …); secrets never leave via `--json`.
    """
    cfg = server.config
    out: dict[str, Any] = {
        "name": server.name,
        "enabled": server.is_enabled(),
        "type": cfg.get("type"),
        "command": cfg.get("command"),
    }
    if cfg.get("package"):
        out["package"] = cfg["package"]
    if cfg.get("args"):
        out["args"] = cfg["args"]
    env = cfg.get("env") or {}
    if env:
        out["env_keys"] = sorted(env.keys())  # names only — never values
    return out


def list_mcp_cmd(options: dict[str, Any]):
    """List installed MCP servers."""
    mcp_manager = _get_mcp_manager()

    servers = mcp_manager.list_servers()

    if options.get("json"):
        import json

        ch.raw_print(json.dumps([_server_public_dict(s) for s in servers], indent=2))
        return

    if not servers:
        ch.warning("No MCP servers installed")
        ch.dim("Search and install servers with: navig mcp search <query>")
        return

    if options.get("plain"):
        # Plain text output - one server per line for scripting
        for server in servers:
            ch.raw_print(server.name)
        return

    # No "Running" column: an MCP server's process is spawned by the CLIENT that
    # uses it (an editor, the NAVIG agent), not by this CLI — and `is_running()`
    # only knows about a process THIS process started, so from a one-shot command
    # it is always "No". A column that can never say Yes is a lie, not a status.
    table = ch.create_table(
        title="📦 Installed MCP Servers",
        columns=[
            {"name": "Name", "style": "cyan"},
            {"name": "Type", "style": "magenta"},
            {"name": "Status"},
            {"name": "Package", "style": "dim"},
        ],
        show_header=True,
    )

    for server in servers:
        status = (
            ch.status_text("Enabled", "success")
            if server.is_enabled()
            else ch.status_text("Disabled", "dim")
        )
        table.add_row(
            server.name,
            server.config.get("type", "unknown").upper(),
            status,
            server.config.get("package") or server.config.get("command", "—"),
        )

    ch.print_table(table)
    enabled = sum(1 for s in servers if s.is_enabled())
    ch.dim(
        f"{enabled}/{len(servers)} enabled · enable one with navig mcp enable <name>"
        if enabled < len(servers)
        else f"{len(servers)} configured · all enabled"
    )


def enable_mcp_cmd(name: str, options: dict[str, Any]):
    """Enable an MCP server."""
    if options.get("dry_run"):
        ch.dim(f"Would enable MCP server: {name}")
        return

    mcp_manager = _get_mcp_manager()
    mcp_manager.enable_server(name)


def disable_mcp_cmd(name: str, options: dict[str, Any]):
    """Disable an MCP server."""
    if options.get("dry_run"):
        ch.dim(f"Would disable MCP server: {name}")
        return

    mcp_manager = _get_mcp_manager()
    mcp_manager.disable_server(name)


def start_mcp_cmd(name: str, options: dict[str, Any]):
    """Start an MCP server."""
    if options.get("dry_run"):
        ch.dim(f"Would start MCP server: {name}")
        return

    mcp_manager = _get_mcp_manager()

    if name == "all":
        mcp_manager.start_all_enabled()
    else:
        mcp_manager.start_server(name)


def stop_mcp_cmd(name: str, options: dict[str, Any]):
    """Stop an MCP server."""
    if options.get("dry_run"):
        ch.dim(f"Would stop MCP server: {name}")
        return

    mcp_manager = _get_mcp_manager()

    if name == "all":
        mcp_manager.stop_all()
    else:
        mcp_manager.stop_server(name)


def restart_mcp_cmd(name: str, options: dict[str, Any]):
    """Restart an MCP server."""
    if options.get("dry_run"):
        ch.dim(f"Would restart MCP server: {name}")
        return

    mcp_manager = _get_mcp_manager()
    mcp_manager.restart_server(name)


def status_mcp_cmd(name: str, options: dict[str, Any]):
    """Show detailed MCP server status."""
    mcp_manager = _get_mcp_manager()

    server = mcp_manager.get_server(name)
    if not server:
        if options.get("json"):
            ch.raw_print("null")  # parseable "no such server" for scripts
        else:
            ch.error(f"MCP server '{name}' not found")
        return

    if options.get("json"):
        import json

        ch.raw_print(json.dumps(_server_public_dict(server), indent=2))
        return

    status = server.get_status()

    ch.header(f"MCP Server: {status['name']}")

    # State line — enabled/disabled at a glance (glyph + colour), mirrors the hub
    # (`navig store`) aesthetic. We only surface "running" when it is affirmatively
    # true: the server's process is spawned by the CLIENT (an editor, the agent),
    # so from this one-shot CLI `is_running()` is always False — printing
    # "○ not running" unconditionally asserts a state we cannot actually know, the
    # same lie `list` refuses to show.
    state = "[green]✓ enabled[/green]" if status["enabled"] else "[dim]○ disabled[/dim]"
    if status["running"]:
        state += f"  ·  [green]● running[/green] [dim](pid {status['pid']})[/dim]"
    ch.console.print(f"  {state}")

    from rich.table import Table

    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column("field", style="dim", no_wrap=True)
    t.add_column("value")
    t.add_row("type", str(status["type"]).upper())
    t.add_row("command", str(status["command"]))
    if server.config.get("package"):
        t.add_row("package", str(server.config["package"]))
    ch.console.print(t)

    if not status["enabled"]:
        ch.dim(f"\n  enable → navig mcp enable {status['name']}")
