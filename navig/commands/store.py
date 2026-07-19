"""navig store — the hub for everything connectable ("the doctor of wiring").

ONE view over skills, plugins (all formats), modules (system plugins), MCP
servers, connectors, and launchers, each with a wire state:

    ✓ wired      installed + enabled + usable (degraded flagged)
    ○ unwired    installed but disabled / disconnected / tier-locked
    ↓ available  known from the catalog, not installed
    ✗ broken     installed but failed

Backed by `navig.hub` (shared with the deck's /api/deck/store). SQLite
maintenance formerly under this name moved to `navig db local` — hidden
forwards below keep old invocations working for one release.
"""

from __future__ import annotations

import typer

from navig import console_helper as ch

store_app = typer.Typer(
    name="store",
    help="The Store — see and wire everything connectable (plugins, skills, MCP, connectors).",
    invoke_without_command=True,
    no_args_is_help=False,
)

_STATE_MARK = {
    "wired": "[green]✓[/green]",
    "unwired": "[dim]○[/dim]",
    "available": "[cyan]↓[/cyan]",
    "broken": "[red]✗[/red]",
}
_STATE_COLOR = {"wired": "green", "unwired": "dim", "available": "cyan", "broken": "red"}
_STATE_ORDER = ("wired", "unwired", "available", "broken")


def _state_banner(by_state: dict[str, int], *, include_available: bool) -> str:
    """One-line wire-state summary — glyph + count + word, semantic colour.

    Rendered at the top of `list`/`status` so the whole picture reads at a glance.
    """
    parts = []
    for st in _STATE_ORDER:
        if st == "available" and not include_available:
            continue
        parts.append(f"{_STATE_MARK[st]} {by_state.get(st, 0)} {st}")
    return "  ·  ".join(parts)


