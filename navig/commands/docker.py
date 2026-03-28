"""Docker Management Commands - Simplified container operations via SSH.

These commands provide convenient shortcuts for common Docker operations,
eliminating the need for complex shell escaping and multi-command sequences.
"""

from typing import Any

from navig import console_helper as ch


def docker_ps(
    options: dict[str, Any],
    all: bool = False,
    filter: str | None = None,
    format: str = "table",
):
    """List Docker containers on remote host.

    Args:
        options: CLI context options
        all: Show all containers (including stopped)
        filter: Filter containers by name (grep pattern)
        format: Output format - table (default), json, or names
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    from navig.cli.recovery import require_active_host
    host_name = require_active_host(options, config_manager)

    host_config = config_manager.load_host_config(host_name)

    import shlex

    # Build docker ps command
    if format == "json":
        docker_format = '--format "{{json .}}"'
    elif format == "names":
        docker_format = '--format "{{.Names}}"'
    else:
        docker_format = '--format "table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}"'

    cmd = f"docker ps {docker_format}"
    if all:
        cmd = f"docker ps -a {docker_format}"

    if filter:
        filter_safe = shlex.quote(filter)
        cmd = f"{cmd} | grep -E {filter_safe}"

    if not options.get("quiet"):
        ch.info(f"Containers on {host_name}:")

    result = remote_ops.execute_command(cmd, host_config, capture_output=False)

    if result.returncode != 0 and filter:
        ch.dim("No containers matching filter.")


def docker_logs(
    container: str,
    options: dict[str, Any],
    tail: int | None = None,
    follow: bool = False,
    since: str | None = None,
):
    """View Docker container logs.

    Args:
        container: Container name or ID
        options: CLI context options
        tail: Number of lines to show (default: 50)
        follow: Follow log output (like tail -f)
        since: Show logs since timestamp (e.g., "10m", "1h", "2024-01-01")
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    from navig.cli.recovery import require_active_host
    host_name = require_active_host(options, config_manager)

    host_config = config_manager.load_host_config(host_name)

    import shlex

    # Build docker logs command
    cmd_parts = ["docker logs"]

    if tail:
        cmd_parts.append(f"--tail {int(tail)}")
    elif not follow:
        cmd_parts.append("--tail 50")  # Default to last 50 lines

    if follow:
        cmd_parts.append("-f")

    if since:
        cmd_parts.append(f"--since {shlex.quote(since)}")

    cmd_parts.append(shlex.quote(container))
    cmd = " ".join(cmd_parts)

    # Add stderr redirect for combined output
    cmd = f"{cmd} 2>&1"

    if not options.get("quiet"):
        ch.info(f"Logs for {container}:")

    result = remote_ops.execute_command(cmd, host_config, capture_output=False)

    if result.returncode != 0:
        ch.error(f"Failed to get logs for container: {container}")


