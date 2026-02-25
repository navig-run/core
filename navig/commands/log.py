

import typer
from navig.cli import show_subcommand_help, deprecation_warning
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
from navig import console_helper as ch


log_app = typer.Typer(
    help="Log viewing and management",
    invoke_without_command=True,
    no_args_is_help=False,
)


@log_app.callback()
def log_callback(ctx: typer.Context):
    """Log management command group."""
    if ctx.invoked_subcommand is None:
        # If run without subcommand, default to listing logs or showing help
        # For now, just show help
        pass

    """Log operations - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("log", ctx)
        raise typer.Exit()


@log_app.command("show")
def log_show(
    ctx: typer.Context,
    service: str = typer.Argument(..., help="Service name (nginx, php-fpm, mysql, app, etc.)"),
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    tail: bool = typer.Option(False, "--tail", "-f", help="Follow logs in real-time"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines"),
    since: Optional[str] = typer.Option(None, "--since", help="Show logs since (e.g., 10m, 1h)"),
):
    """Show service or container logs."""
    if container:
        from navig.commands.docker import docker_logs
        docker_logs(container, ctx.obj, tail=lines, follow=tail, since=since)
    else:
        from navig.commands.monitoring import view_service_logs
        view_service_logs(service, tail, lines, ctx.obj)


@log_app.command("run")
def log_run(
    ctx: typer.Context,
    rotate: bool = typer.Option(False, "--rotate", help="Rotate and compress logs"),
):
    """Run log maintenance operations."""
    if rotate:
        from navig.commands.maintenance import rotate_logs
        rotate_logs(ctx.obj)
    else:
        ch.error("Specify an action: --rotate")


# ============================================================================
# SERVER OPERATIONS (Canonical 'server' group - unifies web, docker, hestia)
# ============================================================================
