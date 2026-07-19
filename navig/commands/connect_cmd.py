"""
`navig connect` — manage NAVIG AI provider connections.

The CLI face of the unified connection service (:mod:`navig.providers.connect`):
the same flow the deck and onboarding use. Connect Claude Code / Codex / Copilot
(later phases), API-key providers, and local runtimes; list, set a default, and
disconnect.

Security: secrets are NEVER accepted as command-line arguments (they would leak
into process args / shell history). Keys are read via a hidden prompt or stdin.
"""

from __future__ import annotations

import asyncio
import sys

import typer

from navig.console_helper import get_console
from navig.providers.connect import (
    CONNECTION_TEMPLATES,
    begin_oauth,
    complete_oauth,
    connect_provider,
    detect_external,
    diagnostics_report,
    list_connections,
    list_templates,
    resolve_default,
)
from navig.providers.connect import (
    revalidate as _revalidate,
)
from navig.providers.connect import (
    set_default as _set_default,
)
from navig.providers.connection_types import ConnectionError as ConnError

connect_app = typer.Typer(
    help="Connect & manage AI providers (Claude Code, Codex, Copilot, API keys, local). Service integrations → `navig connector`.",
    invoke_without_command=True,
    no_args_is_help=False,
)


def _read_secret(stdin: bool, label: str = "API key") -> str | None:
    """Read a secret without exposing it in argv: stdin pipe or hidden prompt."""
    if stdin:
        data = sys.stdin.readline().strip()
        return data or None
    if sys.stdin.isatty():
        return typer.prompt(label, hide_input=True) or None
    # non-interactive without --stdin → no secret
    return None


@connect_app.callback()
def _callback(ctx: typer.Context):
    """List connections when invoked with no subcommand."""
    if ctx.invoked_subcommand is None:
        _list()


@connect_app.command("list")
def list_cmd():
    """List configured connections and their status."""
    _list()


def _list() -> None:
    console = get_console()
    rows = list_connections()
    default = resolve_default()
    default_id = default["connection_id"] if default else None
    if not rows:
        console.print("[dim]No connections yet. Run [bold]navig connect add[/bold] to add one, "
                      "or [bold]navig connect login[/bold] for a subscription.[/dim]")
        return
    try:
        from rich.table import Table

        table = Table(title="AI Connections", show_lines=False)
        table.add_column("", width=2)
        table.add_column("Name")
        table.add_column("Template")
        table.add_column("Driver")
        table.add_column("State")
        # Short id — enough to copy into `navig connect remove <id>` /
        # `navig connect default <id>` (both accept a unique prefix).
        table.add_column("ID", style="dim", no_wrap=True)
        for r in rows:
            mark = "★" if r["connection_id"] == default_id else " "
            table.add_row(
                mark, r["name"], r["template_id"], r["driver"], r["ui_state"],
                str(r["connection_id"])[:8],
            )
        console.print(table)
        console.print(
            "[dim]remove one with [bold]navig connect remove <id>[/bold] "
            "(the short ID above, or the exact name)[/dim]"
        )
    except Exception:  # noqa: BLE001 — fall back to plain text
        for r in rows:
            mark = "*" if r["connection_id"] == default_id else " "
            console.print(
                f"{mark} {r['name']} [{r['template_id']}] {r['ui_state']} "
                f"({str(r['connection_id'])[:8]})"
            )


