"""navig capabilities — what this install can do, and what an agent will be told.

The same data the MCP ``navig_capabilities`` tool returns, for the operator: installed
plugins, the accounts/keys/devices each can use, and next steps for a file.
"""

from __future__ import annotations

import json

import typer

from navig import console_helper as ch

capabilities_app = typer.Typer(
    help="What this install can do: plugins, connected accounts, next steps for a file.",
    invoke_without_command=True,
)

_STATE = {
    "connected": "[green]● connected[/green]",
    "expires": "[yellow]● expiring[/yellow]",
    "rejected": "[red]✗ rejected[/red]",
    "missing": "[dim]○ —[/dim]",
    "unknown": "[dim]? unknown[/dim]",
}


@capabilities_app.callback()
def capabilities_overview(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    refresh: bool = typer.Option(False, "--refresh", help="Re-run the account probes"),
) -> None:
    """Installed plugins and the accounts each can act as."""
    if ctx.invoked_subcommand:
        return
    from navig.capabilities import overview
    from navig.capabilities.probes import clear_cache
    from navig.console_helper import Table

    if refresh:
        clear_cache()
    data = overview()
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Plugin", style="bold", no_wrap=True)
    table.add_column("Cmds", justify="right", no_wrap=True)
    table.add_column("Accounts")  # the one wrappable column
    table.add_column("Next step", no_wrap=True)
    for plugin in data["plugins"]:
        accounts = plugin.get("accounts") or []
        live = [a for a in accounts if a["state"] != "missing"]
        cell = ", ".join(
            f"{a['provider']} {a['label']}".strip() + " " + _STATE.get(a["state"], a["state"])
            for a in live[:6]
        )
        if len(live) > 6:
            cell += f" [dim]+{len(live) - 6}[/dim]"
        missing = [a for a in accounts if a["state"] == "missing"]
        nxt = missing[0]["next_step"] if missing and missing[0].get("next_step") else ""
        short = plugin["plugin"].removeprefix("navig-")
        verb = short if short in plugin["verbs"] else (plugin["verbs"] or [short])[0]
        table.add_row(
            plugin["plugin"].removeprefix("navig-"),
            str(plugin["commands"] or "—"),
            cell or ("[dim]no probe[/dim]" if not plugin.get("probe") else "[dim]—[/dim]"),
            f"[cyan]{nxt}[/cyan]" if nxt else f"[dim]navig {verb} --help[/dim]",
        )
    ch.console.print(table)
    if data["probes_failed"]:
        ch.warning("Could not verify: " + ", ".join(data["probes_failed"]))
    ch.console.print(
        "\n  [dim]Agents see this through MCP navig_capabilities · next steps for a file:[/dim] "
        "[cyan]navig capabilities suggest <file>[/cyan]"
    )


@capabilities_app.command("suggest")
def capabilities_suggest(
    file: str = typer.Argument(..., help="Image, video, audio file or fan-out brief"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Next steps for a file, using only installed commands and connected accounts."""
    from navig.capabilities import suggest
    from navig.console_helper import Table
    from navig.platform.paths import resolve_user_path

    data = suggest(str(resolve_user_path(file)))
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    if data["kind"] is None:
        ch.warning("Unknown file type.", "Try: navig capabilities find \"<goal>\"")
        raise typer.Exit(1)
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Gate", no_wrap=True)
    table.add_column("Step")
    table.add_column("Command", no_wrap=True)
    for step in data["steps"]:
        gate = {"safe": "[green]read[/green]", "moderate": "[yellow]asks[/yellow]",
                "dangerous": "[red]approve[/red]"}[step["safety"]]  # fmt: skip
        table.add_row(gate, step["label"], f"{step['path']} {' '.join(step['args'])}")
    ch.console.print(table)
    for unlock in data["unlocks"]:
        ch.console.print(f"  [dim]○ {unlock['label']} — connect with[/dim] "
                         f"[cyan]{unlock['connect']}[/cyan]")


@capabilities_app.command("find")
def capabilities_find(
    query: str = typer.Argument(..., help="The goal, in plain words"),
    limit: int = typer.Option(8, "--limit", "-n", help="Maximum commands"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Installed commands that match a goal."""
    from navig.capabilities import find
    from navig.console_helper import Table

    rows = find(query, limit=limit)
    if as_json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Command", style="cyan", no_wrap=True)
    table.add_column("Plugin", no_wrap=True)
    table.add_column("What it does")
    for row in rows:
        table.add_row(row["path"], row["plugin"].removeprefix("navig-"), row["summary"])
    ch.console.print(table)
