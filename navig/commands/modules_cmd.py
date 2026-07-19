"""navig modules — list / enable / disable / inspect operator modules.

Modules are the tier-gated capability units surfaces render (Finance · DevOps ·
Goals · Life · Projects · launchers · plugins). This command reads the canonical
registry (`navig.modules.registry`) — the same data `GET /api/deck/modules` serves
the deck + navig-os — so the CLI and the GUIs never drift.
"""

from __future__ import annotations

import typer

from navig import console_helper as ch

modules_app = typer.Typer(
    name="modules",
    help="[deprecated → navig store] List and toggle operator modules.",
    no_args_is_help=False,
    invoke_without_command=True,
)


@modules_app.callback()
def _modules_callback(ctx: typer.Context):
    ch.dim("[deprecated] `navig modules` is folding into `navig store` — one hub for everything.")
    if ctx.invoked_subcommand is None:
        _modules_list(dev=False)
        raise typer.Exit()


@modules_app.command("list")
def _modules_list(
    dev: bool = typer.Option(False, "--dev", help="Include dev-only modules."),
):
    """List all modules with their tier lock + enabled state."""
    from rich.table import Table

    from navig.modules.registry import CATEGORY_LABELS, get_registry

    rows = get_registry().discover().list_modules(include_dev=dev)
    if not rows:
        ch.info("No modules registered.")
        return

    table = Table(title="NAVIG Modules")
    table.add_column("Module", style="cyan")
    table.add_column("Kind", style="dim")
    table.add_column("Category", style="dim")
    table.add_column("Access", style="bold")
    table.add_column("State", style="bold")

    for m in rows:
        if m["locked"]:
            access = f"[yellow]lock: {m['min_tier'] or m['capability']}[/yellow]"
        else:
            access = "[green]included[/green]"
        state = "[green]on[/green]" if m["enabled"] else "[dim]off[/dim]"
        table.add_row(
            f"{m['label']}  [dim]{m['id']}[/dim]",
            m["kind"],
            CATEGORY_LABELS.get(m["category"], m["category"]),
            access,
            state,
        )
    ch.console.print(table)


@modules_app.command("enable")
def _modules_enable(
    module_id: str = typer.Argument(..., help="Module id (e.g. finance, devops)."),
):
    """Enable a module for this operator (persisted override)."""
    from navig.modules.registry import get_registry

    reg = get_registry().discover()
    m = reg.get(module_id)
    if m is None:
        ch.error(f"Unknown module '{module_id}'")
        raise typer.Exit(1)
    reg.set_enabled(module_id, True)
    ch.success(f"Enabled '{m.label}'")


@modules_app.command("disable")
def _modules_disable(
    module_id: str = typer.Argument(..., help="Module id to disable."),
):
    """Disable a module for this operator (persisted override)."""
    from navig.modules.registry import get_registry

    reg = get_registry().discover()
    m = reg.get(module_id)
    if m is None:
        ch.error(f"Unknown module '{module_id}'")
        raise typer.Exit(1)
    reg.set_enabled(module_id, False)
    ch.success(f"Disabled '{m.label}'")


@modules_app.command("info")
def _modules_info(
    module_id: str = typer.Argument(..., help="Module id to inspect."),
):
    """Show a module's kind, tier lock, surfaces, and requirements."""
    from navig.modules.registry import get_registry

    reg = get_registry().discover()
    m = reg.get(module_id)
    if m is None:
        ch.error(f"Unknown module '{module_id}'")
        raise typer.Exit(1)
    rendered = next((r for r in reg.list_modules(include_dev=True) if r["id"] == module_id), None)

    ch.heading(f"{m.label}  [dim]({m.id})[/dim]")
    ch.dim(m.description)
    ch.dim(f"kind: {m.kind.value}   category: {m.category}   source: {m.source}")
    if m.capability:
        locked = rendered and rendered["locked"]
        access = f"requires '{m.capability}'" + (f" ({m.min_tier}+)" if m.min_tier else "")
        (ch.warning if locked else ch.success)(
            f"access: {access} — {'LOCKED' if locked else 'included in your tier'}"
        )
    else:
        ch.success("access: free (all tiers)")
    if m.surfaces:
        ch.dim("surfaces: " + ", ".join(m.surfaces))
    if m.requires:
        ch.dim("requires: " + ", ".join(m.requires))
    ch.dim(f"state: {'enabled' if reg.is_enabled(module_id) else 'disabled'}")
