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


class _Client(str, Enum):
    vscode = "vscode"
    cursor = "cursor"


@mcp_app.command("install-config")
def mcp_install_config(
    client: _Client = typer.Option(_Client.vscode, "--client", "-c", help="Target editor: vscode | cursor"),
    path: str = typer.Option(None, "--path", help="Project dir (default: current dir)."),
):
    """Wire this editor to NAVIG's MCP server so blocks/tools are callable in-editor.

    Merge-safe: adds a `navig` server to the client's MCP config, preserving any
    existing servers. VS Code → .vscode/mcp.json · Cursor → .cursor/mcp.json.
    Then run `navig mcp serve --transport stdio` (or let the client launch it).
    """
    import json
    from pathlib import Path

    from navig import console_helper as ch
    from navig.mcp_server import generate_vscode_mcp_config

    server_def = generate_vscode_mcp_config().get("mcpServers", {}).get("navig", {})
    root = Path(path) if path else Path.cwd()

    if client == _Client.vscode:
        cfg_path = root / ".vscode" / "mcp.json"
        top_key = "servers"  # VS Code uses `servers`
    else:
        cfg_path = root / ".cursor" / "mcp.json"
        top_key = "mcpServers"  # Cursor uses `mcpServers`

    existing: dict = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ch.warning(f"Could not parse existing {cfg_path}; leaving it and writing a backup.")
            cfg_path.with_suffix(".json.bak").write_text(cfg_path.read_text(encoding="utf-8"), encoding="utf-8")
            existing = {}

    servers = existing.get(top_key) if isinstance(existing.get(top_key), dict) else {}
    servers["navig"] = server_def
    existing[top_key] = servers

    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    ch.success(f"Wired {client.value} → {cfg_path}")
    ch.dim("  Restart the editor, then the agent can call navig_block_list / navig_block_apply.")


@mcp_app.command("tools")
def mcp_tools(
    json_out: bool = typer.Option(False, "--json", help="Emit the tool list as JSON."),
):
    """List every tool the NAVIG MCP server exposes (what an editor/agent can call)."""
    from navig import console_helper as ch

    class _Probe:
        def __init__(self):
            self.tools: dict = {}
            self._tool_handlers: dict = {}

    probe = _Probe()
    from navig.mcp.tools import register_all_tools

    register_all_tools(probe)

    if json_out:
        ch.emit_json(list(probe.tools.values()))
        return

    # Group by prefix (navig_agent_*, navig_block_*, desktop_*, …) for readability.
    groups: dict[str, list[tuple[str, str]]] = {}
    for name, schema in sorted(probe.tools.items()):
        parts = name.split("_")
        group = parts[1] if name.startswith("navig_") and len(parts) > 1 else parts[0]
        groups.setdefault(group, []).append((name, schema.get("description", "")))

    ch.info(f"NAVIG MCP exposes {len(probe.tools)} tools across {len(groups)} groups:\n")
    for group, tools in sorted(groups.items()):
        ch.console.print(f"[bold]{group}[/bold] ({len(tools)})")
        for name, desc in tools:
            ch.console.print(f"  [green]{name}[/green]  [dim]{desc[:80]}[/dim]")
    ch.dim("\nWire an editor:  navig mcp install-config --client vscode   ·   Serve:  navig mcp serve")


@mcp_app.command("list")
def mcp_list(
    plain: bool = typer.Option(False, "--plain", help="One name per line, for scripting"),
    json_out: bool = typer.Option(
        False, "--json", help="Machine-readable JSON (secret-free — env values never included)"
    ),
):
    """List configured MCP servers."""
    # This used to echo the MCPServer OBJECTS — `navig mcp list` literally
    # printed "<navig.mcp_manager.MCPServer object at 0x0000000005BDEBA0>".
    # commands/mcp.py already renders a proper Name/Type/Status/Running table;
    # use it instead of keeping a second, broken rendering.
    from navig.commands.mcp import list_mcp_cmd

    list_mcp_cmd({"plain": plain, "json": json_out})


@mcp_app.command("start", hidden=True)
def mcp_start(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(3001, "--port", "-p", help="Bind port"),
    transport: _Transport = typer.Option(_Transport.http, "--transport", "-t"),
):
    """[deprecated] Use 'navig mcp serve' instead."""
    from navig import console_helper as ch

    ch.warning("'navig mcp start' is deprecated — use 'navig mcp serve'.")
    from navig.mcp_server import start_mcp_server

    try:
        start_mcp_server(mode=transport.value, port=port, host=host)
    except (ImportError, ValueError) as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc


@mcp_app.command("status", hidden=True)
def mcp_status():
    """Show MCP server status (alias of `navig mcp list`).

    It used to say "not yet implemented in this build" — which was its own kind
    of dead end: the information it promised is exactly what `list` shows.
    """
    from navig.commands.mcp import list_mcp_cmd

    list_mcp_cmd({})


@mcp_app.command("info")
def mcp_info(
    name: str = typer.Argument(..., help="Configured server name (from `navig mcp list`)"),
    json_out: bool = typer.Option(
        False, "--json", help="Machine-readable JSON (secret-free — env values never included)"
    ),
):
    """Show one server's details — type, command, enabled state.

    `list` gives the whole table; this is the per-server drill-down. The
    renderer (`status_mcp_cmd`) was implemented but reachable only from the
    legacy interactive shell — same stranded-verb story as search/install/
    enable/disable/remove below.
    """
    from navig.commands.mcp import status_mcp_cmd

    status_mcp_cmd(name, {"json": json_out})


# ── External MCP servers ─────────────────────────────────────────────────────
#
# `navig mcp list` already drives MCPManager, but the verbs that FILL that list
# were never wired: navig/commands/mcp.py implements search / install / enable /
# disable / remove and was reachable only from the legacy interactive shell. So
# the code told users to "Install with: navig mcp install <name>" — a command
# that did not exist. Wired here, against the same manager `list` already uses.


@mcp_app.command("search")
def mcp_search(
    query: str = typer.Argument(..., help="Search the MCP server directory"),
):
    """Search the MCP directory for installable servers."""
    from navig.commands.mcp import search_mcp_cmd

    search_mcp_cmd(query, {})


@mcp_app.command("install")
def mcp_install(
    name: str = typer.Argument(..., help="Server name from `navig mcp search`"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be installed"),
):
    """Install an MCP server from the directory."""
    from navig.commands.mcp import install_mcp_cmd

    install_mcp_cmd(name, {"dry_run": dry_run})


@mcp_app.command("enable")
def mcp_enable(
    name: str = typer.Argument(..., help="Configured server name"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Enable a configured MCP server."""
    from navig.commands.mcp import enable_mcp_cmd

    enable_mcp_cmd(name, {"dry_run": dry_run})


@mcp_app.command("disable")
def mcp_disable(
    name: str = typer.Argument(..., help="Configured server name"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Disable a configured MCP server."""
    from navig.commands.mcp import disable_mcp_cmd

    disable_mcp_cmd(name, {"dry_run": dry_run})


@mcp_app.command("remove")
def mcp_remove(
    name: str = typer.Argument(..., help="Configured server name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Remove a configured MCP server."""
    from navig.commands.mcp import uninstall_mcp_cmd

    # `yes` is honoured by uninstall_mcp_cmd — without exposing it there was no
    # way to remove a server non-interactively (scripts would hang on the prompt).
    uninstall_mcp_cmd(name, {"dry_run": dry_run, "yes": yes})
