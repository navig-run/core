"""
navig.commands.stats — CLI command: navig stats

Fetches aggregated anonymous install statistics from the NAVIG
telemetry endpoint and renders them in a Rich panel.

Usage:
  navig stats
  navig stats --url https://your-self-hosted-server.example.com
  navig stats --json
"""

from __future__ import annotations

import json as _json_mod
import os

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from navig.console_helper import get_console

stats_app = typer.Typer(
    name="stats",
    help="Show anonymous NAVIG install statistics.",
    no_args_is_help=False,
)

_DEFAULT_URL = os.environ.get("NAVIG_TELEMETRY_URL", "https://telemetry.navig.run")
_console = get_console()


def _fetch_stats(url: str, timeout: float = 6.0) -> dict:
    """
    Fetch stats from the telemetry endpoint.

    Args:
        url:     Base URL of the telemetry server.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON dict with keys ``total_installs`` and ``by_platform``.

    Raises:
        SystemExit: When the server is unreachable or returns an error.
    """
    try:
        import requests as _req
    except ImportError:
        typer.echo("Error: 'requests' is required — pip install requests", err=True)
        raise typer.Exit(1) from None

    endpoint = url.rstrip("/") + "/telemetry/stats"
    try:
        resp = _req.get(endpoint, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"Error fetching stats from {endpoint}: {exc}", err=True)
        raise typer.Exit(1) from None


@stats_app.callback(invoke_without_command=True)
def stats(
    url: str | None = typer.Option(
        None,
        "--url",
        "-u",
        envvar="NAVIG_TELEMETRY_URL",
        help="Base URL of the telemetry server (default: https://telemetry.navig.run).",
        show_default=True,
    ),
    json: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON instead of the formatted panel.",
    ),
) -> None:
    """
    Display anonymous NAVIG install statistics.

    Shows total install count and a per-platform breakdown.
    Data is sourced from the NAVIG telemetry server — no credentials required.
    """
    effective_url = url or _DEFAULT_URL
    data = _fetch_stats(effective_url)

    if json:
        _console.print_json(_json_mod.dumps(data))
        return

    total: int = data.get("total_installs", 0)
    by_platform: dict = data.get("by_platform", {})

    # Sort platforms by count descending
    sorted_platforms = sorted(by_platform.items(), key=lambda x: -x[1])

    # Build platform table
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", padding=(0, 1))
    table.add_column("Platform", style="white", no_wrap=True)
    table.add_column("Installs", style="bold white", justify="right")
    table.add_column("Share", style="dim", justify="right")

    for plat, count in sorted_platforms:
        pct = f"{count / total * 100:.1f}%" if total else "—"
        table.add_row(plat or "unknown", str(count), pct)

    # Wrap in panel
    panel = Panel(
        table,
        title="[bold cyan]NAVIG Install Statistics[/bold cyan]",
        subtitle=f"[dim]Total installs: {total:,}[/dim]",
        border_style="cyan",
        expand=False,
    )
    _console.print()
    _console.print(panel)
    _console.print()
