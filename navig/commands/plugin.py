"""navig plugin — manage, install, inspect, and scaffold NAVIG plugins.

THE one plugin command group (the former inline group in main.py was merged
here). Thin wrappers over :class:`navig.plugins.host.PluginHost`, which unifies
the three install formats (CC/NAVIG package dirs, pip entry-point plugins,
legacy `plugin.py` Typer dirs). Canonical verbs: `add` / `remove`
(`install` / `uninstall` remain as hidden aliases).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import typer

from navig import console_helper as ch

plugin_app = typer.Typer(
    name="plugin",
    help="Manage NAVIG plugins — list, add, enable/disable, inspect, scaffold.",
    invoke_without_command=True,
    no_args_is_help=False,
)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")


def _user_plugins_dir() -> Path:
    from navig.platform.paths import plugins_dir

    return plugins_dir()


def _host():
    from navig.plugins.host import get_plugin_host

    return get_plugin_host()


# Wire-state glyphs + colours — mirrors the hub (`navig store`) aesthetic.
_PLUGIN_MARK = {
    "wired": "[green]✓[/green]",
    "degraded": "[yellow]~[/yellow]",
    "disabled": "[dim]○[/dim]",
    "failed": "[red]✗[/red]",
}
_PLUGIN_COLOR = {"wired": "green", "degraded": "yellow", "disabled": "dim", "failed": "red"}


def _plugin_state(p) -> str:
    """One word for a plugin's wire state — the single source both the list table
    and its summary banner read (so they can never disagree)."""
    if not p.enabled:
        return "disabled"
    if p.error or (p.health is not None and p.health.state.value == "failed"):
        return "failed"
    if p.health is not None and p.health.state.value == "degraded":
        return "degraded"
    return "wired"


@plugin_app.callback()
def _plugin_callback(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        # Explicit defaults — a direct call would pass typer.OptionInfo objects.
        _plugin_list(all_plugins=False, plain=False)
        raise typer.Exit()


# ── list / show ───────────────────────────────────────────────────────────────


@plugin_app.command("list")
def _plugin_list(
    all_plugins: bool = typer.Option(False, "--all", "-a", help="Include disabled plugins"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
):
    """List installed plugins across every format (package, pip, legacy)."""
    plugins = _host().list_installed(refresh=True)
    if not plugins:
        ch.info("No plugins installed")
        ch.dim("Add one: navig plugin add <path|zip|git-url|name>")
        ch.dim("Scaffold one: navig plugin new <name>")
        return

    if plain:
        for p in plugins:
            if all_plugins or p.enabled:
                print(f"{p.id}\t{p.version}\t{p.format}\t{p.source}\t{'on' if p.enabled else 'off'}")
        return

    from rich.table import Table

    shown = [p for p in plugins if all_plugins or p.enabled]

    # Wire-state summary banner — glyph + count + word (a broken/disabled count
    # only shows when there is one; wired always shows).
    counts: dict[str, int] = {}
    for p in shown:
        st = _plugin_state(p)
        counts[st] = counts.get(st, 0) + 1
    banner = "  ·  ".join(
        f"{_PLUGIN_MARK[st]} {counts.get(st, 0)} {st}"
        for st in ("wired", "degraded", "disabled", "failed")
        if st == "wired" or counts.get(st)
    )
    ch.console.print("  " + banner + "\n")

    table = Table(title="NAVIG Plugins")
    table.add_column("", width=2)
    table.add_column("Name", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Version", style="dim")
    table.add_column("Format", style="dim")
    table.add_column("Source", style="dim")
    table.add_column("Description")
    for p in shown:
        st = _plugin_state(p)
        fmt = f"{p.format}[dim] (convert → plugin-spec)[/dim]" if p.format == "legacy" else p.format
        desc = p.description[:50] + "…" if len(p.description) > 50 else p.description
        table.add_row(
            _PLUGIN_MARK[st], p.id, f"[{_PLUGIN_COLOR[st]}]{st}[/]",
            p.version, fmt, p.source, desc,
        )
    ch.console.print(table)
    ch.dim(
        "\n  enable/disable → navig plugin enable|disable <id>"
        "  ·  details → navig plugin show <id>"
    )


@plugin_app.command("show")
@plugin_app.command("info", hidden=True)  # deprecated → show
def _plugin_show(
    name: str = typer.Argument(..., help="Plugin id"),
    probe: bool = typer.Option(
        False, "--probe", help="Test-load a legacy plugin to surface load errors (has side effects)."
    ),
):
    """Show detailed information about an installed plugin.

    Read-only by default. `--probe` test-loads a legacy plugin (running its
    import + register()) to surface its real error / missing deps — kept
    opt-in because importing a healthy plugin can have side effects.
    """
    host = _host()
    p = host.get(name)
    if p is None:
        ch.error(f"Plugin '{name}' not found")
        raise typer.Exit(1)
    if probe and p.format == "legacy" and p.enabled:
        p = host.diagnose_legacy(p)  # opt-in force-load

    ch.heading(f"Plugin: {p.id}")

    from rich.table import Table

    meta = Table(box=None, show_header=False, padding=(0, 2))
    meta.add_column("field", style="dim", no_wrap=True)
    meta.add_column("value")
    meta.add_row("format", p.format)
    meta.add_row("version", p.version or "(unknown)")
    meta.add_row("source", p.source + (f" ({p.path})" if p.path else ""))
    meta.add_row("description", p.description or "(no description)")
    if p.commands:
        meta.add_row("CLI commands", ", ".join(sorted(p.commands)))
    ch.console.print(meta)
    ch.console.print()
    if not p.enabled:
        ch.warning("Status: disabled")
        ch.dim(f"Enable with: navig plugin enable {p.id}")
    elif p.error:
        ch.error("Status: failed to load", p.error)
    elif p.health is not None and p.health.state.value != "healthy":
        ch.warning(f"Status: {p.health.state.value}")
        if p.health.error:
            ch.dim(f"  {p.health.error}")
        for comp in p.health.degraded_components():
            ch.dim(f"  {comp.kind}:{comp.name} — {comp.state.value}: {comp.error}")
    else:
        ch.success("Status: wired")
    if p.missing_deps:
        ch.warning("Missing dependencies:")
        for dep in p.missing_deps:
            ch.dim(f"  • {dep}")
        # Always the runtime-aware installer — bare `pip install` fails on the
        # shipped pip-less uv runtime.
        from navig.plugins.require import install_hint  # noqa: PLC0415

        for dep in p.missing_deps:
            ch.dim(f"Install with: {install_hint(dep)}")
    elif p.format == "legacy" and p.enabled and not p.error:
        ch.dim("Run `navig plugin show --probe " + p.id + "` to test-load and surface any errors.")


# ── enable / disable ──────────────────────────────────────────────────────────


@plugin_app.command("enable")
def _plugin_enable(name: str = typer.Argument(..., help="Plugin id to enable")):
    """Enable a disabled plugin (all formats)."""
    try:
        p = _host().enable(name)
    except KeyError as exc:
        ch.error(str(exc.args[0]))
        raise typer.Exit(1) from exc
    ch.success(f"Plugin '{p.id}' enabled")
    if p.format == "legacy":
        ch.dim("Restart NAVIG to load its commands")


@plugin_app.command("disable")
def _plugin_disable(name: str = typer.Argument(..., help="Plugin id to disable")):
    """Disable a plugin without uninstalling it."""
    try:
        p = _host().disable(name)
    except KeyError as exc:
        ch.error(str(exc.args[0]))
        raise typer.Exit(1) from exc
    ch.success(f"Plugin '{p.id}' disabled")
    if p.commands:
        ch.dim(f"Its commands ({', '.join(sorted(p.commands))}) now suggest re-enabling when run.")


# ── add / remove ──────────────────────────────────────────────────────────────


@plugin_app.command("add")
@plugin_app.command("install", hidden=True)  # deprecated → add
def _plugin_add(
    source: str = typer.Argument(..., help="Local dir, .zip, Git URL, or marketplace name"),
):
    """Install a plugin (validated through the host before it lands)."""
    try:
        dest = _host().install(source)
    except ValueError as exc:
        ch.error("Could not install plugin", str(exc))
        raise typer.Exit(1) from exc
    ch.success(f"Installed plugin '{dest.name}' to {dest}")
    ch.dim("Skills/prompts/spaces are live now; legacy CLI commands need a restart.")


@plugin_app.command("remove")
@plugin_app.command("uninstall", hidden=True)  # deprecated → remove
def _plugin_remove(
    name: str = typer.Argument(..., help="Plugin id to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove an installed plugin directory (pip plugins: use pip uninstall)."""
    if not force and not typer.confirm(f"Remove plugin '{name}'?"):
        raise typer.Abort()
    try:
        p = _host().uninstall(name)
    except (KeyError, ValueError) as exc:
        ch.error(str(exc.args[0]) if exc.args else str(exc))
        raise typer.Exit(1) from exc
    ch.success(f"Removed plugin '{p.id}'")


