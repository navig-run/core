"""navig plugin — manage, install, inspect, and scaffold NAVIG plugins.

THE one plugin command group (the former inline group in main.py was merged
here). Thin wrappers over :class:`navig.plugins.host.PluginHost`, which unifies
the three install formats (CC/NAVIG package dirs, pip entry-point plugins,
legacy `plugin.py` Typer dirs). Canonical verbs: `add` / `remove`
(`install` / `uninstall` remain as hidden aliases).
"""

from __future__ import annotations

import re
from collections.abc import Container
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
    "shadowed": "[yellow]⚠[/yellow]",
    "degraded": "[yellow]~[/yellow]",
    "disabled": "[dim]○[/dim]",
    "failed": "[red]✗[/red]",
}
_PLUGIN_COLOR = {
    "wired": "green",
    "shadowed": "yellow",
    "degraded": "yellow",
    "disabled": "dim",
    "failed": "red",
}


def _plugin_state(p, shadowed: Container[str] = ()) -> str:
    """The table and banner's state word — see ``navig.plugins.host.wire_state``.

    Kept as a thin local alias because this module's readers (and its tests) have
    always spelled it this way; the PRECEDENCE itself lives in the plugin domain so
    the hub/store aggregator resolves the same word from the same code.
    """
    from navig.plugins.host import wire_state  # noqa: PLC0415

    return wire_state(p, shadowed)

