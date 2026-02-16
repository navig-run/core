"""Docker Management Commands - Simplified container operations via SSH.

These commands provide convenient shortcuts for common Docker operations,
eliminating the need for complex shell escaping and multi-command sequences.
"""
from typing import Dict, Any, Optional, List
from navig import console_helper as ch


def docker_ps(options: Dict[str, Any], all: bool = False, filter: Optional[str] = None, format: str = "table"):
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
    
    host_name = options.get('host') or config_manager.get_active_host()
    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return
    
    host_config = config_manager.load_host_config(host_name)
    
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
        cmd = f"{cmd} | grep -E '{filter}'"
    
    if not options.get('quiet'):
        ch.info(f"Containers on {host_name}:")
    
    result = remote_ops.execute_command(cmd, host_config, capture_output=False)
    
    if result.returncode != 0 and filter:
        ch.dim("No containers matching filter.")


def docker_logs(
    container: str,
    options: Dict[str, Any],
    tail: Optional[int] = None,
    follow: bool = False,
    since: Optional[str] = None,
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
    
    host_name = options.get('host') or config_manager.get_active_host()
    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return
    
    host_config = config_manager.load_host_config(host_name)
    
    # Build docker logs command
    cmd_parts = ["docker logs"]
    
    if tail:
        cmd_parts.append(f"--tail {tail}")
    elif not follow:
        cmd_parts.append("--tail 50")  # Default to last 50 lines
    
    if follow:
        cmd_parts.append("-f")
    
    if since:
        cmd_parts.append(f"--since {since}")
    
    cmd_parts.append(container)
    cmd = " ".join(cmd_parts)
    
    # Add stderr redirect for combined output
    cmd = f"{cmd} 2>&1"
    
    if not options.get('quiet'):
        ch.info(f"Logs for {container}:")
    
    result = remote_ops.execute_command(cmd, host_config, capture_output=False)
    
    if result.returncode != 0:
        ch.error(f"Failed to get logs for container: {container}")


def docker_exec(
    container: str,
    command: str,
    options: Dict[str, Any],
    interactive: bool = False,
    user: Optional[str] = None,
    workdir: Optional[str] = None,
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
    
    host_name = options.get('host') or config_manager.get_active_host()
    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return
    
    host_config = config_manager.load_host_config(host_name)
    
    # Build docker exec command
    cmd_parts = ["docker exec"]
    
    if interactive:
        cmd_parts.append("-it")
    
    if user:
        cmd_parts.append(f"-u {user}")
    
    if workdir:
        cmd_parts.append(f"-w {workdir}")
    
    cmd_parts.append(container)
    cmd_parts.append(command)
    cmd = " ".join(cmd_parts)
    
    if not options.get('quiet'):
        ch.info(f"Executing in {container}: {command}")
    
    result = remote_ops.execute_command(cmd, host_config, capture_output=False)
    
    if result.returncode != 0:
        ch.warning(f"Command exited with code: {result.returncode}")


def docker_compose(
    action: str,
    options: Dict[str, Any],
    path: Optional[str] = None,
    services: Optional[List[str]] = None,
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
    
    host_name = options.get('host') or config_manager.get_active_host()
    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return
    
    host_config = config_manager.load_host_config(host_name)
    
    valid_actions = ['up', 'down', 'restart', 'stop', 'start', 'pull', 'build', 'logs', 'ps', 'config']
    if action not in valid_actions:
        ch.error(f"Invalid action: {action}", f"Valid actions: {', '.join(valid_actions)}")
        return
    
    # Build compose command
    cmd_parts = []
    
    if path:
        cmd_parts.append(f"cd {path} &&")
    
    cmd_parts.append("docker compose")
    cmd_parts.append(action)
    
    # Action-specific options
    if action == 'up':
        if detach:
            cmd_parts.append("-d")
        if build:
            cmd_parts.append("--build")
        if pull:
            cmd_parts.append("--pull always")
    elif action == 'logs':
        cmd_parts.append("--tail 50")
    
    # Add specific services if provided
    if services:
        cmd_parts.extend(services)
    
    cmd = " ".join(cmd_parts)
    
    # Confirm for destructive operations
    if action in ['down', 'restart', 'stop']:
        if not ch.confirm_operation(
            operation_name=f"Docker Compose {action}",
            operation_type='standard',
            host=host_name,
            details=f"Path: {path or 'current directory'}",
            auto_confirm=options.get('yes', False),
            force_confirm=options.get('confirm', False),
        ):
            ch.warning("Cancelled.")
            return
    
    if not options.get('quiet'):
        ch.info(f"Running: docker compose {action}")
    
    result = remote_ops.execute_command(cmd, host_config, capture_output=False)
    
    if result.returncode == 0:
        ch.success(f"✓ Docker compose {action} completed")
    else:
        ch.error(f"Docker compose {action} failed")


def docker_inspect(
    container: str,
    options: Dict[str, Any],
    format: Optional[str] = None,
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
    
    host_name = options.get('host') or config_manager.get_active_host()
    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return
    
    host_config = config_manager.load_host_config(host_name)
    
    if format:
        cmd = f"docker inspect --format '{format}' {container}"
    else:
        cmd = f"docker inspect {container}"
    
    result = remote_ops.execute_command(cmd, host_config, capture_output=False)
    
    if result.returncode != 0:
        ch.error(f"Failed to inspect container: {container}")


def docker_restart(
    container: str,
    options: Dict[str, Any],
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
    
    host_name = options.get('host') or config_manager.get_active_host()
    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return
    
    host_config = config_manager.load_host_config(host_name)
    
    # Confirm restart
    if not ch.confirm_operation(
        operation_name=f"Restart container: {container}",
        operation_type='standard',
        host=host_name,
        auto_confirm=options.get('yes', False),
        force_confirm=options.get('confirm', False),
    ):
        ch.warning("Cancelled.")
        return
    
    cmd = f"docker restart -t {timeout} {container}"
    
    if not options.get('quiet'):
        with ch.create_spinner(f"Restarting {container}..."):
            result = remote_ops.execute_command(cmd, host_config)
    else:
        result = remote_ops.execute_command(cmd, host_config)
    
    if result.returncode == 0:
        ch.success(f"✓ Container {container} restarted")
    else:
        ch.error(f"Failed to restart container: {container}")


def docker_stop(container: str, options: Dict[str, Any], timeout: int = 10):
    """Stop Docker container."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations
    
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)
    
    host_name = options.get('host') or config_manager.get_active_host()
    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return
    
    host_config = config_manager.load_host_config(host_name)
    
    if not ch.confirm_operation(
        operation_name=f"Stop container: {container}",
        operation_type='standard',
        host=host_name,
        auto_confirm=options.get('yes', False),
        force_confirm=options.get('confirm', False),
    ):
        ch.warning("Cancelled.")
        return
    
    result = remote_ops.execute_command(f"docker stop -t {timeout} {container}", host_config)
    
    if result.returncode == 0:
        ch.success(f"✓ Container {container} stopped")
    else:
        ch.error(f"Failed to stop container: {container}")


def docker_start(container: str, options: Dict[str, Any]):
    """Start Docker container."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations
    
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)
    
    host_name = options.get('host') or config_manager.get_active_host()
    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return
    
    host_config = config_manager.load_host_config(host_name)
    
    result = remote_ops.execute_command(f"docker start {container}", host_config)
    
    if result.returncode == 0:
        ch.success(f"✓ Container {container} started")
    else:
        ch.error(f"Failed to start container: {container}")


def docker_stats(options: Dict[str, Any], container: Optional[str] = None, no_stream: bool = True):
    """Show Docker container resource usage statistics."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations
    
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)
    
    host_name = options.get('host') or config_manager.get_active_host()
    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return
    
    host_config = config_manager.load_host_config(host_name)
    
    cmd = "docker stats"
    if no_stream:
        cmd += " --no-stream"
    if container:
        cmd += f" {container}"
    
    if not options.get('quiet'):
        ch.info("Container resource usage:")
    
    remote_ops.execute_command(cmd, host_config, capture_output=False)