def docker_exec(
    container: str,
    command: str,
    options: dict[str, Any],
    interactive: bool = False,
    user: str | None = None,
    workdir: str | None = None,
):
    """Execute command in Docker container.

    Args:
        container: Container name or ID
        command: Command to execute inside container
        options: CLI context options
        interactive: Run in interactive mode with TTY
        user: Run as specific user
        workdir: Working directory inside container
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    from navig.cli.recovery import require_active_host
    host_name = require_active_host(options, config_manager)

    host_config = config_manager.load_host_config(host_name)

    import shlex

    # Build docker exec command
    cmd_parts = ["docker exec"]

    if interactive:
        cmd_parts.append("-it")

    if user:
        cmd_parts.append(f"-u {shlex.quote(user)}")

    if workdir:
        cmd_parts.append(f"-w {shlex.quote(workdir)}")

    cmd_parts.append(shlex.quote(container))
    # We must quote the actual command payload so SSH transmits it correctly as arguments
    # but still allow it to execute inside the docker container
    cmd_parts.append(shlex.quote(command))
    cmd = " ".join(cmd_parts)

    if not options.get("quiet"):
        ch.info(f"Executing in {container}: {command}")

    result = remote_ops.execute_command(cmd, host_config, capture_output=False)

    if result.returncode != 0:
        ch.warning(f"Command exited with code: {result.returncode}")


def docker_compose(
    action: str,
    options: dict[str, Any],
    path: str | None = None,
    services: list[str] | None = None,
    detach: bool = True,
    build: bool = False,
    pull: bool = False,
):
    """Run docker compose commands.

    Args:
        action: Compose action (up, down, restart, stop, start, pull, build, logs)
        options: CLI context options
        path: Path to docker-compose.yml directory
        services: Specific services to target (default: all)
        detach: Run in background (for 'up' action)
        build: Build images before starting (for 'up' action)
        pull: Pull images before starting (for 'up' action)
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    from navig.cli.recovery import require_active_host
    host_name = require_active_host(options, config_manager)

    host_config = config_manager.load_host_config(host_name)

    valid_actions = [
        "up",
        "down",
        "restart",
        "stop",
        "start",
        "pull",
        "build",
        "logs",
        "ps",
        "config",
    ]
    if action not in valid_actions:
        ch.error(f"Invalid action: {action}", f"Valid actions: {', '.join(valid_actions)}")
        return

    import shlex

    # Build compose command
    cmd_parts = []

    if path:
        cmd_parts.append(f"cd {shlex.quote(path)} &&")

    cmd_parts.append("docker compose")
    cmd_parts.append(shlex.quote(action))

    # Action-specific options
    if action == "up":
        if detach:
            cmd_parts.append("-d")
        if build:
            cmd_parts.append("--build")
        if pull:
            cmd_parts.append("--pull always")
    elif action == "logs":
        cmd_parts.append("--tail 50")

    # Add specific services if provided
    if services:
        cmd_parts.extend([shlex.quote(s) for s in services])

    cmd = " ".join(cmd_parts)

    # Confirm for destructive operations
    if action in ["down", "restart", "stop"]:
        if not ch.confirm_operation(
            operation_name=f"Docker Compose {action}",
            operation_type="standard",
            host=host_name,
            details=f"Path: {path or 'current directory'}",
            auto_confirm=options.get("yes", False),
            force_confirm=options.get("confirm", False),
        ):
            ch.warning("Cancelled.")
            return

    if not options.get("quiet"):
        ch.info(f"Running: docker compose {action}")

    result = remote_ops.execute_command(cmd, host_config, capture_output=False)

    if result.returncode == 0:
        ch.success(f"✓ Docker compose {action} completed")
    else:
        ch.error(f"Docker compose {action} failed")


def docker_inspect(
    container: str,
    options: dict[str, Any],
    format: str | None = None,
):
    """Inspect Docker container.

    Args:
        container: Container name or ID
        options: CLI context options
        format: Go template format string (e.g., '{{.State.Status}}')
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    from navig.cli.recovery import require_active_host
    host_name = require_active_host(options, config_manager)

    host_config = config_manager.load_host_config(host_name)

    import shlex

    container_safe = shlex.quote(container)
    if format:
        format_safe = shlex.quote(format)
        cmd = f"docker inspect --format {format_safe} {container_safe}"
    else:
        cmd = f"docker inspect {container_safe}"

    result = remote_ops.execute_command(cmd, host_config, capture_output=False)

    if result.returncode != 0:
        ch.error(f"Failed to inspect container: {container}")


def docker_restart(
    container: str,
    options: dict[str, Any],
    timeout: int = 10,
):
    """Restart Docker container.

    Args:
        container: Container name or ID
        options: CLI context options
        timeout: Timeout in seconds to wait for stop before killing
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    from navig.cli.recovery import require_active_host
    host_name = require_active_host(options, config_manager)

    host_config = config_manager.load_host_config(host_name)

    # Confirm restart
    if not ch.confirm_operation(
        operation_name=f"Restart container: {container}",
        operation_type="standard",
        host=host_name,
        auto_confirm=options.get("yes", False),
        force_confirm=options.get("confirm", False),
    ):
        ch.warning("Cancelled.")
        return

    import shlex

    cmd = f"docker restart -t {int(timeout)} {shlex.quote(container)}"

    if not options.get("quiet"):
        with ch.create_spinner(f"Restarting {container}..."):
            result = remote_ops.execute_command(cmd, host_config)
    else:
        result = remote_ops.execute_command(cmd, host_config)

    if result.returncode == 0:
        ch.success(f"✓ Container {container} restarted")
    else:
        ch.error(f"Failed to restart container: {container}")


