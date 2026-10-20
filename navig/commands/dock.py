"""navig dock — the NAVIG Dock browser extension's link to this daemon.

``status`` shows which extension is paired and whether agents can drive the browser
right now; ``unpair`` forgets the pairing (the Dock pairs again when browser control is
turned back on). Agents use the ``dock_*`` MCP tools (``navig mcp serve``).
"""

from __future__ import annotations

import json

import typer

from navig import console_helper as ch

dock_app = typer.Typer(
    name="dock",
    help="NAVIG Dock — let AI agents use your own browser (pairing and status).",
    no_args_is_help=True,
)


def _live_status() -> dict | None:
    try:
        from navig.gateway_client import gateway_request, unwrap_envelope  # noqa: PLC0415

        resp = gateway_request("GET", "/dock/status", timeout=3)
        if resp.status_code != 200:
            return None
        body = unwrap_envelope(resp.json())
        return body if isinstance(body, dict) else None
    except Exception:  # noqa: BLE001 — daemon down / older daemon: status still shows the pairing
        return None


@dock_app.command("status")
def status_cmd(as_json: bool = typer.Option(False, "--json", help="Machine-readable output.")) -> None:
    """Which extension is paired, and whether agents can use the browser now."""
    from navig.dock import pairing  # noqa: PLC0415

    pairs = pairing.load()
    live = _live_status()
    if as_json:
        print(json.dumps({
            "pairing_file": str(pairing.pairing_path()),
            "pairing_readable": pairs is not None,
            "paired": [{"extension_id": p.extension_id, "name": p.name, "paired_at": p.paired_at}
                       for p in (pairs or ())],
            "daemon": live,
        }, indent=2))
        return

    from navig.console_helper import Table  # noqa: PLC0415

    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column("k", style="dim", no_wrap=True)
    t.add_column("v")
    if pairs is None:
        t.add_row("Pairing", "[red]✗ unreadable[/red]  extensions are refused until it is reset")
    elif not pairs:
        t.add_row("Pairing", "[dim]○ not paired[/dim]  any local extension is trusted until the Dock pairs")
    else:
        for p in pairs:
            t.add_row("Pairing", f"[green]● {p.name or 'NAVIG Dock'}[/green]  {p.extension_id}  since {p.paired_at}")
    if live is None:
        t.add_row("Daemon", "[dim]○ not reachable[/dim]")
    elif live.get("connected"):
        state = "[yellow]● paused by the user (Stop)[/yellow]" if live.get("paused") else "[green]● connected[/green]"
        ping = (live.get("ping_ms") or {}).get("p50")
        t.add_row("Browser", f"{state}  Dock {live.get('dock_version') or '?'}"
                  + (f"  ping {ping} ms" if ping is not None else ""))
    else:
        t.add_row("Browser", "[dim]○ not connected[/dim]")
    ch.console.print(t)
    if pairs is None:
        ch.info("Next step: navig dock unpair --yes  (resets the pairing file)")
    elif live and not live.get("connected"):
        ch.info("Next step: in the Dock, Settings → NAVIG daemon → turn on 'Let AI agents use this browser'.")
    elif live and live.get("connected"):
        ch.info("Next step: agents use the dock_* tools from `navig mcp serve`.")


@dock_app.command("unpair")
def unpair_cmd(yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation.")) -> None:
    """Forget the paired extension. The Dock pairs again when browser control is turned on."""
    from navig.dock import pairing  # noqa: PLC0415

    if not yes and not typer.confirm("Forget the paired NAVIG Dock? Agents lose browser access until it pairs again."):
        raise typer.Exit(1)
    removed = pairing.unpair(None)
    ch.success(f"Unpaired ({removed} extension{'s' if removed != 1 else ''}).")
