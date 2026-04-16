"""
NAVIG Stack CLI Commands

Manage the local NAVIG infrastructure stack (Docker Compose services).

Commands:
    navig stack status   — Show stack container status
    navig stack up       — Start the stack
    navig stack down     — Stop the stack
    navig stack logs     — Tail stack logs
    navig stack health   — Run healthcheck
    navig stack info     — Show stack configuration & paths
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

stack_app = typer.Typer(
    name="stack",
    help="Manage the local NAVIG infrastructure stack (Postgres, Redis, Ollama)",
    no_args_is_help=True,
)


def _get_stack_dir() -> Path:
    """Resolve the stack directory."""
    from navig.platform import stack_dir

    return stack_dir()


def _compose_cmd(stack_path: Path) -> list[str]:
    """Build the base docker compose command."""
    compose_file = stack_path / "docker-compose.yml"
    env_file = stack_path / ".env"
    cmd = ["docker", "compose", "-f", str(compose_file)]
    if env_file.exists():
        cmd.extend(["--env-file", str(env_file)])
    return cmd


def _check_prerequisites(stack_path: Path) -> bool:
    """Check that Docker and compose file exist."""
    from navig.platform import check_docker

    docker = check_docker()
    if not docker["available"]:
        ch.error("Docker is not installed or not running")
        ch.info("Install Docker: https://docs.docker.com/engine/install/")
        return False

    if not docker["compose"]:
        ch.error("Docker Compose plugin is not available")
        ch.info("Install: https://docs.docker.com/compose/install/")
        return False

    compose_file = stack_path / "docker-compose.yml"
    if not compose_file.exists():
        ch.error(f"No docker-compose.yml found in {stack_path}")
        ch.info("Run the bootstrap script first: navig-core/scripts/bootstrap_navig_linux.sh")
        return False

    return True


@stack_app.command("status")
def stack_status(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show NAVIG stack container status.

    Examples:
        navig stack status
        navig stack status --json
    """
    stack_path = _get_stack_dir()
    if not _check_prerequisites(stack_path):
        raise typer.Exit(1)

    cmd = _compose_cmd(stack_path) + ["ps", "-a"]
    if json_output:
        cmd.append("--format=json")

    result = subprocess.run(cmd, capture_output=not json_output)
    if json_output:
        import json

        try:
            containers = json.loads(result.stdout)
            print(json.dumps(containers, indent=2))
        except Exception:
            print(result.stdout.decode())


@stack_app.command("up")
def stack_up(
    detach: bool = typer.Option(True, "--detach/--foreground", "-d", help="Run in background"),
):
    """
    Start the NAVIG infrastructure stack.

    Examples:
        navig stack up
        navig stack up --foreground
    """
    stack_path = _get_stack_dir()
    if not _check_prerequisites(stack_path):
        raise typer.Exit(1)

    ch.info("Starting NAVIG stack...")
    cmd = _compose_cmd(stack_path) + ["up", "--remove-orphans"]
    if detach:
        cmd.append("-d")

    result = subprocess.run(cmd)
    if result.returncode == 0 and detach:
        ch.success("Stack started")
        ch.info("Check status: navig stack status")
    elif result.returncode != 0:
        ch.error("Failed to start stack")
        raise typer.Exit(1)


@stack_app.command("down")
def stack_down(
    volumes: bool = typer.Option(False, "--volumes", "-v", help="Also remove volumes (DATA LOSS)"),
):
    """
    Stop the NAVIG infrastructure stack.

    Examples:
        navig stack down
        navig stack down --volumes  # WARNING: removes all data
    """
    stack_path = _get_stack_dir()
    if not _check_prerequisites(stack_path):
        raise typer.Exit(1)

    if volumes:
        if not ch.confirm_action(
            "This will DELETE all stack data (Postgres, Redis, Ollama models). Continue?",
            default=False,
        ):
            ch.info("Cancelled")
            return

    ch.info("Stopping NAVIG stack...")
    cmd = _compose_cmd(stack_path) + ["down"]
    if volumes:
        cmd.append("--volumes")

    result = subprocess.run(cmd)
    if result.returncode == 0:
        ch.success("Stack stopped")
    else:
        ch.error("Failed to stop stack")
        raise typer.Exit(1)


