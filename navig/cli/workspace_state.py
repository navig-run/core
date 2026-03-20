"""Context, indexing, and history command groups for `navig cli`."""
from __future__ import annotations

from typing import Optional

import typer

context_app = typer.Typer(
    help="Manage host/app context for current project",
    invoke_without_command=True,
    no_args_is_help=False,
)


@context_app.callback()
def context_callback(ctx: typer.Context):
    """Context management - shows current context if no subcommand."""
    if ctx.invoked_subcommand is None:
        from navig.commands.context import show_context
        show_context(ctx.obj)
        raise typer.Exit()


@context_app.command("show")
def context_show(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="One-line output for scripting"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show current context resolution.

    Displays which host/app is active and where the context is resolved from
    (environment variable, project config, user cache, or default).
    """
    from navig.commands.context import show_context
    ctx.obj['plain'] = plain
    if json_out:
        ctx.obj['json'] = True
    show_context(ctx.obj)


@context_app.command("set")
def context_set(
    ctx: typer.Context,
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Host to set as project default"),
    app_name: Optional[str] = typer.Option(None, "--app", "-a", help="App to set as project default"),
):
    """Set project-local context in `.navig/config.yaml`."""
    from navig.commands.context import set_context
    set_context(host=host, app=app_name, opts=ctx.obj)


@context_app.command("clear")
def context_clear(ctx: typer.Context):
    """Clear project-local context."""
    from navig.commands.context import clear_context
    clear_context(ctx.obj)


@context_app.command("init")
def context_init(ctx: typer.Context):
    """Initialize `.navig` directory in current project."""
    from navig.commands.context import init_context
    init_context(ctx.obj)


index_app = typer.Typer(
    help="Project source code indexer (BM25 search over workspace files)",
    invoke_without_command=True,
    no_args_is_help=True,
)


@index_app.command("scan")
def index_scan(
    ctx: typer.Context,
    root: Optional[str] = typer.Argument(None, help="Project root directory (default: current directory)"),
    incremental: bool = typer.Option(True, "--incremental/--full", help="Incremental scan (only changed files) or full rescan"),
):
    """Scan and index project source code for BM25 search."""
    from pathlib import Path

    from rich.console import Console as _Con

    from navig.memory.project_indexer import ProjectIndexer

    console = _Con()
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
    root: Optional[str] = typer.Option(None, "--root", "-r", help="Project root directory"),
    top_k: int = typer.Option(10, "--top", "-k", help="Max results to return"),
):
    """Search the project index using BM25 ranking."""
    from pathlib import Path

    from rich.console import Console as _Con

    from navig.memory.project_indexer import ProjectIndexer

    console = _Con()
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
            console.print(f"\n{score_str} [bold]{result.file_path}[/] L{result.start_line}-{result.end_line} [dim]({result.content_type})[/]")
            lines = result.content.split('\n')[:5]
            for line in lines:
                console.print(f"  [dim]{line}[/]")
            if len(result.content.split('\n')) > 5:
                console.print(f"  [dim]... ({len(result.content.split(chr(10)))} lines total)[/]")


@index_app.command("stats")
def index_stats(
    ctx: typer.Context,
    root: Optional[str] = typer.Option(None, "--root", "-r", help="Project root directory"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show project index statistics."""
    import json
    from pathlib import Path

    from rich.console import Console as _Con

    from navig.memory.project_indexer import ProjectIndexer

    console = _Con()
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
    root: Optional[str] = typer.Option(None, "--root", "-r", help="Project root directory"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Drop the project index (removes SQLite database)."""
    from pathlib import Path

    from rich.console import Console as _Con

    from navig.memory.project_indexer import ProjectIndexer

    console = _Con()
    project_root = Path(root) if root else Path.cwd()

    if not yes:
        import typer as _typer
        confirmed = _typer.confirm(f"Drop index for {project_root}?")
        if not confirmed:
            raise typer.Exit(0)

    with ProjectIndexer(project_root) as indexer:
        indexer.drop_index()
        console.print("[green]✓[/] Index dropped")


history_app = typer.Typer(
    help="Command history, replay, and audit trail",
    invoke_without_command=True,
    no_args_is_help=False,
)


@history_app.callback()
def history_callback(ctx: typer.Context):
    """History management - shows recent history if no subcommand."""
    if ctx.invoked_subcommand is None:
        from navig.commands.history import show_history
        show_history(limit=20, opts=ctx.obj)
        raise typer.Exit()


@history_app.command("list")
def history_list(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-l", help="Number of entries to show"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Filter by host"),
    type_filter: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by operation type"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status (success/failed)"),
    search: Optional[str] = typer.Option(None, "--search", "-q", help="Search in command text"),
    since: Optional[str] = typer.Option(None, "--since", help="Time filter (e.g., 1h, 24h, 7d)"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List command history with filtering."""
    from navig.commands.history import show_history
    ctx.obj['plain'] = plain
    if json_out:
        ctx.obj['json'] = True
    show_history(
        limit=limit,
        host=host,
        operation_type=type_filter,
        status=status,
        search=search,
        since=since,
        opts=ctx.obj,
    )


@history_app.command("show")
def history_show(
    ctx: typer.Context,
    op_id: str = typer.Argument(..., help="Operation ID or index (1=last, 2=second-last)"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show detailed information about an operation."""
    from navig.commands.history import show_operation_details
    if json_out:
        ctx.obj['json'] = True
    show_operation_details(op_id, opts=ctx.obj)


@history_app.command("replay")
def history_replay(
    ctx: typer.Context,
    op_id: str = typer.Argument(..., help="Operation ID or index to replay"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be done"),
    modify: Optional[str] = typer.Option(None, "--modify", "-m", help="Modify command before replay"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Replay a previous operation."""
    from navig.commands.history import replay_operation
    ctx.obj['yes'] = yes
    replay_operation(op_id, dry_run=dry_run, modify=modify, opts=ctx.obj)


@history_app.command("undo")
def history_undo(
    ctx: typer.Context,
    op_id: str = typer.Argument(..., help="Operation ID or index to undo"),
):
    """Undo a reversible operation."""
    from navig.commands.history import undo_operation
    undo_operation(op_id, opts=ctx.obj)


@history_app.command("export")
def history_export(
    ctx: typer.Context,
    output: str = typer.Argument(..., help="Output file path"),
    format: str = typer.Option("json", "--format", "-f", help="Export format (json, csv)"),
    limit: int = typer.Option(1000, "--limit", "-l", help="Max entries to export"),
):
    """Export operation history to file."""
    from navig.commands.history import export_history
    export_history(output, format=format, limit=limit, opts=ctx.obj)


@history_app.command("clear")
def history_clear(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Clear all operation history."""
    from navig.commands.history import clear_history
    ctx.obj['yes'] = yes
    clear_history(opts=ctx.obj)


@history_app.command("stats")
def history_stats_cmd(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show history statistics."""
    from navig.commands.history import history_stats
    if json_out:
        ctx.obj['json'] = True
    history_stats(opts=ctx.obj)


def register_workspace_state_typers(app: typer.Typer) -> None:
    """Register extracted workspace-state typer groups on the root app."""
    app.add_typer(context_app, name="context")
    app.add_typer(context_app, name="ctx", hidden=True)
    app.add_typer(index_app, name="index")
    app.add_typer(history_app, name="history")
    app.add_typer(history_app, name="hist", hidden=True)