@store_app.callback()
def _store_callback(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        # Explicit defaults — calling the command function directly would pass
        # typer.OptionInfo objects as values (renders as `[]`).
        _store_list(kind=None, state=None, available=True, refresh=False,
                    json_output=False, plain=False)
        raise typer.Exit()


@store_app.command("list")
def _store_list(
    kind: str = typer.Option(None, "--kind", "-k", help="Filter: module|plugin|skill|mcp|connector"),
    state: str = typer.Option(None, "--state", "-s", help="Filter: wired|unwired|available|broken"),
    available: bool = typer.Option(True, "--available/--no-available", help="Include catalog items"),
    refresh: bool = typer.Option(False, "--refresh", help="Also fetch registered marketplaces"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """List everything connectable, grouped by kind, with wire states."""
    from navig.hub import collect_store

    items = collect_store(include_available=available, refresh=refresh)
    if kind:
        items = [i for i in items if i.kind == kind]
    if state:
        items = [i for i in items if i.state == state]

    if json_output:
        import json

        ch.console.print_json(json.dumps([i.to_dict() for i in items], indent=2))
        return
    if plain:
        for i in items:
            print(f"{i.id}\t{i.state}\t{i.version}\t{i.provider or ''}")
        return
    if not items:
        ch.info("Nothing matches.")
        return

    from rich.table import Table

    by_state: dict[str, int] = {}
    for i in items:
        by_state[i.state] = by_state.get(i.state, 0) + 1
    ch.console.print("  " + _state_banner(by_state, include_available=available) + "\n")

    kinds_order = ["module", "plugin", "webapp", "app", "skill", "mcp", "connector"]
    for k in kinds_order:
        rows = [i for i in items if i.kind == k]
        if not rows:
            continue
        table = Table(title=f"{k}s ({len(rows)})", show_lines=False)
        table.add_column("", width=2)
        table.add_column("Name", style="cyan")
        table.add_column("State")
        table.add_column("Badges", style="dim")
        table.add_column("Description")
        for i in sorted(rows, key=lambda x: (x.state != "broken", x.label.lower())):
            badges = " ".join(
                b for b, on in (
                    ("system", i.system), ("standalone", i.standalone),
                    ("locked", i.locked), ("degraded", i.degraded),
                ) if on
            )
            state_txt = (
                f"[{_STATE_COLOR.get(i.state, 'white')}]{i.state}[/]"
                + (f" [dim]({i.detail.get('error', '')[:40]})[/dim]"
                   if i.state == "broken" and i.detail.get("error") else "")
            )
            desc = i.description[:60] + "…" if len(i.description) > 60 else i.description
            table.add_row(_STATE_MARK.get(i.state, "?"), i.label, state_txt, badges, desc)
        ch.console.print(table)

    ch.dim("Wire something: navig store enable|disable|install|remove <id>  ·  Details: navig store info <id>")


@store_app.command("status")
def _store_status(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """One-screen wiring summary — counts per kind/state, broken items first."""
    from navig.hub import collect_store, store_status

    items = collect_store(include_available=False)  # installed only (matches store_status)
    summary = store_status(items)
    if json_output:
        # NOTHING may follow the JSON on stdout — scripts pipe this to jq.
        import json

        ch.console.print_json(json.dumps(summary, indent=2))
        return

    from rich.table import Table

    ch.heading("Store", "the doctor of wiring")
    banner = _state_banner(summary["by_state"], include_available=False)
    if summary["degraded"]:
        banner += f"  ·  [yellow]~ {len(summary['degraded'])} degraded[/yellow]"
    ch.console.print("  " + banner + "\n")

    # Per-kind × state breakdown (installed items only) — a table, not stacked lines.
    kinds = sorted({i.kind for i in items})
    if kinds:
        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("Kind", style="cyan", no_wrap=True)
        table.add_column("✓ wired", justify="right", no_wrap=True)
        table.add_column("○ unwired", justify="right", no_wrap=True)
        table.add_column("✗ broken", justify="right", no_wrap=True)
        for k in kinds:
            krows = [i for i in items if i.kind == k]
            w = sum(1 for i in krows if i.state == "wired")
            u = sum(1 for i in krows if i.state == "unwired")
            b = sum(1 for i in krows if i.state == "broken")
            table.add_row(
                f"{k}s",
                f"[green]{w}[/green]" if w else "[dim]0[/dim]",
                str(u) if u else "[dim]0[/dim]",
                f"[red]{b}[/red]" if b else "[dim]0[/dim]",
            )
        ch.console.print(table)

    if summary["broken"]:
        ch.console.print()
        for b in summary["broken"]:
            ch.error(f"  ✗ {b['id']}" + (f" — {b['error'][:80]}" if b.get("error") else ""))
    for d in summary["degraded"]:
        ch.warning(f"  ~ {d} (degraded)")

    ch.dim(
        "\n  "
        + ("fix broken → navig store info <id>  ·  " if summary["broken"] else "")
        + "browse everything → navig store list"
    )

    # One release of courtesy: the old `navig store status` was SQLite maintenance.
    try:
        from navig.platform.paths import config_dir

        if any(config_dir().glob("*.db")):
            ch.dim("  SQLite store maintenance moved to: navig db local status")
    except Exception:  # noqa: BLE001
        pass


@store_app.command("info")
def _store_info(
    item_id: str = typer.Argument(..., help="Item id (e.g. plugin:navig-social, module:finance)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Full detail for one item, including health and provided commands."""
    from navig.hub import collect_store

    items = collect_store(include_available=True)
    item = next((i for i in items if i.id == item_id or i.id.endswith(f":{item_id}")), None)
    if item is None:
        ch.error(f"'{item_id}' not found in the store")
        raise typer.Exit(1)
    if json_output:
        import json

        ch.console.print_json(json.dumps(item.to_dict(), indent=2))
        return
    from rich.table import Table

    mark = _STATE_MARK.get(item.state, "?")
    ch.heading(item.label, item.kind)
    ch.console.print(
        f"  {mark} [bold]{item.state}[/bold]"
        + (" [yellow](degraded)[/yellow]" if item.degraded else "")
        + ("  [dim]·[/dim] [yellow]locked[/yellow]" if item.locked else "")
    )
    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column("field", style="dim", no_wrap=True)
    t.add_column("value")
    for label, value in (
        ("description", item.description), ("version", item.version),
        ("source", item.source), ("provider", item.provider),
        ("actions", ", ".join(item.actions)),
    ):
        if value:
            t.add_row(label, str(value))
    for k, v in item.detail.items():
        if v and k != "error":
            t.add_row(k, str(v))
    if item.detail.get("error"):
        t.add_row("error", f"[red]{item.detail['error']}[/red]")
    ch.console.print(t)

    nudge = {
        "unwired": f"wire it → navig store enable {item.id}",
        "wired": f"unwire → navig store disable {item.id}",
        "available": f"install → navig store install {item.id}",
        "broken": f"remove/reinstall → navig store remove {item.id}",
    }.get(item.state)
    if nudge:
        ch.dim(f"\n  {nudge}")


def _apply(item_id: str, action: str) -> None:
    from navig.hub import apply_action

    result = apply_action(item_id, action)
    if not result.get("ok"):
        ch.error(result.get("message", "failed"))
        raise typer.Exit(1)
    ch.success(result.get("message", "done"))
    # webapp/app open + unlock return a URL for the client to launch.
    url = result.get("url")
    if url:
        import webbrowser

        if webbrowser.open(url):
            ch.dim(f"  {url}")
        else:
            ch.info(f"  Open: {url}")


@store_app.command("open")
def _store_open(item_id: str = typer.Argument(..., help="Webapp/app id (e.g. webapp:photopea)")):
    """Open a Bay webapp/app (or its checkout if it's premium and not owned)."""
    _apply(item_id if ":" in item_id else f"webapp:{item_id}", "open")


@store_app.command("enable")
@store_app.command("wire", hidden=True)
def _store_enable(item_id: str = typer.Argument(..., help="Item id to enable")):
    """Enable / wire an item (module or plugin)."""
    _apply(item_id, "enable")


@store_app.command("disable")
@store_app.command("unwire", hidden=True)
def _store_disable(item_id: str = typer.Argument(..., help="Item id to disable")):
    """Disable / unwire an item without uninstalling."""
    _apply(item_id, "disable")


@store_app.command("install")
def _store_install(
    source: str = typer.Argument(..., help="Plugin name, local dir, .zip, or git URL"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Install a plugin (validated through the host; pip fallback from the provider map)."""
    if source.startswith("pip:"):
        pkg = source[4:]
        if not yes and not typer.confirm(f"Install {pkg} into the NAVIG runtime?"):
            raise typer.Abort()
        from navig.cli.providers import pip_install

        try:
            pip_install(pkg)
        except ValueError as exc:
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        ch.success(f"Installed '{pkg}' — restart the CLI to pick up its commands")
        return
    _apply(f"plugin:{source}", "install")


@store_app.command("remove")
def _store_remove(
    item_id: str = typer.Argument(..., help="Plugin id to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Remove an installed plugin."""
    if not yes and not typer.confirm(f"Remove '{item_id}'?"):
        raise typer.Abort()
    _apply(item_id if ":" in item_id else f"plugin:{item_id}", "remove")


# ── hidden forwards: SQLite maintenance moved to `navig db local` ─────────────


@store_app.command("maintenance", hidden=True)
def _fwd_maintenance(json_output: bool = typer.Option(False, "--json")):
    ch.dim("[deprecated] moved: navig db local maintenance")
    from navig.commands.db_local import store_maintenance

    store_maintenance(json_output=json_output)


@store_app.command("backup", hidden=True)
def _fwd_backup(dest: str = typer.Argument(...), json_output: bool = typer.Option(False, "--json")):
    ch.dim("[deprecated] moved: navig db local backup")
    from navig.commands.db_local import store_backup

    store_backup(dest=dest, json_output=json_output)


@store_app.command("migrate", hidden=True)
def _fwd_migrate(
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json"),
):
    ch.dim("[deprecated] moved: navig db local migrate")
    from navig.commands.db_local import store_migrate

    store_migrate(dry_run=dry_run, json_output=json_output)


@store_app.command("cleanup", hidden=True)
def _fwd_cleanup(
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, "--force", "-f"),
    json_output: bool = typer.Option(False, "--json"),
):
    ch.dim("[deprecated] moved: navig db local cleanup")
    from navig.commands.db_local import store_cleanup

    store_cleanup(dry_run=dry_run, force=force, json_output=json_output)