def docker_stop(container: str, options: dict[str, Any], timeout: int = 10):
    """Stop Docker container."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    from navig.cli.recovery import require_active_host
    host_name = require_active_host(options, config_manager)

    host_config = config_manager.load_host_config(host_name)

    if not ch.confirm_operation(
        operation_name=f"Stop container: {container}",
        operation_type="standard",
        host=host_name,
        auto_confirm=options.get("yes", False),
        force_confirm=options.get("confirm", False),
    ):
        ch.warning("Cancelled.")
        return

    import shlex

    result = remote_ops.execute_command(
        f"docker stop -t {int(timeout)} {shlex.quote(container)}", host_config
    )

    if result.returncode == 0:
        ch.success(f"✓ Container {container} stopped")
    else:
        ch.error(f"Failed to stop container: {container}")


def docker_start(container: str, options: dict[str, Any]):
    """Start Docker container."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    from navig.cli.recovery import require_active_host
    host_name = require_active_host(options, config_manager)

    host_config = config_manager.load_host_config(host_name)

    import shlex

    result = remote_ops.execute_command(f"docker start {shlex.quote(container)}", host_config)

    if result.returncode == 0:
        ch.success(f"✓ Container {container} started")
    else:
        ch.error(f"Failed to start container: {container}")


def docker_stats(options: dict[str, Any], container: str | None = None, no_stream: bool = True):
    """Show Docker container resource usage statistics."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    from navig.cli.recovery import require_active_host
    host_name = require_active_host(options, config_manager)

    host_config = config_manager.load_host_config(host_name)

    import shlex

    cmd = "docker stats"
    if no_stream:
        cmd += " --no-stream"
    if container:
        cmd += f" {shlex.quote(container)}"

    if not options.get("quiet"):
        ch.info("Container resource usage:")

    remote_ops.execute_command(cmd, host_config, capture_output=False)


# ── QUANTUM VELOCITY A: docker_app Typer wrapper ───────────────────────────
# Moved here from cli/__init__.py so that `docker` is dispatched lazily via
# _EXTERNAL_CMD_MAP (“navig.commands.docker”, “docker_app”).  This means the
# 175-line docker block in cli/__init__.py is no longer parsed on every cold
# start — only when the user actually runs `navig docker …`.

import typer as _t

docker_app = _t.Typer(
    help="Docker container management",
    invoke_without_command=True,
    no_args_is_help=False,
)


@docker_app.callback()
def _docker_callback(ctx: _t.Context):
    """Docker management — run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        import os as _os  # noqa: PLC0415

        if _os.environ.get("NAVIG_LAUNCHER", "fuzzy") == "legacy":
            print(ctx.get_help())
            raise _t.Exit()
        from navig.cli.launcher import smart_launch  # noqa: PLC0415

        smart_launch("docker", docker_app)


@docker_app.command("ps")
def _docker_ps_cmd(
    ctx: _t.Context,
    all: bool = _t.Option(False, "--all", "-a", help="Show all containers (including stopped)"),
    filter: str | None = _t.Option(None, "--filter", "-f", help="Filter by name (grep pattern)"),
    format: str = _t.Option("table", "--format", help="Output format: table, json, names"),
):
    """
    List Docker containers on remote host.

    \b
    Examples:
        navig docker ps                 # Running containers
        navig docker ps --all           # All containers
        navig docker ps -f affine       # Filter by name
    """
    docker_ps(ctx.obj, all=all, filter=filter, format=format)


@docker_app.command("logs")
def _docker_logs_cmd(
    ctx: _t.Context,
    container: str = _t.Argument(..., help="Container name or ID"),
    tail: int | None = _t.Option(None, "--tail", "-n", help="Number of lines to show"),
    follow: bool = _t.Option(False, "--follow", "-f", help="Follow log output"),
    since: str | None = _t.Option(None, "--since", help="Show logs since (e.g., 10m, 1h)"),
):
    """
    View Docker container logs.

    \b
    Examples:
        navig docker logs nginx          # Last 50 lines
        navig docker logs app -n 100     # Last 100 lines
        navig docker logs app --follow   # Stream logs
        navig docker logs app --since 1h # Logs from last hour
    """
    docker_logs(container, ctx.obj, tail=tail, follow=follow, since=since)


