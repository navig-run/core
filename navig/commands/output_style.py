"""
navig output-style — manage user-defined AI response format profiles.

Commands:
  navig output-style list              List all discovered output styles.
  navig output-style use <name>        Activate a style by name.
  navig output-style show <name>       Print a style's prompt text.
  navig output-style off               Clear the active style.
  navig output-style create <name>     Scaffold a new style file.
"""

from __future__ import annotations

from pathlib import Path

import typer
from typing_extensions import Annotated

from navig import console_helper as ch

output_style_app = typer.Typer(
    name="output-style",
    help="Manage user-defined AI response format profiles.",
    no_args_is_help=True,
)


@output_style_app.command("list")
def style_list() -> None:
    """List all discovered output styles (project, user, and built-in)."""
    from navig.output_styles import load_output_styles

    styles = load_output_styles()
    if not styles:
        ch.warning("No output styles found.")
        ch.dim(
            "  Add .md files with YAML frontmatter to .navig/output-styles/ "
            "or ~/.navig/output-styles/"
        )
        return

    try:
        from navig.output_styles import get_active_style

        active = get_active_style()
        active_name = active.name if active else None

        table = ch.Table(title="Output Styles")
        table.add_column("Name", style="cyan")
        table.add_column("Source", style="dim")
        table.add_column("Description")
        table.add_column("Active", justify="center")

        for s in styles:
            table.add_row(
                s.name,
                s.source,
                s.description or "[dim]—[/dim]",
                "[green]✓[/green]" if s.name == active_name else "",
            )

        ch.console.print(table)
    except Exception:  # noqa: BLE001
        for s in styles:
            marker = " [active]" if s.name == _active_name_safe() else ""
            typer.echo(f"{s.name}  ({s.source}){marker}")


def _active_name_safe() -> str | None:
    try:
        from navig.output_styles import get_active_style

        s = get_active_style()
        return s.name if s else None
    except Exception:  # noqa: BLE001
        return None


@output_style_app.command("use")
def style_use(
    name: Annotated[str, typer.Argument(help="Name of the style to activate.")],
) -> None:
    """Activate an output style by name."""
    from navig.output_styles import load_output_styles, set_active_style

    available = {s.name: s for s in load_output_styles()}
    if name not in available:
        ch.error(f"Style {name!r} not found.")
        if available:
            ch.dim("  Available styles: " + ", ".join(sorted(available)))
        raise typer.Exit(1)

    set_active_style(name)
    ch.success(f"Active output style set to: {name}")
    ch.dim(f"  {available[name].description}")


@output_style_app.command("off")
def style_off() -> None:
    """Clear the active output style (use default AI behaviour)."""
    from navig.output_styles import set_active_style

    set_active_style(None)
    ch.success("Output style cleared — using default AI response format.")


@output_style_app.command("show")
def style_show(
    name: Annotated[str, typer.Argument(help="Name of the style to display.")],
) -> None:
    """Print a style's prompt text and metadata."""
    from navig.output_styles import load_output_styles

    available = {s.name: s for s in load_output_styles()}
    style = available.get(name)
    if style is None:
        ch.error(f"Style {name!r} not found.")
        raise typer.Exit(1)

    typer.echo(f"\nName:        {style.name}")
    typer.echo(f"Source:      {style.source}")
    typer.echo(f"Description: {style.description or '—'}")
    typer.echo(f"Keep coding: {style.keep_coding_instructions}")
    typer.echo("\n── Prompt ──────────────────────────────────────")
    typer.echo(style.prompt)
    typer.echo("─" * 48 + "\n")


@output_style_app.command("create")
def style_create(
    name: Annotated[str, typer.Argument(help="Name for the new style.")],
    global_: Annotated[
        bool,
        typer.Option(
            "--global",
            "-g",
            help="Create in ~/.navig/output-styles/ (user-global) rather than project.",
        ),
    ] = False,
) -> None:
    """Scaffold a new output style file."""
    if global_:
        target_dir = Path.home() / ".navig" / "output-styles"
    else:
        target_dir = Path.cwd() / ".navig" / "output-styles"

    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{name}.md"

    if target_file.exists():
        ch.warning(f"Style file already exists: {target_file}")
        raise typer.Exit(1)

    template = f"""\
---
name: {name}
description: Describe what this style does.
keep-coding-instructions: true
---
Write your formatting instructions here.

For example:
- Always respond in bullet points.
- Use technical language; skip introductory phrases.
- Prefix code blocks with the language.
"""
    try:
        target_file.write_text(template, encoding="utf-8")
        ch.success(f"Created style file: {target_file}")
        ch.dim("  Edit it, then run: navig output-style use " + name)
    except OSError as exc:
        ch.error(f"Failed to create style file: {exc}")
        raise typer.Exit(1) from exc
