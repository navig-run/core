"""
NAVIG Webhook CLI Commands

Commands:
    navig webhook list             — List all registered webhooks (inbound + outbound)
    navig webhook add-inbound      — Create an inbound trigger endpoint
    navig webhook add-outbound     — Register an outbound notification URL
    navig webhook disable <id>     — Disable a webhook
    navig webhook delete <id>      — Delete a webhook permanently
    navig webhook test <id>        — Send a test payload to an outbound webhook
"""
from __future__ import annotations

from typing import Optional

import typer

from navig.lazy_loader import lazy_import

_ch = lazy_import("navig.console_helper")

webhook_app = typer.Typer(name="webhook", help="Manage NAVIG inbound/outbound webhooks")

_DAEMON_BASE = "http://127.0.0.1:7421"


def _api(method: str, path: str, json=None):
    """Call the NAVIG host daemon REST API."""
    import httpx
    url = f"{_DAEMON_BASE}{path}"
    try:
        if method == "GET":
            r = httpx.get(url, timeout=5)
        elif method == "POST":
            r = httpx.post(url, json=json, timeout=5)
        elif method == "DELETE":
            r = httpx.delete(url, timeout=5)
        else:
            raise ValueError(f"Unknown method: {method}")
        if r.status_code >= 400:
            _ch.error(f"API error {r.status_code}: {r.text[:200]}")
            raise typer.Exit(1)
        return r.json()
    except httpx.ConnectError as _exc:
        _ch.error("NAVIG host daemon is not running (port 7421).")
        _ch.info("Start with: navig gateway start  (or the Go host daemon)")
        raise typer.Exit(1) from _exc


@webhook_app.command("list")
def webhook_list(json_output: bool = typer.Option(False, "--json")):
    """List all registered inbound and outbound webhooks."""
    import json as _json
    data = _api("GET", "/api/v1/webhooks")
    if json_output:
        from rich import print as rprint
        rprint(_json.dumps(data, indent=2))
        return
    from rich.console import Console
    from rich.table import Table

    inbound = data.get("inbound") or []
    outbound = data.get("outbound") or []

    if not inbound and not outbound:
        _ch.warning("No webhooks registered.")
        return

    con = Console()

    if inbound:
        t = Table(title="Inbound Webhooks", show_lines=False)
        t.add_column("ID", style="dim")
        t.add_column("Name", style="cyan")
        t.add_column("Token", style="yellow")
        t.add_column("Secret", style="dim")
        t.add_column("Triggers", justify="right")
        t.add_column("Enabled")
        for wh in inbound:
            t.add_row(
                wh["id"], wh["name"], wh["token"], wh.get("secret", "****"),
                str(wh.get("trigger_count", 0)),
                "✅" if wh["enabled"] else "⏸️",
            )
        con.print(t)

    if outbound:
        t2 = Table(title="Outbound Webhooks", show_lines=False)
        t2.add_column("ID", style="dim")
        t2.add_column("Name", style="cyan")
        t2.add_column("URL", style="blue", max_width=45)
        t2.add_column("Events", style="yellow")
        t2.add_column("Enabled")
        for wh in outbound:
            events = ", ".join(wh.get("events") or []) or "all"
            t2.add_row(
                wh["id"], wh["name"], wh["url"][:45],
                events,
                "✅" if wh["enabled"] else "⏸️",
            )
        con.print(t2)


@webhook_app.command("add-inbound")
def webhook_add_inbound(
    name: str = typer.Option(..., "--name", "-n", help="Human-readable name"),
    json_output: bool = typer.Option(False, "--json"),
):
    """
    Create an inbound webhook endpoint.
    Returns a unique URL + HMAC secret. Use the secret to sign requests.

    Example (GitHub Actions):
        curl -X POST https://yourserver/webhook/in/<token> \\
             -H 'X-Navig-Signature: sha256=<hmac>' \\
             -d '{"event":"deploy","branch":"main"}'
    """
    import json as _json
    result = _api("POST", "/api/v1/webhooks/inbound", json={"name": name})
    if json_output:
        from rich import print as rprint
        rprint(_json.dumps(result, indent=2))
        return
    wh = result.get("webhook", result)
    from rich.console import Console
    Console().print(
        f"\n[bold green]✅ Inbound webhook created[/bold green]\n"
        f"  ID:     [cyan]{wh.get('id')}[/cyan]\n"
        f"  Name:   {wh.get('name')}\n"
        f"  URL:    [blue]POST {_DAEMON_BASE}/webhook/in/{wh.get('token')}[/blue]\n"
        f"  Secret: [yellow]{wh.get('secret')}[/yellow] (sign payload with HMAC-SHA256)\n"
        f"\n  Add header: [dim]X-Navig-Signature: sha256=<hmac>[/dim]"
    )


@webhook_app.command("add-outbound")
def webhook_add_outbound(
    name: str = typer.Option(..., "--name", "-n", help="Human-readable name"),
    url: str = typer.Option(..., "--url", "-u", help="Target URL to POST events to"),
    events: Optional[str] = typer.Option(
        None, "--events", "-e",
        help="Comma-separated events to subscribe to. Empty = all. "
             "Options: task_complete,task_fail,task_start,captcha,2fa"
    ),
    json_output: bool = typer.Option(False, "--json"),
):
    """
    Register an outbound webhook. NAVIG will POST to this URL when events occur.

    Examples:
        navig webhook add-outbound --name Zapier --url https://hooks.zapier.com/xyz
        navig webhook add-outbound --name Slack --url https://hooks.slack.com/... --events task_complete,task_fail
    """
    import json as _json
    event_list = [e.strip() for e in events.split(",")] if events else []
    result = _api("POST", "/api/v1/webhooks/outbound", json={"name": name, "url": url, "events": event_list})
    if json_output:
        from rich import print as rprint
        rprint(_json.dumps(result, indent=2))
        return
    wh = result.get("webhook", result)
    from rich.console import Console
    Console().print(
        f"\n[bold green]✅ Outbound webhook registered[/bold green]\n"
        f"  ID:     [cyan]{wh.get('id')}[/cyan]\n"
        f"  Name:   {wh.get('name')}\n"
        f"  URL:    [blue]{url}[/blue]\n"
        f"  Events: {', '.join(event_list) or 'all'}\n"
        f"  Secret: [yellow]{wh.get('secret')}[/yellow] (verify X-Navig-Signature on your end)"
    )


@webhook_app.command("disable")
def webhook_disable(
    webhook_id: str = typer.Argument(..., help="Webhook ID"),
):
    """Disable a webhook (inbound or outbound)."""
    _api("POST", f"/api/v1/webhooks/{webhook_id}/disable")
    _ch.success(f"Webhook {webhook_id} disabled.")


@webhook_app.command("delete")
def webhook_delete(
    webhook_id: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Permanently delete a webhook."""
    if not force:
        if not _ch.confirm_action(f"Delete webhook {webhook_id}?"):
            raise typer.Abort()
    _api("DELETE", f"/api/v1/webhooks/{webhook_id}")
    _ch.success(f"Webhook {webhook_id} deleted.")


@webhook_app.command("test")
def webhook_test(
    webhook_id: str = typer.Argument(..., help="Outbound webhook ID to test"),
):
    """Send a test event payload to an outbound webhook."""
    result = _api("POST", f"/api/v1/webhooks/{webhook_id}/test")
    if result.get("ok"):
        _ch.success(f"Test event delivered to webhook {webhook_id}.")
    else:
        _ch.error(f"Test failed: {result.get('error', 'unknown')}")
