"""
NAVIG Formation CLI Commands

Manage profile-based agent formations.
"""

import json as json_module
import os
import tempfile
from pathlib import Path

import typer

from navig.lazy_loader import lazy_import
from navig.platform.paths import invocation_cwd, resolve_user_path

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
        import os as _os  # noqa: PLC0415

        if _os.environ.get("NAVIG_LAUNCHER", "fuzzy") == "legacy":
            from navig.cli import show_subcommand_help  # noqa: PLC0415

            show_subcommand_help("formation", ctx)
            raise typer.Exit()
        from navig.cli.launcher import smart_launch  # noqa: PLC0415

        smart_launch("formation", formation_app)


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
            ch.info("    .navig/formations/ (project — walked up from the cwd)")
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

    # A typed --workspace anchors to where the operator ran the command, and so
    # does the default: main.py chdir's into the active space first, so a bare
    # Path.cwd() would write .navig/profile.json into the space, not the project.
    ws = resolve_user_path(workspace) if workspace else invocation_cwd()
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

    _tmp_path: Path | None = None
    try:
        _fd, _tmp = tempfile.mkstemp(dir=profile_path.parent, suffix=".tmp")
        _tmp_path = Path(_tmp)
        with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
            _fh.write(json_module.dumps(profile_data, indent=2))
        os.replace(_tmp_path, profile_path)
        _tmp_path = None
    finally:
        if _tmp_path is not None:
            _tmp_path.unlink(missing_ok=True)
    ch.success(f"Profile set to '{profile}'")
    ch.info(f"  Written to: {profile_path}")


@formation_app.command("agents")
def formation_agents(
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List agents in the active formation (from .navig/profile.json)."""
    from navig.formations.loader import get_active_formation

    formation = get_active_formation(workspace_dir=_workspace_root())
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


# Tool names Claude Code understands in a subagent's `tools:` frontmatter. A
# formation's `tools[]` also holds navig ReAct tool names ("ai", "analysis") for
# `formation run` — those must never leak into Claude frontmatter, so `sync`
# emits a tools line ONLY when the whole list is inside this set; otherwise the
# line is omitted and the subagent inherits all tools.
_CC_TOOL_NAMES = frozenset({
    "Agent", "Artifact", "Bash", "Edit", "Glob", "Grep", "MultiEdit", "NotebookEdit",
    "PowerShell", "Read", "Skill", "Task", "TodoWrite", "WebFetch", "WebSearch", "Write",
})

_GEN_SYNC_HEADER = (
    "<!-- GENERATED from formation {src} by navig formation sync — do not edit by hand. -->"
)


def _yaml_quote(s: str) -> str:
    """A safe single-line double-quoted YAML scalar (same shape sync-agents uses)."""
    s = " ".join(s.split())
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _workspace_root() -> Path:
    """Nearest ancestor of the invocation cwd that holds `.navig/profile.json`.

    `formation init` writes the profile anchored to where the operator STANDS,
    but `read_profile` defaults to `Path.cwd()` — which `main.py` has already
    chdir'd into the active SPACE. Reading from there instead of the workspace
    is how a worktree's `schema-grid` activation silently resolved the space's
    `app_project` roster. Walk up from the invocation dir so root, subdir and
    worktree all find THEIR profile; fall back to the invocation dir (the
    default-profile fallback then behaves exactly as before).
    """
    start = invocation_cwd()
    for base in (start, *start.parents):
        if (base / ".navig" / "profile.json").exists():
            return base
    return start


@formation_app.command("sync")
def formation_sync(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would change; write nothing."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
):
    """Render the active formation's agents into .navig/agents/<id>.md (Claude Code).

    The missing bridge between formations (the canonical roster) and
    `.claude/agents` (what Claude Code actually loads — the wire junction points
    at `.navig/agents`). Frontmatter mirrors scripts/sync-agents.py's
    `_render_claude`: name, quoted description, optional tools, then the
    GENERATED marker and the composed system prompt. Idempotent: an unchanged
    file is not rewritten, so repeated syncs are byte-stable.
    """
    from navig.formations.loader import get_active_formation  # noqa: PLC0415
    from navig.platform.paths import invocation_cwd  # noqa: PLC0415

    formation = get_active_formation(workspace_dir=_workspace_root())
    if formation is None:
        ch.error("No active formation.", "Initialize one: navig formation init <formation-id>")
        raise typer.Exit(1)

    out_dir = invocation_cwd() / ".navig" / "agents"
    written: list[str] = []
    unchanged: list[str] = []
    missing: list[str] = []

    for aid in formation.agents:
        agent = formation.loaded_agents.get(aid)
        if agent is None:
            missing.append(aid)
            continue
        desc = " ".join((agent.role or f"{formation.id} agent").split())
        lines = ["---", f"name: {aid}", f"description: {_yaml_quote(desc)}"]
        if agent.tools and set(agent.tools) <= _CC_TOOL_NAMES:
            lines.append("tools: " + ", ".join(agent.tools))
        lines += ["---", "", _GEN_SYNC_HEADER.format(src=formation.id), ""]
        text = "\n".join(lines) + agent.system_prompt.strip() + "\n"

        target = out_dir / f"{aid}.md"
        rel = str(target)
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == text:
            unchanged.append(rel)
            continue
        if dry_run:
            written.append(rel)
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        written.append(rel)

    if json_output:
        print(json_module.dumps({
            "formation": formation.id,
            "written": written,
            "unchanged": unchanged,
            "missing": missing,
            "dry_run": dry_run,
        }, indent=2))
        return
    if missing:
        ch.warning(f"{len(missing)} agent(s) failed to load: {', '.join(missing)}")
    if dry_run:
        ch.info(f"Dry run — {len(written)} file(s) would change.", f"output: {out_dir}")
        return
    ch.success(
        f"Synced {formation.id}",
        f"{len(written)} written · {len(unchanged)} unchanged → {out_dir}",
    )
    ch.dim("Claude Code reads them through the .claude/agents junction (navig wire).")


@formation_app.command("run")
def formation_run(
    request: str = typer.Argument(..., help="The request to run across the formation"),
    max_workers: int = typer.Option(3, "--max-workers", "-w", help="Max specialists in parallel"),
):
    """Run a request across the active formation's specialists (real multi-agent).

    Each specialist runs as a parallel ReAct agent; results are synthesized (and,
    when the verifier is enabled, verified) into one answer.

    Examples:
        navig formation run "audit this repo for security and performance, then summarize"
    """
    import asyncio

    from navig.formations.coordinator import run_active_formation

    try:
        ch.info("Running the active formation… (specialists work in parallel)")
        result = asyncio.run(run_active_formation(request, max_workers=max_workers))
        if result.get("workers", 0) == 0:
            ch.warning(result.get("summary", "No active formation."))
            ch.info("  Initialize with: navig formation init <formation-id>")
            return
        ch.console.print(f"\n[bold cyan]Formation:[/bold cyan] {result.get('formation', '?')}  "
                         f"([green]{result['workers']}[/green] specialists, "
                         f"[red]{result['failed']}[/red] failed)\n")
        ch.console.print(result.get("summary", "(no summary)"))
        ch.console.print()
    except Exception as e:  # noqa: BLE001
        ch.error(f"Error: {e}")
        raise typer.Exit(1) from e


# `navig formation council` — the council deliberation, housed with the formation it runs
# across. Same implementation as the (now hidden, superseded) `navig council run`.
from navig.commands.council import council_run as _council_run  # noqa: E402

formation_app.command("council")(_council_run)
