"""
Project index commands for NAVIG.

Provides BM25/FTS-backed source indexing and search over workspace files.
"""

from pathlib import Path

import typer

from navig.console_helper import get_console
from navig.memory.project_indexer import ProjectIndexer

index_app = typer.Typer(
    help="Project source code indexer (BM25 search over workspace files)",
    invoke_without_command=True,
    no_args_is_help=True,
)


@index_app.command("scan")
def index_scan(
    ctx: typer.Context,
    root: str | None = typer.Argument(
        None, help="Project root directory (default: current directory)"
    ),
    incremental: bool = typer.Option(
        True,
        "--incremental/--full",
        help="Incremental scan (only changed files) or full rescan",
    ),
):
    """
    Scan and index project source code for BM25 search.

    Creates or updates a SQLite FTS5 index of all project files,
    chunked by function boundaries for code and paragraph boundaries for docs.

    Examples:
        navig index scan
        navig index scan /path/to/project --full
    """
    console = get_console()

    project_root = Path(root) if root else Path.cwd()
    if not project_root.is_dir():
        console.print(f"[red]Not a directory: {project_root}[/]")
        raise typer.Exit(1)

    with ProjectIndexer(project_root) as indexer:
        if incremental and indexer._file_hashes:
            console.print(f"[dim]Incremental scan of[/] [bold]{project_root}[/]")
            stats = indexer.update_incremental()
        else:
            console.print(f"[dim]Full scan of[/] [bold]{project_root}[/]")
            stats = indexer.scan()
        console.print(f"[green]✓[/] Indexed: {stats}")


@index_app.command("search")
def index_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query"),
    root: str | None = typer.Option(None, "--root", "-r", help="Project root directory"),
    top_k: int = typer.Option(10, "--top", "-k", help="Max results to return"),
):
    """
    Search the project index using BM25 ranking.

    Returns the most relevant code/doc chunks matching the query.

    Examples:
        navig index search "authentication middleware"
        navig index search "database connection" --top 5
    """
    console = get_console()

    project_root = Path(root) if root else Path.cwd()
    with ProjectIndexer(project_root) as indexer:
        if not indexer._file_hashes:
            console.print("[yellow]No index found. Run 'navig index scan' first.[/]")
            raise typer.Exit(1)

        results = indexer.search(query, top_k=top_k)
        if not results:
            console.print("[dim]No results found.[/]")
            raise typer.Exit(0)

        for result in results:
            score_str = f"[dim]({result.score:.2f})[/]"
            console.print(
                f"\n{score_str} [bold]{result.file_path}[/] L{result.start_line}-{result.end_line} [dim]({result.content_type})[/]"
            )
            lines = result.content.split("\n")[:5]
            for line in lines:
                console.print(f"  [dim]{line}[/]")
            if len(result.content.split("\n")) > 5:
                console.print(
                    f"  [dim]... ({len(result.content.split(chr(10)))} lines total)[/]"
                )


@index_app.command("stats")
def index_stats(
    ctx: typer.Context,
    root: str | None = typer.Option(None, "--root", "-r", help="Project root directory"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show project index statistics.

    Examples:
        navig index stats
        navig index stats --json
    """
    import json

    console = get_console()

    project_root = Path(root) if root else Path.cwd()
    with ProjectIndexer(project_root) as indexer:
        stats = indexer.stats()
        if json_out:
            console.print(json.dumps(stats, indent=2))
        else:
            console.print(f"[bold]Project Index Stats[/] — {project_root}")
            for key, value in stats.items():
                console.print(f"  {key}: [cyan]{value}[/]")


@index_app.command("drop")
def index_drop(
    ctx: typer.Context,
    root: str | None = typer.Option(None, "--root", "-r", help="Project root directory"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Drop the project index (removes SQLite database).

    Examples:
        navig index drop
        navig index drop --yes
    """
    console = get_console()

    project_root = Path(root) if root else Path.cwd()

    if not yes:
        confirmed = typer.confirm(f"Drop index for {project_root}?")
        if not confirmed:
            raise typer.Exit(0)

    with ProjectIndexer(project_root) as indexer:
        indexer.drop_index()
        console.print("[green]✓[/] Index dropped")