# ── inspect ───────────────────────────────────────────────────────────────────


@plugin_app.command("inspect")
def _plugin_inspect(
    path: str = typer.Argument(..., help="Path to a plugin/package dir (Claude Code compatible)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the raw health report."),
):
    """Inspect a plugin/package (CC bundle + NAVIG personas/formations/spaces).

    Loads the package and reports its health — degraded parts are surfaced but
    never fatal. Accepts a Claude Code plugin unchanged, or a NAVIG superset.
    """
    from navig.plugins.package import load_package

    pkg = load_package(path)
    if json_out:
        import json as _json

        ch.console.print_json(_json.dumps({"summary": pkg.summary(), **pkg.health.to_dict()}))
        return

    state = pkg.health.state.value
    kind = "Claude Code plugin" if pkg.is_claude_compatible else "NAVIG package"
    colour = {"healthy": "green", "degraded": "yellow", "failed": "red"}.get(state, "dim")
    ch.console.print(f"[bold]{pkg.plugin_id}[/bold] — {kind} — state: [{colour}]{state}[/{colour}]")
    if pkg.health.error:
        ch.console.print(f"  [red]{pkg.health.error}[/red]")
    summary = pkg.summary()
    parts = " · ".join(f"{k}: {v}" for k, v in summary.items() if v)
    if parts:
        ch.console.print(f"  {parts}")
    for comp in pkg.health.degraded_components():
        ch.console.print(
            f"  [yellow]{comp.kind}:{comp.name}[/yellow] — {comp.state.value}: {comp.error}"
        )