@stack_app.command("logs")
def stack_logs(
    service: str | None = typer.Argument(None, help="Service name (postgres, redis, ollama)"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
):
    """
    View NAVIG stack logs.

    Examples:
        navig stack logs
        navig stack logs ollama -f
        navig stack logs postgres -n 100
    """
    stack_path = _get_stack_dir()
    if not _check_prerequisites(stack_path):
        raise typer.Exit(1)

    cmd = _compose_cmd(stack_path) + ["logs", f"--tail={lines}"]
    if follow:
        cmd.append("-f")
    if service:
        cmd.append(service)

    subprocess.run(cmd)


@stack_app.command("health")
def stack_health():
    """
    Run healthcheck on all stack services.

    Examples:
        navig stack health
    """
    stack_path = _get_stack_dir()
    if not _check_prerequisites(stack_path):
        raise typer.Exit(1)

    healthcheck = stack_path / "navig_healthcheck.sh"
    if healthcheck.exists():
        result = subprocess.run(str(healthcheck), shell=True, check=False)  # noqa: S602  # dynamic shell dispatch
        if result.returncode == 0:
            return
        ch.warning("Healthcheck script failed; running manual checks instead.")

    # Fallback: manual checks
    ch.info("Running health checks...")
    checks = [
        ("Docker", ["docker", "info"]),
        ("Postgres", ["docker", "exec", "navig-postgres", "pg_isready"]),
        ("Redis", ["docker", "exec", "navig-redis", "redis-cli", "ping"]),
        (
            "Ollama",
            [
                "docker",
                "exec",
                "navig-ollama",
                "curl",
                "-sf",
                "http://localhost:11434/api/tags",
            ],
        ),
    ]

    passed = 0
    failed = 0
    for name, cmd in checks:
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=10)
            if result.returncode == 0:
                ch.success(f"[OK] {name}")
                passed += 1
            else:
                ch.error(f"[FAIL] {name}")
                failed += 1
        except Exception as e:
            ch.error(f"[FAIL] {name}: {e}")
            failed += 1

    ch.console.print()
    ch.info(f"Results: {passed} passed, {failed} failed")


@stack_app.command("info")
def stack_info():
    """
    Show stack configuration and paths.

    Examples:
        navig stack info
    """
    from navig.platform import check_docker, platform_info, stack_dir

    stack_path = stack_dir()
    docker = check_docker()
    pinfo = platform_info()

    ch.header("NAVIG Stack Info")
    ch.console.print(f"  OS:           {pinfo['os']} ({pinfo.get('distro', pinfo['os_version'])})")
    ch.console.print(f"  Stack dir:    {stack_path}")
    ch.console.print(f"  Config dir:   {pinfo['paths']['config']}")

    ch.console.print()
    ch.header("Docker")
    ch.console.print(f"  Available:    {'Yes' if docker['available'] else 'No'}")
    if docker["version"]:
        ch.console.print(f"  Version:      {docker['version']}")
    if docker["compose_version"]:
        ch.console.print(f"  Compose:      {docker['compose_version']}")

    # Show compose services if available
    compose_file = stack_path / "docker-compose.yml"
    if compose_file.exists():
        ch.console.print()
        ch.header("Stack Services")
        try:
            cmd = _compose_cmd(stack_path) + ["config", "--services"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                for svc in result.stdout.strip().split("\n"):
                    if svc.strip():
                        ch.console.print(f"  - {svc.strip()}")
        except Exception:
            ch.dim("  (could not read compose config)")

    # Show .env variables (keys only)
    env_file = stack_path / ".env"
    if env_file.exists():
        ch.console.print()
        ch.header("Environment (.env)")
        try:
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key = line.split("=", 1)[0]
                    ch.console.print(f"  {key}=***")
        except PermissionError:
            ch.dim("  (permission denied)")
