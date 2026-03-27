"""
``navig tailscale`` — Tailscale network management.
"""

from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.table import Table

console = Console()

tailscale_app = typer.Typer(
    name="tailscale",
    help="Tailscale network integration.",
    invoke_without_command=True,
    no_args_is_help=True,
)


@tailscale_app.command("status")
def ts_status(
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show Tailscale status and peer list."""
    from navig.integrations.tailscale import Tailscale  # noqa: PLC0415

    status = asyncio.run(Tailscale().status())
    if json_out:
        console.print(json.dumps(status.to_dict(), indent=2))
        return
    if not status.available:
        console.print(f"[red]Tailscale not available:[/red] {status.error}")
        raise typer.Exit(1)
    if not status.running:
        console.print(
            f"[yellow]Tailscale not running.[/yellow] Backend: {status.backend_state}"
        )
        raise typer.Exit(1)
    console.print(
        f"[green]Tailscale running[/green] — {status.self_hostname} ({status.self_ip})"
    )
    console.print(f"Backend: {status.backend_state}")
    if status.peers:
        table = Table(title="Peers")
        table.add_column("Hostname", style="cyan")
        table.add_column("IP")
        table.add_column("Online")
        table.add_column("OS")
        for p in status.peers:
            online = "[green]yes[/green]" if p.online else "[dim]no[/dim]"
            table.add_row(p.hostname, p.tailscale_ip, online, p.os)
        console.print(table)
    else:
        console.print("[dim]No peers.[/dim]")


@tailscale_app.command("ping")
def ts_ping(
    peer: str = typer.Argument(..., help="Peer hostname or IP."),
) -> None:
    """Ping a Tailscale peer."""
    from navig.integrations.tailscale import Tailscale  # noqa: PLC0415

    ok = asyncio.run(Tailscale().ping(peer))
    if ok:
        console.print(f"[green]pong[/green] from {peer}")
    else:
        console.print(f"[red]No response[/red] from {peer}")
        raise typer.Exit(1)


@tailscale_app.command("ip")
def ts_ip(
    peer: str | None = typer.Argument(None, help="Peer name (omit for self)."),
) -> None:
    """Show Tailscale IP of self or a peer."""
    from navig.integrations.tailscale import Tailscale  # noqa: PLC0415

    ip = asyncio.run(Tailscale().ip(peer))
    if ip:
        console.print(ip)
    else:
        console.print("[yellow]Not found.[/yellow]")
        raise typer.Exit(1)
