"""navig mcp — Model Context Protocol server management."""
import typer

mcp_app = typer.Typer(help="Manage MCP (Model Context Protocol) servers", no_args_is_help=True)


@mcp_app.command("list")
def mcp_list():
    """List configured MCP servers."""
    from navig.mcp_manager import MCPManager

    manager = MCPManager()
    servers = manager.list_servers() if hasattr(manager, "list_servers") else []
    if not servers:
        from navig import console_helper as ch

        ch.info("No MCP servers configured.")
        return
    for s in servers:
        typer.echo(f"  {s}")


@mcp_app.command("start")
def mcp_start(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8765, "--port", "-p", help="Bind port"),
):
    """Start the NAVIG MCP server."""
    from navig import console_helper as ch

    ch.info(f"Starting MCP server on {host}:{port} …")
    try:
        from navig.mcp_server import run_server

        run_server(host=host, port=port)
    except ImportError as exc:
        ch.error(f"MCP server unavailable: {exc}")
        raise typer.Exit(1) from exc


@mcp_app.command("status")
def mcp_status():
    """Show MCP server status."""
    from navig import console_helper as ch

    ch.warn("navig mcp status is not yet implemented in this build.")