@plugin_app.callback()
def _plugin_callback(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        # Explicit defaults — a direct call would pass typer.OptionInfo objects.
        _plugin_list(all_plugins=False, plain=False, json_out=False)
        raise typer.Exit()


# ── list / show ───────────────────────────────────────────────────────────────


def _emit_plugin_json(plugins, *, all_plugins: bool) -> None:
    """The machine view of `navig plugin list` — same facts the table shows.

    `--plain` cannot carry this: its five tab-separated columns are a scripting
    contract, so the `shadowed` state added for humans was invisible to every agent
    and script. That is the half of the audience that most needs it — a CI step or an
    agent checking plugin health could not tell that the CLI was running a stale copy.

    Emitted via `emit_json`, never the Rich console: `console.print(json.dumps(...))`
    hard-wraps at the console width once stdout is a pipe and corrupts the payload.
    """
    from navig.console_helper import emit_json  # noqa: PLC0415
    from navig.plugins.sources import audit_plugin_sources  # noqa: PLC0415

    audit = audit_plugin_sources()
    shadowed = frozenset(audit.shadowed) if audit else frozenset()
    shown = [p for p in plugins if all_plugins or p.enabled]

    rows = []
    counts: dict[str, int] = {}
    for p in shown:
        state = _plugin_state(p, shadowed)
        counts[state] = counts.get(state, 0) + 1
        rows.append({
            "id": p.id,
            "state": state,
            "version": p.version,
            "format": p.format,
            "source": p.source,
            "enabled": p.enabled,
            "description": p.description,
        })

    payload: dict[str, object] = {"plugins": rows, "counts": counts}
    if audit is not None:
        # Only when there is something to compare against — outside a development
        # checkout there is no declaration, and an empty block would read as 'nothing
        # is shadowed' rather than 'this was not checked'.
        payload["sources"] = {
            "declared": audit.declared,
            "verified": list(audit.verified),
            "shadowed": list(audit.shadowed),
            "unresolved": [{"id": d, "reason": why} for d, why in audit.unresolved],
            "fix_paths": list(audit.fix_paths),
        }
    emit_json(payload)


@plugin_app.command("list")
def _plugin_list(
    all_plugins: bool = typer.Option(False, "--all", "-a", help="Include disabled plugins"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
):
    """List installed plugins across every format (package, pip, legacy)."""
    plugins = _host().list_installed(refresh=True)

    if json_out:
        # BEFORE the empty-list branch on purpose: a script asked for JSON, and prose
        # ("No plugins installed") is not parseable. An empty install emits an empty
        # list, which a caller can read.
        _emit_plugin_json(plugins, all_plugins=all_plugins)
        return

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

    # Which plugins resolve to an installed copy instead of the source tree
    # `[tool.uv.sources]` declares. Run ONCE for the whole table (it touches the
    # filesystem); empty for anyone outside a development checkout, which is why an
    # end user never sees this state at all.
    from navig.plugins.sources import audit_plugin_sources  # noqa: PLC0415

    audit = audit_plugin_sources()
    shadowed = frozenset(audit.shadowed) if audit else frozenset()
    shadowed_shown = {p.id for p in shown if p.id in shadowed}

    # Wire-state summary banner — glyph + count + word (a broken/disabled count
    # only shows when there is one; wired always shows).
    counts: dict[str, int] = {}
    for p in shown:
        st = _plugin_state(p, shadowed)
        counts[st] = counts.get(st, 0) + 1
    banner = "  ·  ".join(
        f"{_PLUGIN_MARK[st]} {counts.get(st, 0)} {st}"
        for st in ("wired", "shadowed", "degraded", "disabled", "failed")
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
        st = _plugin_state(p, shadowed)
        fmt = f"{p.format}[dim] (convert → plugin-spec)[/dim]" if p.format == "legacy" else p.format
        desc = p.description[:50] + "…" if len(p.description) > 50 else p.description
        table.add_row(
            _PLUGIN_MARK[st], p.id, f"[{_PLUGIN_COLOR[st]}]{st}[/]",
            p.version, fmt, p.source, desc,
        )
    ch.console.print(table)
    # Report what THIS table shows. `audit.shadowed` covers every declared plugin,
    # but a distribution the host does not list as a plugin (navig-vault is a library
    # dependency) has no row here — so counting all of them made the footer say 4
    # above a table showing 3, which is the banner/table disagreement one level up.
    here = [d for d in audit.shadowed if d in shadowed_shown] if audit else []
    elsewhere = len(audit.shadowed) - len(here) if audit else 0
    if here:
        remedy = [path for dist, path in zip(audit.shadowed, audit.fix_paths) if dist in here]
        ch.console.print()
        ch.console.print(
            f"  [yellow]⚠ {len(here)} plugin(s) run an installed copy, not your "
            f"source[/yellow] — the CLI runs stale code while tests read the repo."
        )
        ch.dim("  fix → pip install -e " + " ".join(remedy))
        if elsewhere:
            ch.dim(
                f"  {elsewhere} more declared plugin(s) are shadowed but not listed "
                f"here — navig doctor"
            )
    ch.dim(
        "\n  enable/disable → navig plugin enable|disable <id>"
        "  ·  details → navig plugin show <id>"
    )


def _show_health_detail(p) -> None:
    """The per-component lines behind a failed/degraded status."""
    if p.health is None:
        return
    if p.health.error:
        ch.dim(f"  {p.health.error}")
    for comp in p.health.degraded_components():
        ch.dim(f"  {comp.kind}:{comp.name} — {comp.state.value}: {comp.error}")

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
    # The state WORD comes from the canonical resolver; only the per-state DETAIL is
    # local. This command used to re-derive the word from `enabled`/`error`/`health` by
    # hand — equivalent for all seven reachable combinations when measured, but nothing
    # bound the two, so a state added to `wire_state` would have reached the table and
    # never this view. `list` says `shadowed` while `show` says `wired` is exactly the
    # disagreement this state exists to prevent.
    from navig.plugins.sources import audit_plugin_sources  # noqa: PLC0415

    audit = audit_plugin_sources()
    state = _plugin_state(p, frozenset(audit.shadowed) if audit else frozenset())

    if state == "disabled":
        ch.warning("Status: disabled")
        ch.dim(f"Enable with: navig plugin enable {p.id}")
    elif state == "failed":
        # `error` is the legacy-format load error; a health FAILED carries its detail on
        # the health object instead, so fall through to the health lines below.
        if p.error:
            ch.error("Status: failed to load", p.error)
        else:
            ch.error("Status: failed")
        _show_health_detail(p)
    elif state == "degraded":
        ch.warning("Status: degraded")
        _show_health_detail(p)
    elif state == "shadowed":
        ch.warning("Status: shadowed — running an installed copy, not your source")
        remedy = dict(zip(audit.shadowed, audit.fix_paths)).get(p.id) if audit else None
        if remedy:
            ch.dim(f"  fix → pip install -e {remedy}")
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
def hello(who: str = typer.Argument("world", help="Who to greet")) -> None:
    """Example command — run: navig {name} hello"""
    if not who.strip():
        # A command that PRINTS a failure must EXIT non-zero, or the shell sees success
        # and `navig {name} hello && next-step` runs the next step anyway. The convention
        # across navig: typer.Exit(2) for a usage error (bad/missing argument, not found),
        # typer.Exit(1) for an operation failure (add `from exc` when an exception drove
        # it). Never print an error and simply return.
        typer.secho("who must not be empty", fg="red", err=True)
        raise typer.Exit(2)
    typer.echo(f"Hello, {{who}}, from the {name} plugin!")


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