@connect_app.command("login")
def login_cmd(
    template: str = typer.Argument("claude-max", help="OAuth template: claude-max | chatgpt."),
    name: str = typer.Option(None, "--name", help="Display name for this connection."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Print the URL instead of opening it."),
):
    """Log in to a subscription via OAuth — `claude-max` (Claude Pro/Max) or `chatgpt`
    (ChatGPT Plus/Pro). Mints a token / API key; no secrets pasted on the command line."""
    console = get_console()
    try:
        flow = begin_oauth(template)
    except ConnError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    url = flow["auth_url"]
    kind = flow.get("kind")
    console.print(
        "[dim]Note: using a subscription with NAVIG is outside the provider's "
        "first-party-CLI terms — use your own subscription on your own infra.[/dim]"
    )
    label = "ChatGPT (Plus/Pro)" if kind == "codex" else "Claude (Pro or Max)"
    console.print(f"[bold]Sign in to {label}[/bold] in the browser that opens.")
    console.print(f"[dim]{url}[/dim]")
    if not no_browser:
        try:
            import webbrowser

            webbrowser.open(url)
            console.print("[dim]Opened your browser…[/dim]")
        except Exception:  # noqa: BLE001
            pass

    code: str | None = None
    # ChatGPT/Codex redirects to a loopback URL — try to capture it automatically.
    if kind == "codex":
        try:
            from navig.providers import codex_oauth

            # `state` comes back from begin_oauth — no reaching into private
            # in-flight state (which no longer lives in memory anyway).
            state = flow.get("state") or ""
            console.print("[dim]Waiting for the browser redirect (localhost:1455)…[/dim]")
            code = codex_oauth.capture_loopback_code(state, timeout=180.0)
        except Exception:  # noqa: BLE001
            code = None

    if not code:
        if not sys.stdin.isatty():
            console.print("[yellow]Non-interactive shell — re-run in a terminal to paste the code.[/yellow]")
            raise typer.Exit(1)
        prompt = ("Paste the redirected localhost URL (or the code)"
                  if kind == "codex" else "Paste the authorization code")
        code = typer.prompt(prompt).strip()
    if not code:
        console.print("[red]No code provided.[/red]")
        raise typer.Exit(1)

    try:
        conn = asyncio.run(complete_oauth(flow["handle"], code, name=name))
    except ConnError as exc:
        console.print(f"[red]Login failed:[/red] {exc}")
        raise typer.Exit(1)

    if conn.is_routable:
        console.print(f"[green]✓ Connected[/green] {conn.name} ({conn.ui_state().value}).")
    else:
        console.print(f"[yellow]Saved[/yellow] {conn.name} — {conn.ui_state().value}.")


@connect_app.command("detect")
def detect_cmd():
    """Detect official coding-agent runtimes (Claude Code, Codex, Copilot) on this machine."""
    console = get_console()
    found = detect_external()
    if not found:
        console.print("[dim]No official coding-agent runtimes detected on PATH.[/dim]")
        return
    for d in found:
        cfg = "configured" if d.get("configured") else "not configured"
        console.print(
            f"  [bold]{d['label']}[/bold] — installed ([dim]{cfg}[/dim]) → {d['runtime_path']}"
        )
    console.print(
        "[dim]These are delegated runtimes — detected, not yet routable by NAVIG inference.[/dim]"
    )


@connect_app.command("templates")
def templates_cmd():
    """List the available connection templates, grouped."""
    console = get_console()
    groups = {
        "subscription": "Subscriptions (OAuth — use `navig connect login`)",
        "api": "API providers (use `navig connect add`)",
        "local": "Local runtimes",
        "external": "Detected coding agents (delegated)",
    }
    by_group: dict[str, list] = {}
    for t in list_templates():
        by_group.setdefault(t.group, []).append(t)
    for group, heading in groups.items():
        items = by_group.get(group)
        if not items:
            continue
        console.print(f"[bold]{heading}[/bold]")
        for t in items:
            hint = ""
            if t.group == "subscription":
                hint = "  → navig connect login " + t.template_id
            elif t.key_placeholder:
                hint = f"  [dim]{t.key_placeholder}[/dim]"
            elif t.requires_endpoint:
                hint = "  [dim](needs endpoint)[/dim]"
            console.print(f"  [bold]{t.template_id}[/bold] — {t.name}{hint}")


@connect_app.command("add")
def add_cmd(
    template: str = typer.Argument(..., help="Template id (see `navig connect templates`)."),
    name: str = typer.Option(None, "--name", help="Display name for this connection."),
    endpoint: str = typer.Option(None, "--endpoint", help="Endpoint URL (OpenAI-compatible)."),
    model: str = typer.Option(None, "--model", help="Default model id."),
    stdin: bool = typer.Option(False, "--stdin", help="Read the API key from stdin (else hidden prompt)."),
):
    """Add a connection. The API key is read via hidden prompt or --stdin (never argv)."""
    console = get_console()
    tmpl = CONNECTION_TEMPLATES.get(template)
    if tmpl is None:
        console.print(f"[red]Unknown template:[/red] {template}. See `navig connect templates`.")
        raise typer.Exit(1)

    # Subscriptions authenticate via OAuth, not a pasted key — accepting a manual
    # "API key" here stores a bogus credential (the claude-max needs_reauth trap).
    if tmpl.auth_kind == "oauth":
        console.print(
            f"[yellow]{template} is a subscription — sign in with[/yellow] "
            f"[bold]navig connect login {template}[/bold] (no key to paste)."
        )
        raise typer.Exit(1)

    api_key = _read_secret(stdin) if tmpl.requires_key else None
    if tmpl.requires_key and not api_key:
        console.print("[red]This connection requires an API key.[/red]")
        raise typer.Exit(1)

    try:
        conn = asyncio.run(connect_provider(
            template, name=name, api_key=api_key, endpoint=endpoint, model=model,
        ))
    except ConnError as exc:
        console.print(f"[red]Connect failed:[/red] {exc}")
        raise typer.Exit(1)

    state = conn.ui_state().value
    if conn.is_routable:
        console.print(f"[green]✓ Connected[/green] {conn.name} ({state}).")
    else:
        console.print(f"[yellow]Saved[/yellow] {conn.name} — {state}. Re-run to re-authenticate.")


@connect_app.command("doctor")
def doctor_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit the raw redacted JSON report."),
):
    """Print a redacted, paste-safe diagnostics report (no secrets)."""
    import json as _json

    console = get_console()
    report = diagnostics_report()
    if json_out:
        console.print_json(_json.dumps(report))
        return
    console.print(f"[bold]Connections:[/bold] {len(report['connections'])}")
    for c in report["connections"]:
        sec = "🔑" if c.get("has_secret") else "—"
        console.print(f"  {sec} {c['name']} [{c['template_id']}] {c['ui_state']}")
    console.print(f"[bold]Default:[/bold] {report['default_connection_id'] or '—'}")
    det = ", ".join(d["label"] for d in report["detected_external"]) or "none"
    console.print(f"[bold]Detected runtimes:[/bold] {det}")
    console.print(f"[bold]Bridge:[/bold] pi-ai@{report['bridge']['pi_pinned']} (proto {report['bridge']['protocol']})")


