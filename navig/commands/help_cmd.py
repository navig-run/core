"""NAVIG in-app help command implementation."""
from __future__ import annotations

from typing import Any

from navig.console_helper import get_console


def run_help(
    ctx: Any,
    topic: str | None,
    plain: bool,
    json_output: bool,
    raw: bool,
    schema_out: bool,
) -> None:
    """In-app help system — delegated from ``navig help``."""
    import json as jsonlib
    from pathlib import Path

    import typer
    from rich.table import Table

    from navig.cli._callbacks import show_subcommand_help
    from navig.cli.help_dictionaries import HELP_REGISTRY
    from navig.cli.registry import get_schema as _get_schema

    # --schema: emit the canonical command registry and exit
    if schema_out:
        typer.echo(jsonlib.dumps(_get_schema(), indent=2))
        raise typer.Exit()

    console = get_console()
    help_dir = Path(__file__).resolve().parent.parent / "help"
    schema = _get_schema()
    schema_commands = [
        row
        for row in schema.get("commands", [])
        if isinstance(row, dict) and str(row.get("path", "")).strip().startswith("navig ")
    ]
    manifest_topics = sorted(
        {
            str(row.get("path", "")).split()[1]
            for row in schema_commands
            if len(str(row.get("path", "")).split()) >= 2
        }
    )

    md_topics: list[str] = []
    if help_dir.exists():
        md_topics = sorted(
            {
                p.stem
                for p in help_dir.glob("*.md")
                if p.is_file() and p.stem.lower() not in {"readme"}
            }
        )

    registry_topics = sorted(set(HELP_REGISTRY.keys()) | set(manifest_topics))
    all_topics = sorted(set(md_topics) | set(registry_topics))

    want_json = bool(json_output or (ctx.obj and ctx.obj.get("json")))
    want_raw = bool(raw or (ctx.obj and ctx.obj.get("raw")))
    want_plain = plain or want_raw

    if not topic:
        if want_json:
            typer.echo(
                jsonlib.dumps(
                    {
                        "topics": all_topics,
                        "sources": {
                            "markdown": md_topics,
                            "registry": registry_topics,
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit()

        index_md = help_dir / "index.md"
        if index_md.exists():
            content = index_md.read_text(encoding="utf-8")
            if want_plain:
                console.print(content)
            else:
                from rich.markdown import Markdown

                console.print(Markdown(content))
        else:
            console.print("[bold cyan]NAVIG Help[/bold cyan]")
            console.print(
                "Use [yellow]navig help <topic>[/yellow] or [yellow]navig <cmd> --help[/yellow]."
            )

        if all_topics:
            console.print("\n[bold white]Topics[/bold white]")
            for name in all_topics:
                console.print(f"  - {name}")
        raise typer.Exit()

    normalized = topic.strip().lower()

    # Prefer markdown topic files if present.
    md_path = help_dir / f"{normalized}.md"
    if md_path.exists():
        content = md_path.read_text(encoding="utf-8")
        if want_json:
            typer.echo(
                jsonlib.dumps(
                    {"topic": normalized, "content": content, "source": "markdown"},
                    indent=2,
                    sort_keys=True,
                )
            )
        elif want_plain:
            console.print(content)
        else:
            from rich.markdown import Markdown

            console.print(Markdown(content))
        raise typer.Exit()

    # Fall back to generated registry topic index.
    if normalized in manifest_topics:
        topic_commands = [
            row
            for row in schema_commands
            if str(row.get("path", "")).startswith(f"navig {normalized}")
        ]

        if want_json:
            payload_commands = []
            for row in topic_commands:
                path = str(row.get("path", "")).strip()
                prefix = f"navig {normalized}"
                cmd_name = path[len(prefix):].strip() if path.startswith(prefix) else path
                payload_commands.append(
                    {
                        "name": cmd_name or normalized,
                        "path": path,
                        "description": row.get("summary", ""),
                        "status": row.get("status", "stable"),
                        "since": row.get("since", ""),
                    }
                )

            typer.echo(
                jsonlib.dumps(
                    {
                        "topic": normalized,
                        "desc": HELP_REGISTRY.get(normalized, {}).get("desc", ""),
                        "commands": payload_commands,
                        "source": "registry",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit()

        if want_plain:
            console.print(f"navig {normalized}")
            for row in topic_commands:
                path = str(row.get("path", "")).strip()
                summary = str(row.get("summary", "")).strip()
                console.print(f"  {path} - {summary}")
            raise typer.Exit()

        table = Table(box=None, show_header=False, padding=(0, 2), collapse_padding=True)
        table.add_column("Command", style="cyan")
        table.add_column("Description", style="dim")
        for row in topic_commands:
            path = str(row.get("path", "")).strip()
            summary = str(row.get("summary", "")).strip()
            if row.get("status") == "deprecated" and isinstance(row.get("deprecated"), dict):
                replacement = row["deprecated"].get("replaced_by")
                if replacement:
                    summary = f"{summary} (deprecated -> {replacement})"
            table.add_row(path, summary)

        console.print()
        console.print(f"[bold cyan]navig {normalized}[/bold cyan]")
        console.print("[dim]Generated command registry[/dim]")
        console.print(table)
        raise typer.Exit()

    # Final fallback to legacy centralized help registry.
    if normalized in HELP_REGISTRY:
        if want_json:
            typer.echo(
                jsonlib.dumps(
                    {
                        "topic": normalized,
                        "desc": HELP_REGISTRY[normalized].get("desc"),
                        "commands": HELP_REGISTRY[normalized].get("commands"),
                        "source": "registry",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit()

        show_subcommand_help(normalized, ctx)
        raise typer.Exit()

    from navig import console_helper as ch

    ch.error(
        f"Unknown help topic: {topic}",
        "Run 'navig help' to list topics or 'navig <cmd> --help' for command help.",
    )
    raise typer.Exit(1)
