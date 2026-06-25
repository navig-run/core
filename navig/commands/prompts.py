"""navig prompts — discover, manage & export reusable prompts.

Prompts are a *distinct type* from skills (Claude's commands-vs-skills split).
Discovery spans every root (user store · packages · active workshop · Claude
commands) via ``navig.prompt_registry``; ``show``/``edit``/``remove`` operate on
the user store at ``~/.navig/store/prompts/``.
"""
from __future__ import annotations

from pathlib import Path

import typer

from navig.console_helper import get_console
from navig.platform.paths import config_dir

prompts_app = typer.Typer(help="Manage agent system prompts", no_args_is_help=True)
console = get_console()

_PROMPTS_DIR = config_dir() / "store" / "prompts"


@prompts_app.command("list")
def prompts_list(
    all_scopes: bool = typer.Option(
        False, "--all", "-a", help="Include builtin (internal LLM) prompts too."
    ),
):
    """List discovered prompts across all roots (user · package · space · claude)."""
    from collections import defaultdict

    from navig.prompt_registry import load_all_prompts

    prompts = load_all_prompts()
    if not all_scopes:
        prompts = [p for p in prompts if p.scope != "builtin"]
    if not prompts:
        console.print("[dim]No prompts found. Add one with[/dim] navig prompts new …")
        return

    by_scope: dict[str, list] = defaultdict(list)
    for p in prompts:
        by_scope[p.scope].append(p)
    for scope in ("user", "space", "package", "claude", "builtin"):
        items = by_scope.get(scope)
        if not items:
            continue
        console.print(f"[bold]{scope}[/bold]")
        for p in sorted(items, key=lambda x: x.id):
            desc = f"  [dim]{p.description}[/dim]" if p.description else ""
            console.print(f"  [cyan]{p.id}[/cyan]{desc}")


@prompts_app.command("export")
def prompts_export(
    name: str = typer.Argument(..., help="Prompt id (see `navig prompts list`)."),
    dest: Path = typer.Option(
        Path(".claude/commands"), "--dest", "-o",
        help="Output dir for the Claude slash command (.claude/commands).",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing export."),
):
    """Export a prompt as a Claude slash command (``.claude/commands/<id>.md``)."""
    from navig.prompt_registry import export_prompt

    try:
        out = export_prompt(name, dest, force=force)
    except (ValueError, FileExistsError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Exported[/green] {name} → {out}")


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
