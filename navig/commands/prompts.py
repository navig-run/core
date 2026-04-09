"""navig prompts — agent system-prompt management in .navig/store/prompts/."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from navig.console_helper import get_console
from navig.platform.paths import config_dir

prompts_app = typer.Typer(help="Manage agent system prompts", no_args_is_help=True)
console = get_console()

_PROMPTS_DIR = config_dir() / "store" / "prompts"


@prompts_app.command("list")
def prompts_list():
    """List saved system prompts."""
    _PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(_PROMPTS_DIR.glob("*.txt")) + sorted(_PROMPTS_DIR.glob("*.md"))
    if not files:
        console.print("[dim]No prompts found in[/dim] " + str(_PROMPTS_DIR))
        return
    for f in files:
        console.print(f"  [cyan]{f.stem}[/cyan]  [dim]{f.suffix}[/dim]")


@prompts_app.command("show")
def prompts_show(name: str = typer.Argument(..., help="Prompt name")):
    """Show a saved prompt."""
    _PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    for ext in (".txt", ".md", ""):
        target = _PROMPTS_DIR / (name + ext)
        if target.exists():
            console.print(target.read_text(encoding="utf-8"))
            return
    console.print(f"[red]Prompt not found:[/red] {name}")
    raise typer.Exit(1)


@prompts_app.command("edit")
def prompts_edit(name: str = typer.Argument(..., help="Prompt name")):
    """Open a prompt in the system editor."""
    import os

    _PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    target = _PROMPTS_DIR / f"{name}.md"
    editor = os.environ.get("EDITOR", "notepad" if os.name == "nt" else "nano")
    os.execlp(editor, editor, str(target))


@prompts_app.command("remove")
def prompts_remove(name: str = typer.Argument(..., help="Prompt name")):
    """Delete a prompt."""
    for ext in (".txt", ".md", ""):
        target = _PROMPTS_DIR / (name + ext)
        if target.exists():
            target.unlink()
            console.print(f"[green]Deleted:[/green] {target.name}")
            return
    console.print(f"[yellow]Not found:[/yellow] {name}")
