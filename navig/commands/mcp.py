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

    # Create table
    table = ch.create_table(
        title=f"🔍 MCP Server Search Results: {query}",
        columns=["Name", "Type", "Description"],
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
        confirm = ch.confirm(f"Uninstall MCP server '{name}'?")
        if not confirm:
            ch.warning("Cancelled")
            return

    mcp_manager.uninstall_server(name)


def list_mcp_cmd(options: dict[str, Any]):
    """List installed MCP servers."""
    mcp_manager = _get_mcp_manager()

    servers = mcp_manager.list_servers()

    if not servers:
        ch.warning("No MCP servers installed")
        ch.dim("Search and install servers with: navig mcp search <query>")
        return

    if options.get("plain"):
        # Plain text output - one server per line for scripting
        for server in servers:
            ch.raw_print(server.name)
        return

    # Create table
    table = ch.create_table(
        title="📦 Installed MCP Servers",
        columns=["Name", "Type", "Status", "Running"],
        show_header=True,
    )

    for server in servers:
        status = (
            ch.status_text("Enabled", "success")
            if server.is_enabled()
            else ch.status_text("Disabled", "dim")
        )
        running = (
            ch.status_text("Yes", "success") if server.is_running() else ch.status_text("No", "dim")
        )

        table.add_row(server.name, server.config.get("type", "unknown").upper(), status, running)

    ch.print_table(table)


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
        ch.error(f"MCP server '{name}' not found")
        return

    status = server.get_status()

    # Header
    ch.header(f"MCP Server: {status['name']}")
    ch.newline()

    # Status details
    ch.info(f"Type: {status['type'].upper()}")
    ch.info(f"Command: {status['command']}")

    enabled_status = (
        ch.status_text("Enabled", "success")
        if status["enabled"]
        else ch.status_text("Disabled", "dim")
    )
    ch.info(f"Status: {enabled_status}")

    if status["running"]:
        ch.success(f"✓ Running (PID: {status['pid']})")
    else:
        ch.warning("○ Not running")
