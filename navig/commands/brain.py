"""Brain commands — system prompts, agent context, soul management.

Provides `navig brain prompts <list|get|set|reload>` for managing
agent system-prompt files stored at:

  Project-local:  <workspace>/.navig/brain/prompts/<slug>.md
  Global:         ~/.navig/brain/prompts/<slug>.md

Project-local files shadow global ones (same slug → project wins).
navig-bridge calls `navig brain prompts get <slug>` to fetch a prompt
without hard-coding prompt strings in TypeScript.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

# ---------------------------------------------------------------------------
# Root app
# ---------------------------------------------------------------------------
brain_app = typer.Typer(
    name="brain",
    help="Brain — system prompts, soul context, agent configuration.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)

# ---------------------------------------------------------------------------
# prompts sub-app
# ---------------------------------------------------------------------------
prompts_app = typer.Typer(
    name="prompts",
    help="Manage agent system-prompt files (.navig/brain/prompts/).",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)
brain_app.add_typer(prompts_app, name="prompts")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prompt_dirs() -> list[Path]:
    """Return search dirs in priority order: project-local → global."""
    dirs: list[Path] = []

    # Walk up from cwd to find the nearest project .navig/brain/prompts/
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".navig" / "brain" / "prompts"
        if candidate.is_dir():
            dirs.append(candidate)
            break

    # Always include the global dir (may or may not exist yet)
    global_dir = Path.home() / ".navig" / "brain" / "prompts"
    if global_dir not in dirs:
        dirs.append(global_dir)

    return dirs


def _resolve(slug: str) -> Path | None:
    """Return the first file matching <slug>.md in search dirs."""
    filename = f"{slug}.md" if not slug.endswith(".md") else slug
    for d in _prompt_dirs():
        candidate = d / filename
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# navig brain prompts list
# ---------------------------------------------------------------------------


@prompts_app.command("list")
def prompts_list(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON array of slugs."),
) -> None:
    """List all available prompt slugs (project-local + global, merged)."""
    seen: dict[str, Path] = {}
    for d in _prompt_dirs():
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            slug = f.stem
            if slug not in seen:
                seen[slug] = f

    if json_output:
        import json

        typer.echo(json.dumps(sorted(seen.keys())))
        return

    if not seen:
        typer.echo("No prompts found. Create .navig/brain/prompts/<slug>.md")
        return

    for slug, filepath in sorted(seen.items()):
        scope = (
            "project"
            if ".navig/brain/prompts" in filepath.as_posix()
            and filepath.is_relative_to(Path(".navig").resolve().parent)
            else "global"
        )
        typer.echo(f"  {slug:<30}  ({scope})  {filepath}")


# ---------------------------------------------------------------------------
# navig brain prompts get
# ---------------------------------------------------------------------------


@prompts_app.command("get")
def prompts_get(
    slug: str = typer.Argument(..., help="Prompt slug (filename without .md)."),
    json_output: bool = typer.Option(False, "--json", help="Wrap output in JSON envelope."),
) -> None:
    """Output prompt content — intended for programmatic consumption by navig-bridge/CLI."""
    filepath = _resolve(slug)
    if filepath is None:
        typer.echo(f"Prompt not found: {slug}", err=True)
        raise typer.Exit(1)

    content = filepath.read_text(encoding="utf-8")

    if json_output:
        import json

        typer.echo(json.dumps({"slug": slug, "content": content, "path": str(filepath)}))
    else:
        typer.echo(content, nl=False)


# ---------------------------------------------------------------------------
# navig brain prompts set
# ---------------------------------------------------------------------------


@prompts_app.command("set")
def prompts_set(
    slug: str = typer.Argument(..., help="Prompt slug (filename without .md)."),
    content: str | None = typer.Option(
        None, "--content", "-c", help="Prompt text. Reads from stdin if omitted."
    ),
    global_scope: bool = typer.Option(
        False,
        "--global",
        "-g",
        help="Write to ~/.navig/brain/prompts/ (default: project-local).",
    ),
) -> None:
    """Write or overwrite a prompt file."""
    if content is None:
        if sys.stdin.isatty():
            typer.echo("Enter prompt content (Ctrl+D to finish):", err=True)
        content = sys.stdin.read()

    if global_scope:
        target_dir = Path.home() / ".navig" / "brain" / "prompts"
    else:
        # Find project root or fall back to cwd
        cwd = Path.cwd()
        project_root: Path = cwd
        for parent in [cwd, *cwd.parents]:
            if (parent / ".navig").is_dir():
                project_root = parent
                break
        target_dir = project_root / ".navig" / "brain" / "prompts"

    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{slug}.md"
    target_file.write_text(content, encoding="utf-8")
    typer.echo(f"Wrote prompt '{slug}' → {target_file}")


# ---------------------------------------------------------------------------
# navig brain prompts reload
# ---------------------------------------------------------------------------


@prompts_app.command("reload")
def prompts_reload() -> None:
    """Signal the NAVIG daemon to flush its prompt cache (no-op if daemon is not running)."""
    try:
        from navig.daemon.client import send_command  # type: ignore[import]

        send_command({"type": "cache_flush", "target": "prompts"})
        typer.echo("Prompt cache flushed.")
    except Exception:
        # Daemon may not be running — that's fine, cache is in-process anyway.
        typer.echo("Daemon not running — prompt cache cleared on next start.")