# ── scaffolding ───────────────────────────────────────────────────────────────

_PLUGIN_PY = '''\
"""NAVIG plugin: {name}."""
from __future__ import annotations

import typer

name = "{name}"
version = "0.1.0"
description = "{description}"

# Self-registered as `navig {name} ...` when discovered.
app = typer.Typer(help=description, no_args_is_help=True)


@app.command("hello")
def hello() -> None:
    """Example command — run: navig {name} hello"""
    typer.echo("Hello from the {name} plugin!")


def check_dependencies() -> tuple[bool, list[str]]:
    """Return (ok, missing_packages). Keep checks cross-platform."""
    return True, []
'''

_PLUGIN_YAML = """\
name: {name}
version: 0.1.0
description: {description}
permissions: []
"""


@plugin_app.command("new")
def _plugin_new(
    name: str = typer.Argument(..., help="Plugin name (snake/kebab case)"),
    description: str = typer.Option("", "--description", "-d", help="One-line description."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing plugin."),
):
    """Scaffold a new plugin skeleton into ~/.navig/plugins/<name>/."""
    slug = _slug(name)
    if not slug:
        ch.error("Invalid plugin name.")
        raise typer.Exit(1)

    target = _user_plugins_dir() / slug
    pyfile = target / "plugin.py"
    if pyfile.exists() and not force:
        ch.warning(f"Plugin '{slug}' already exists.", details=str(pyfile))
        raise typer.Exit(1)

    target.mkdir(parents=True, exist_ok=True)
    desc = description or f"The {slug} plugin"
    pyfile.write_text(_PLUGIN_PY.format(name=slug, description=desc), encoding="utf-8")
    (target / "plugin.yaml").write_text(_PLUGIN_YAML.format(name=slug, description=desc), encoding="utf-8")
    (target / "requirements.txt").write_text("", encoding="utf-8")

    ch.success(f"Created plugin '{slug}'.", details=str(target))
    ch.info(f"Try it: navig {slug} hello")


# ── marketplaces (Claude Code compatible; moved from main.py) ────────────────

market_app = typer.Typer(
    name="marketplace",
    help="Manage plugin marketplaces (Claude Code compatible).",
    no_args_is_help=True,
)


@market_app.command("add")
def _marketplace_add(
    url: str = typer.Argument(..., help="Marketplace git URL or local directory."),
):
    """Register a marketplace (validates its marketplace.json first)."""
    from navig.plugins.marketplace import MarketplaceStore

    try:
        mkt = MarketplaceStore().add(url)
    except Exception as exc:  # noqa: BLE001 — surface, never crash
        ch.error("Could not add marketplace", str(exc)[:300])
        raise typer.Exit(1) from exc
    ch.success(f"Added marketplace '{mkt.name}' ({len(mkt.entries)} plugins)")
    ch.dim("Install one with: navig plugin add <name>")


@market_app.command("list")
def _marketplace_list():
    """List registered marketplaces and the plugins they advertise."""
    from navig.plugins.marketplace import MarketplaceStore, fetch_marketplace

    stores = MarketplaceStore().list_marketplaces()
    if not stores:
        ch.info("No marketplaces registered")
        ch.dim("Add one with: navig plugin marketplace add <url>")
        return
    for row in stores:
        try:
            mkt = fetch_marketplace(row.url)
            ch.heading(f"{row.name}  [dim]({row.url})[/dim]")
            for entry in mkt.entries:
                ver = f" {entry.version}" if entry.version else ""
                ch.dim(f"  • {entry.name}{ver} — {entry.description}")
        except Exception as exc:  # noqa: BLE001
            ch.warning(f"{row.name} unreachable — {str(exc)[:120]}")


@market_app.command("refresh")
def _marketplace_refresh(
    name: str = typer.Argument(None, help="Marketplace to refresh (default: all)."),
):
    """Re-fetch live catalogs so the Store's AVAILABLE rows aren't stale."""
    from navig.plugins.marketplace import MarketplaceStore

    results = MarketplaceStore().refresh(name)
    if not results:
        ch.info("No marketplaces registered" if name is None else f"'{name}' not registered")
        return
    for mkt_name, status in results:
        (ch.success if "plugins" in status else ch.warning)(f"{mkt_name}: {status}")


@market_app.command("remove")
def _marketplace_remove(
    name: str = typer.Argument(..., help="Marketplace name to remove."),
):
    """Unregister a marketplace."""
    from navig.plugins.marketplace import MarketplaceStore

    if MarketplaceStore().remove(name):
        ch.success(f"Removed marketplace '{name}'")
    else:
        ch.warning(f"Marketplace '{name}' is not registered")


plugin_app.add_typer(market_app, name="marketplace")
