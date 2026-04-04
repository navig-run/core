"""
Community asset installer — install/manage skills, playbooks, workflows, etc.

Extracted from navig/cli/__init__.py.
"""

from __future__ import annotations

import typer

from navig import console_helper as ch

# ============================================================================
# Business-logic stubs — not yet implemented
# ============================================================================
# These functions were referenced by the inline install_app but never shipped.
# They are defined here so that the CLI commands register cleanly and emit a
# helpful message at invocation time rather than a raw ImportError.


def _not_implemented(name: str):
    ch.warning(f"'{name}' is not yet implemented.")
    ch.info("  Track progress: https://github.com/navig-run/core/issues")


def list_assets(plain: bool = False):
    _not_implemented("install list")


def install_asset(spec: str, force: bool = False, dry_run: bool = False):
    _not_implemented("install add")


def remove_asset(spec: str, force: bool = False):
    _not_implemented("install remove")


def update_assets(spec: str | None = None, dry_run: bool = False):
    _not_implemented("install update")


def show_asset(spec: str):
    _not_implemented("install show")


def freeze_assets(plain: bool = False):
    _not_implemented("install freeze")


def status_assets():
    _not_implemented("install status")


def search_assets(
    query: str,
    asset_type: str | None = None,
    force_refresh: bool = False,
) -> list[dict]:
    _not_implemented("install search")
    return []


def browse_assets(
    asset_type: str | None = None,
    force_refresh: bool = False,
) -> list[dict]:
    _not_implemented("install browse")
    return []


# ============================================================================
# install_app — Typer CLI group (extracted from navig/cli/__init__.py)
# ============================================================================

install_app = typer.Typer(
    help="Install community assets (skills, playbooks, workflows, …) from GitHub.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@install_app.callback()
def install_callback(ctx: typer.Context):
    """Install community assets from GitHub into store/."""
    if ctx.invoked_subcommand is None:
        list_assets()
        raise typer.Exit()


@install_app.command("add")
def install_add(
    ctx: typer.Context,
    spec: str = typer.Argument(..., help="type:owner/repo[@ref]  e.g. skill:myuser/my-skill"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if already installed."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing files."),
):
    """Install an asset from GitHub.

    SPEC format: <type>:<owner>/<repo>[@ref]

    Types: skill, playbook, workflow, formation, stack, plugin, tool, prompt, webflow,
    blueprint, deck

    Examples:

      navig install add skill:myuser/my-skill

      navig install add playbook:myorg/ops-pack@v1.2.0 --force
    """
    try:
        install_asset(spec, force=force, dry_run=dry_run)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("list")
def install_list(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
):
    """List installed community assets."""
    list_assets(plain=plain)


@install_app.command("remove")
def install_remove(
    ctx: typer.Context,
    spec: str = typer.Argument(..., help="type:owner/repo  or  type/name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation."),
):
    """Remove an installed community asset."""
    try:
        remove_asset(spec, force=force)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("update")
def install_update(
    ctx: typer.Context,
    spec: str = typer.Argument(None, help="Specific asset to update (omit to update all)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without changes."),
):
    """Update one or all installed assets to latest."""
    try:
        update_assets(spec, dry_run=dry_run)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("upgrade")
def install_upgrade(
    ctx: typer.Context,
    spec: str = typer.Argument(None, help="Specific asset (omit to upgrade all)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without changes."),
):
    """Upgrade all installed assets (alias for update)."""
    try:
        update_assets(spec, dry_run=dry_run)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("show")
def install_show(
    ctx: typer.Context,
    spec: str = typer.Argument(..., help="type:owner/repo"),
):
    """Show details of an installed asset."""
    try:
        show_asset(spec)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("freeze")
def install_freeze(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
):
    """Print installed assets as type/name==version specs."""
    freeze_assets(plain=plain)


@install_app.command("status")
def install_status(ctx: typer.Context):
    """Show health of all installed assets."""
    status_assets()


@install_app.command("search")
def install_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query (name, description, tags)"),
    type_filter: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by asset type: skill, playbook, workflow, plugin, …",
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Force registry re-fetch."),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
    json_out: bool = typer.Option(False, "--json", help="JSON output."),
) -> None:
    """
    Search the NAVIG community registry.

    Examples:
        navig install search docker
        navig install search backup --type playbook
        navig install search git --refresh
    """
    results = search_assets(query, asset_type=type_filter, force_refresh=refresh)

    if json_out:
        import json as _json

        ch.print(_json.dumps(results, indent=2, ensure_ascii=False))
        return

    if not results:
        ch.warn(f"No assets found matching {query!r}.")
        ch.dim("  Try 'navig install browse' to see all available assets.")
        return

    if plain:
        for asset in results:
            ch.print(
                f"{asset.get('type', '?')}:{asset.get('repo', asset.get('name', '?'))}"
                f"  — {asset.get('description', '')}"
            )
        return

    from rich.table import Table

    table = Table(title=f"Registry search: {query!r}", show_lines=False)
    table.add_column("Type", style="dim", width=10)
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Install", style="green")

    for asset in results:
        install_spec = f"{asset.get('type', '?')}:{asset.get('repo', asset.get('name', ''))}"
        table.add_row(
            asset.get("type", "?"),
            asset.get("name", "?"),
            asset.get("description", "")[:60],
            f"navig install add {install_spec}",
        )

    ch.console.print(table)
    ch.dim(f"\n{len(results)} result(s). Install with: navig install add <type>:<repo>")


@install_app.command("browse")
def install_browse(
    ctx: typer.Context,
    type_filter: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by asset type: skill, playbook, workflow, plugin, …",
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Force registry re-fetch."),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
    json_out: bool = typer.Option(False, "--json", help="JSON output."),
) -> None:
    """
    Browse the NAVIG community registry.

    Examples:
        navig install browse
        navig install browse --type skill
        navig install browse --type playbook --plain
    """
    assets = browse_assets(asset_type=type_filter, force_refresh=refresh)

    if json_out:
        import json as _json

        ch.print(_json.dumps(assets, indent=2, ensure_ascii=False))
        return

    if not assets:
        label = f" of type {type_filter!r}" if type_filter else ""
        ch.warn(f"Registry is empty{label}.")
        ch.dim("  Check your internet connection or run with --refresh.")
        return

    if plain:
        for asset in assets:
            ch.print(
                f"{asset.get('type', '?')}  {asset.get('name', '?')}  "
                f"{asset.get('description', '')}"
            )
        return

    from rich.table import Table

    title = "Community Registry" + (f" — {type_filter}" if type_filter else "")
    table = Table(title=title, show_lines=False)
    table.add_column("Type", style="dim", width=10)
    table.add_column("Name", style="cyan")
    table.add_column("Author", style="dim")
    table.add_column("Description")

    for asset in assets:
        table.add_row(
            asset.get("type", "?"),
            asset.get("name", "?"),
            asset.get("author", "—"),
            asset.get("description", "")[:60],
        )

    ch.console.print(table)
    ch.dim(f"\n{len(assets)} asset(s). Install: navig install add <type>:<repo>")
