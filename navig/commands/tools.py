"""
navig.commands.tools — CLI command: navig tools

Introspect and document all registered tools.

Usage
-----
    navig tools list
    navig tools list --domain web
    navig tools list --detailed
    navig tools list --format json
    navig tools schema            # dump OpenAPI JSON
    navig tools schema --output openapi.json
"""

from __future__ import annotations

import json

import typer

tools_app = typer.Typer(
    name="tools",
    help="Inspect registered tools.",
    no_args_is_help=True,
)


def _get_registry():
    from navig.tools.router import get_tool_registry

    return get_tool_registry()


@tools_app.command("list")
def tools_list(
    domain: str | None = typer.Option(
        None,
        "--domain",
        "-d",
        help="Filter by domain (web, code, system, data, image, general).",
    ),
    detailed: bool = typer.Option(
        False,
        "--detailed",
        "-D",
        help="Show parameters schema for each tool.",
    ),
    available_only: bool = typer.Option(
        True,
        "--available/--all",
        help="Show only available tools (default) or all including disabled.",
    ),
    output_format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format: table | json | markdown.",
    ),
) -> None:
    """List all registered tools."""
    from navig.tools.router import ToolDomain

    registry = _get_registry()

    domain_enum = None
    if domain:
        try:
            domain_enum = ToolDomain(domain.lower())
        except ValueError as _exc:
            typer.echo(
                f"Unknown domain '{domain}'. Valid: {[d.value for d in ToolDomain]}",
                err=True,
            )
            raise typer.Exit(1) from _exc

    tools = registry.list_tools(available_only=available_only, domain=domain_enum)

    if not tools:
        typer.echo("No tools found.")
        raise typer.Exit(0)

    if output_format == "json":
        typer.echo(json.dumps([t.to_dict() for t in tools], indent=2))
        return

    if output_format == "markdown":
        typer.echo(registry.to_markdown_summary(available_only=available_only, domain=domain_enum))
        return

    # Default: Rich table
    try:
        from rich import box as rich_box
        from rich.console import Console
        from rich.table import Table

        console = Console()
        title = f"Tools ({len(tools)})"
        if domain:
            title += f" — domain: {domain}"

        table = Table(title=title, box=rich_box.SIMPLE, show_header=True, header_style="bold cyan")
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Domain", style="dim")
        table.add_column("Safety", style="dim")
        table.add_column("Status", style="dim")
        table.add_column("Description")

        for t in tools:
            safety_style = {
                "safe": "green",
                "moderate": "yellow",
                "dangerous": "red",
            }.get(t.safety.value, "white")
            table.add_row(
                t.name,
                t.domain.value,
                f"[{safety_style}]{t.safety.value}[/{safety_style}]",
                t.status.value,
                (t.description or "")[:72],
            )
            if detailed and t.parameters_schema:
                params_str = json.dumps(t.parameters_schema, separators=(",", ":"))
                table.add_row("", "", "", "", f"  [dim]params: {params_str[:80]}[/dim]")

        console.print(table)
    except ImportError:
        # Rich not available — plain text fallback
        for t in tools:
            typer.echo(f"{t.name:30} {t.domain.value:10} {t.safety.value:10} {t.description[:60]}")


@tools_app.command("schema")
def tools_schema(
    output: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write to file instead of stdout.",
    ),
    available_only: bool = typer.Option(
        True,
        "--available/--all",
        help="Include only available tools.",
    ),
) -> None:
    """Dump the OpenAPI schema for all registered tools."""
    registry = _get_registry()
    schema = registry.to_openapi_schema()
    text = json.dumps(schema, indent=2)

    if output:
        from pathlib import Path

        Path(output).write_text(text, encoding="utf-8")
        typer.echo(f"Schema written to {output}")
    else:
        typer.echo(text)


@tools_app.command("show")
def tools_show(
    name: str = typer.Argument(..., help="Tool name to inspect."),
) -> None:
    """Show full metadata for a single tool."""
    registry = _get_registry()
    meta = registry.get_tool(name)
    if meta is None:
        typer.echo(f"Tool '{name}' not found.", err=True)
        raise typer.Exit(1)

    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        lines = [
            f"[bold]{meta.name}[/bold]",
            f"Domain:  {meta.domain.value}",
            f"Safety:  {meta.safety.value}",
            f"Status:  {meta.status.value}",
            f"Desc:    {meta.description}",
        ]
        if meta.tags:
            lines.append(f"Tags:    {', '.join(meta.tags)}")
        if meta.module_path:
            lines.append(f"Handler: {meta.module_path}.{meta.handler_name}")
        if meta.parameters_schema:
            lines.append(f"Params:  {json.dumps(meta.parameters_schema, indent=2)}")
        if meta.output_schema:
            lines.append(f"Output:  {json.dumps(meta.output_schema, indent=2)}")
        console.print(Panel("\n".join(lines), title="Tool Details"))
    except ImportError:
        typer.echo(json.dumps(meta.to_dict(), indent=2))