@connect_app.command("default")
def default_cmd(
    connection: str = typer.Argument(
        ..., help="Connection to make default: a full id, a unique id-prefix, or the exact name."
    ),
):
    """Set the global default connection."""
    console = get_console()
    try:
        cid = _resolve_connection_ref(connection)
        _set_default(cid)
        console.print(f"[green]✓[/green] Default set to {cid}.")
    except ConnError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


@connect_app.command("test")
def test_cmd(
    connection_id: str = typer.Argument(None, help="Connection id to re-test (default: all)."),
):
    """Re-validate connection(s) and persist the fresh status.

    Runs a real 1-token probe through each connection's driver and updates its
    state — the way to flip a connection to [bold]ready[/bold] after fixing auth.

    Configured-elsewhere (BYOK) keys are probed too. They have no stored row, so
    nothing is persisted for them — but they ARE really tested, not assumed good."""
    console = get_console()
    if connection_id:
        targets = [connection_id]
    else:
        # Test EVERY connection, including the "configured-elsewhere" (BYOK) ones.
        # They used to be excluded as "always live" — but live means auto-synced,
        # not valid. Skipping them made `navig connect test` silent about the very
        # providers most people actually use.
        targets = [r["connection_id"] for r in list_connections()]
    if not targets:
        console.print("[dim]No connections to test. Add one with `navig connect add <template>`.[/dim]")
        return
    for cid in targets:
        try:
            conn = asyncio.run(_revalidate(cid))
        except ConnError as exc:
            console.print(f"[red]✗[/red] {cid}: {exc}")
            continue
        state = conn.ui_state().value
        if conn.is_routable:
            console.print(f"[green]✓ ready[/green] {conn.name} ({state})")
        else:
            console.print(f"[yellow]● {state}[/yellow] {conn.name}")


def _resolve_connection_ref(token: str) -> str:
    """Resolve a user-typed reference — a full connection id, a unique id
    **prefix** (as shown by `navig connect list`), or an exact **name** — to a
    full connection_id. Raises :class:`ConnError` on no or ambiguous match so the
    caller never removes the wrong connection."""
    token = (token or "").strip()
    if not token:
        raise ConnError("No connection given.")
    rows = list_connections()
    for r in rows:  # exact id wins
        if r["connection_id"] == token:
            return token
    prefix = [r for r in rows if str(r["connection_id"]).startswith(token)]
    if len(prefix) == 1:
        return prefix[0]["connection_id"]
    if len(prefix) > 1:
        raise ConnError(f"'{token}' matches {len(prefix)} connections — use a longer id.")
    named = [r for r in rows if str(r["name"]).strip().lower() == token.lower()]
    if len(named) == 1:
        return named[0]["connection_id"]
    if len(named) > 1:
        raise ConnError(f"'{token}' matches {len(named)} connections by name — use the id.")
    raise ConnError(f"No connection matches '{token}'. Run `navig connect list` to see ids.")


@connect_app.command("remove")
def remove_cmd(
    connection: str = typer.Argument(
        ..., help="Connection to remove: a full id, a unique id-prefix, or the exact name."
    ),
):
    """Disconnect & remove a connection (local config + vault material only)."""
    console = get_console()
    try:
        cid = _resolve_connection_ref(connection)
        _disconnect(cid)
        console.print(f"[green]✓[/green] Removed {cid}.")
    except ConnError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
