"""Mesh topology management commands."""

from __future__ import annotations

import typer

from navig.console_helper import get_console

mesh_app = typer.Typer(help="Mesh topology management")


@mesh_app.command()
def status() -> None:
    """Show the current mesh topology and peer status."""
    try:
        from navig.mesh.registry import get_registry

        registry = get_registry()
        peers = registry.list_peers()
    except Exception:  # noqa: BLE001
        peers = []


    con = get_console()

    if not peers:
        con.print("[dim]Mesh: no peers discovered yet.[/dim]")
        con.print("[dim]Start the NAVIG daemon to begin mesh discovery:[/dim] navig service start")
        return

    con.print(f"[bold]Mesh peers:[/bold] {len(peers)} node(s) online\n")
    for peer in peers:
        load_pct = int(getattr(peer, "load", 0) * 100)
        caps = ", ".join(getattr(peer, "capabilities", []) or ["—"])
        con.print(
            f"  [cyan]{peer.hostname}[/cyan]"
            f"  [dim]{getattr(peer, 'gateway_url', '')}[/dim]"
            f"  load={load_pct}%  caps=[{caps}]"
        )


@mesh_app.command()
def peers() -> None:
    """List all known mesh peers (alias for 'mesh status')."""
    status()
