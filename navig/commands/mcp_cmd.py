"""navig mcp — Model Context Protocol server management."""

from __future__ import annotations

from enum import Enum

import typer

mcp_app = typer.Typer(help="Manage MCP (Model Context Protocol) servers", no_args_is_help=True)


class _Transport(str, Enum):
    stdio = "stdio"
    websocket = "websocket"
    http = "http"


@mcp_app.command("serve")
def mcp_serve(
    transport: _Transport = typer.Option(
        _Transport.http,
        "--transport",
        "-t",
        help="Transport: http (default, for Perplexity/web clients), websocket, stdio",
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(3001, "--port", "-p", help="Bind port (default 3001)"),
    token: str | None = typer.Option(
        None,
        "--token",
        help="Auth token. Omit to run open (HTTP) or auto-generate (WebSocket).",
    ),
    print_config: bool = typer.Option(
        False,
        "--print-config",
        help="Print connector config for Perplexity / VS Code / Claude and exit.",
    ),
):
    """Start the NAVIG MCP server.

    \b
    Examples:
      navig mcp serve                          # HTTP on http://127.0.0.1:3001/mcp
      navig mcp serve --transport http --port 8080
      navig mcp serve --transport websocket    # WebSocket on ws://localhost:3001
      navig mcp serve --transport stdio        # stdio (for VS Code / Claude Desktop)
      navig mcp serve --print-config           # Print Perplexity connector URL and exit
    """
    from navig import console_helper as ch
    from navig.mcp_server import (
        generate_claude_mcp_config,
        generate_perplexity_mcp_config,
        generate_vscode_mcp_config,
        start_mcp_server,
    )

    if print_config:
        import json

        ch.console.print("\n[bold]── Perplexity AI custom connector ──[/bold]")
        perplexity_cfg = generate_perplexity_mcp_config(host=host, port=port, token=token)
        ch.console.print(
            f"  MCP Server URL: [bold green]{perplexity_cfg['mcp_server_url']}[/bold green]"
        )
        if token:
            ch.console.print(f"  Authorization:  Bearer {token}")

        ch.console.print("\n[bold]── VS Code (mcp.json) ──[/bold]")
        ch.console.print(json.dumps(generate_vscode_mcp_config(), indent=2))

        ch.console.print("\n[bold]── Claude Desktop (claude_desktop_config.json) ──[/bold]")
        ch.console.print(json.dumps(generate_claude_mcp_config(), indent=2))
        return

    if transport == _Transport.http and not token:
        ch.dim("Tip: pass --token to require Bearer token authentication.")

    try:
        start_mcp_server(mode=transport.value, port=port, token=token, host=host)
    except (ImportError, ValueError) as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc


# ---------------------------------------------------------------------------
# Legacy / low-level sub-commands kept for backwards compatibility
# ---------------------------------------------------------------------------


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


@mcp_app.command("start", hidden=True)
def mcp_start(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(3001, "--port", "-p", help="Bind port"),
    transport: _Transport = typer.Option(_Transport.http, "--transport", "-t"),
):
    """[deprecated] Use 'navig mcp serve' instead."""
    from navig import console_helper as ch

    ch.warn("'navig mcp start' is deprecated — use 'navig mcp serve'.")
    from navig.mcp_server import start_mcp_server

    try:
        start_mcp_server(mode=transport.value, port=port, host=host)
    except (ImportError, ValueError) as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc


@mcp_app.command("status")
def mcp_status():
    """Show MCP server status."""
    from navig import console_helper as ch

    ch.warn("navig mcp status is not yet implemented in this build.")