@docker_app.command("exec")
def _docker_exec_cmd(
    ctx: _t.Context,
    container: str = _t.Argument(..., help="Container name or ID"),
    command: str = _t.Argument(..., help="Command to execute"),
    interactive: bool = _t.Option(False, "--interactive", "-i", help="Interactive mode with TTY"),
    user: str | None = _t.Option(None, "--user", "-u", help="Run as specific user"),
    workdir: str | None = _t.Option(None, "--workdir", "-w", help="Working directory"),
):
    """
    Execute command in Docker container.

    \b
    Examples:
        navig docker exec nginx "nginx -t"
        navig docker exec postgres "psql -U postgres -c 'SELECT 1'"
        navig docker exec app "php artisan migrate" -u www-data
    """
    docker_exec(container, command, ctx.obj, interactive=interactive, user=user, workdir=workdir)


@docker_app.command("compose")
def _docker_compose_cmd(
    ctx: _t.Context,
    action: str = _t.Argument(
        ..., help="Action: up, down, restart, stop, start, pull, build, logs, ps"
    ),
    path: str | None = _t.Option(None, "--path", "-p", help="Path to docker-compose.yml directory"),
    services: str | None = _t.Option(
        None, "--services", "-s", help="Comma-separated list of services"
    ),
    detach: bool = _t.Option(
        True, "--detach/--no-detach", "-d", help="Run in background (for 'up')"
    ),
    build: bool = _t.Option(False, "--build", "-b", help="Build images before starting"),
    pull: bool = _t.Option(False, "--pull", help="Pull images before starting"),
):
    """
    Run docker compose commands on remote host.

    \b
    Examples:
        navig docker compose up --path /app
        navig docker compose down --path /app
        navig docker compose restart --path /app --services "web,db"
        navig docker compose logs --path /app
    """
    service_list = services.split(",") if services else None
    docker_compose(
        action,
        ctx.obj,
        path=path,
        services=service_list,
        detach=detach,
        build=build,
        pull=pull,
    )


@docker_app.command("restart")
def _docker_restart_cmd(
    ctx: _t.Context,
    container: str = _t.Argument(..., help="Container name or ID"),
    timeout: int = _t.Option(10, "--timeout", "-t", help="Timeout in seconds"),
):
    """Restart Docker container."""
    docker_restart(container, ctx.obj, timeout=timeout)


@docker_app.command("stop")
def _docker_stop_cmd(
    ctx: _t.Context,
    container: str = _t.Argument(..., help="Container name or ID"),
    timeout: int = _t.Option(10, "--timeout", "-t", help="Timeout in seconds"),
):
    """Stop Docker container."""
    docker_stop(container, ctx.obj, timeout=timeout)


@docker_app.command("start")
def _docker_start_cmd(
    ctx: _t.Context,
    container: str = _t.Argument(..., help="Container name or ID"),
):
    """Start Docker container."""
    docker_start(container, ctx.obj)


@docker_app.command("stats")
def _docker_stats_cmd(
    ctx: _t.Context,
    container: str | None = _t.Argument(None, help="Container name (all if omitted)"),
    stream: bool = _t.Option(False, "--stream", "-s", help="Stream stats continuously"),
):
    """Show container resource usage statistics."""
    docker_stats(ctx.obj, container=container, no_stream=not stream)


@docker_app.command("inspect")
def _docker_inspect_cmd(
    ctx: _t.Context,
    container: str = _t.Argument(..., help="Container name or ID"),
    format: str | None = _t.Option(None, "--format", "-f", help="Go template format"),
):
    """
    Inspect Docker container.

    \b
    Examples:
        navig docker inspect nginx
        navig docker inspect nginx -f "{{.State.Status}}"
        navig docker inspect nginx -f "{{.HostConfig.RestartPolicy.Name}}"
    """
    docker_inspect(container, ctx.obj, format=format)
