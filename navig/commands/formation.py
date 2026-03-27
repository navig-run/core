"""
NAVIG Formation CLI Commands

Manage profile-based agent formations.
"""

import json as json_module
from pathlib import Path

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

formation_app = typer.Typer(
    name="formation",
    help="Manage profile-based agent formations",
    invoke_without_command=True,
    no_args_is_help=False,
)


@formation_app.callback()
def formation_callback(ctx: typer.Context):
    """Formation commands - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        from navig.cli import show_subcommand_help

        show_subcommand_help("formation", ctx)
        raise typer.Exit()


@formation_app.command("list")
def formation_list(
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List all available formations."""
    from navig.formations.loader import list_available_formations

    formations = list_available_formations()

    if not formations:
        if plain:
            print("No formations found")
        else:
            ch.warning("No formations found.")
            ch.info("  Place formation directories in:")
            ch.info("    ./formations/ (project)")
            ch.info("    ~/.navig/formations/ (global)")
        return

    if json_output:
        data = [f.to_dict() for f in formations]
        print(json_module.dumps(data, indent=2))
        return

    if plain:
        for f in formations:
            aliases = ",".join(f.aliases) if f.aliases else ""
            print(f"{f.id}\t{f.name}\t{f.version}\t{len(f.agents)} agents\t{aliases}")
        return

    from rich.table import Table

    table = Table(title="Available Formations")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="white")
    table.add_column("Version", style="dim")
    table.add_column("Agents", justify="right")
    table.add_column("Aliases", style="dim")
    table.add_column("Description", style="dim", max_width=40)

    for f in formations:
        table.add_row(
            f.id,
            f.name,
            f.version,
            str(len(f.agents)),
            ", ".join(f.aliases) if f.aliases else "-",
            (f.description[:37] + "...") if len(f.description) > 40 else f.description,
        )

    ch.console.print(table)


@formation_app.command("show")
def formation_show(
    formation_id: str = typer.Argument(..., help="Formation ID or alias"),
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show detailed information about a formation."""
    from navig.formations.loader import discover_formations, load_formation

    formation_map = discover_formations()
    formation_dir = formation_map.get(formation_id)

    if formation_dir is None:
        available = ", ".join(sorted(formation_map.keys())) or "(none)"
        ch.error(f"Formation '{formation_id}' not found.")
        ch.info(f"  Available: {available}")
        raise typer.Exit(1)

    formation = load_formation(formation_dir)
    if formation is None:
        ch.error(f"Failed to load formation from {formation_dir}")
        raise typer.Exit(1)

    if json_output:
        data = formation.to_dict()
        data["loaded_agents"] = {aid: a.to_dict() for aid, a in formation.loaded_agents.items()}
        print(json_module.dumps(data, indent=2))
        return

    if plain:
        print(f"id={formation.id}")
        print(f"name={formation.name}")
        print(f"version={formation.version}")
        print(f"description={formation.description}")
        print(f"default_agent={formation.default_agent}")
        print(f"agents={','.join(formation.agents)}")
        print(f"aliases={','.join(formation.aliases)}")
        print(f"loaded={len(formation.loaded_agents)}/{len(formation.agents)}")
        return

    ch.console.print()
    ch.console.print(
        f"[bold cyan]{formation.name}[/bold cyan] [dim]({formation.id} v{formation.version})[/dim]"
    )
    ch.console.print(f"[dim]{formation.description}[/dim]")
    ch.console.print()
    ch.console.print(f"  Default agent: [yellow]{formation.default_agent}[/yellow]")
    ch.console.print(f"  Aliases: {', '.join(formation.aliases) if formation.aliases else '-'}")
    ch.console.print()

    from rich.table import Table

    table = Table(title="Agents", box=None)
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Role", style="dim")
    table.add_column("Weight", justify="right")
    table.add_column("Status")

    for agent_id in formation.agents:
        agent = formation.loaded_agents.get(agent_id)
        if agent:
            table.add_row(
                agent.id,
                agent.name,
                agent.role,
                f"{agent.council_weight:.1f}",
                "[green]loaded[/green]",
            )
        else:
            table.add_row(agent_id, "-", "-", "-", "[red]missing[/red]")

    ch.console.print(table)

    if formation.api_connectors:
        ch.console.print()
        ch.console.print("[bold]API Connectors:[/bold]")
        for conn in formation.api_connectors:
            ch.console.print(f"  - {conn.name} ({conn.type}): {conn.description}")


@formation_app.command("init")
def formation_init(
    profile: str = typer.Argument(..., help="Formation ID or alias to activate"),
    workspace: Path | None = typer.Option(
        None, "--workspace", "-w", help="Workspace directory (defaults to cwd)"
    ),
):
    """Initialize a profile for this workspace.

    Creates .navig/profile.json pointing to the specified formation.

    Examples:
        navig formation init navig_app
        navig formation init football_club
        navig formation init creative --workspace /path/to/project
    """
    from navig.formations.loader import discover_formations

    ws = workspace or Path.cwd()
    formation_map = discover_formations()

    if profile not in formation_map:
        available = ", ".join(sorted(formation_map.keys())) or "(none)"
        ch.error(f"Unknown formation: '{profile}'")
        ch.info(f"  Available: {available}")
        raise typer.Exit(1)

    navig_dir = ws / ".navig"
    navig_dir.mkdir(parents=True, exist_ok=True)

    profile_path = navig_dir / "profile.json"
    profile_data = {"version": 1, "profile": profile}

    profile_path.write_text(json_module.dumps(profile_data, indent=2), encoding="utf-8")
    ch.success(f"Profile set to '{profile}'")
    ch.info(f"  Written to: {profile_path}")


@formation_app.command("agents")
def formation_agents(
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List agents in the active formation (from .navig/profile.json)."""
    from navig.formations.loader import get_active_formation

    formation = get_active_formation()
    if formation is None:
        ch.warning("No active formation.")
        ch.info("  Initialize with: navig formation init <formation-id>")
        return

    if json_output:
        agents = {aid: a.to_dict() for aid, a in formation.loaded_agents.items()}
        print(json_module.dumps(agents, indent=2))
        return

    if plain:
        for aid, a in formation.loaded_agents.items():
            print(f"{aid}\t{a.name}\t{a.role}")
        return

    ch.console.print(f"\n[bold cyan]{formation.name}[/bold cyan] agents:\n")

    from rich.table import Table

    table = Table(box=None)
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Role", style="dim")
    table.add_column("Traits", style="dim", max_width=35)

    for aid in formation.agents:
        agent = formation.loaded_agents.get(aid)
        if agent:
            traits = ", ".join(agent.traits[:3])
            if len(agent.traits) > 3:
                traits += f" (+{len(agent.traits) - 3})"
            table.add_row(agent.id, agent.name, agent.role, traits)

    ch.console.print(table)
    ch.console.print()
