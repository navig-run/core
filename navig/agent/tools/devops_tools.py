"""
navig.agent.tools.devops_tools — DevOps agent tools (MVP3 F-16).

Wraps navig's remote-host command surface as structured ``BaseTool`` subclasses
callable by the agentic ReAct loop.  Each tool calls the low-level SSH / config
layer directly (``RemoteOperations``, ``ServerDiscovery``) — **not** the
click/typer command handlers which print to console.

All tools:
- ``owner_only = True``  (only the workspace owner may invoke)
- Output capped at ``_MAX_OUTPUT_CHARS`` to avoid context-window blow-up
- Integrated with ``navig.tools.approval`` for destructive operations

Usage::

    from navig.agent.tools.devops_tools import register_devops_tools
    register_devops_tools()
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_MAX_OUTPUT_CHARS = 4_000


# ─────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────

def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    """Truncate ``text`` to *limit* characters, appending a note if trimmed."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated — {len(text)} chars total]"


def _get_config_manager():
    """Lazy singleton for ConfigManager."""
    from navig.config import get_config_manager
    return get_config_manager()


def _resolve_host(host_name: str | None = None) -> tuple[str, dict[str, Any]]:
    """Return ``(host_name, host_config)`` for the requested or active host.

    Raises ``RuntimeError`` when no host is configured/found.
    """
    cm = _get_config_manager()
    if not host_name:
        host_name = cm.get_active_server() or cm.get_active_host()
    if not host_name:
        raise RuntimeError("No active host configured — run 'navig host use <name>' first")
    host_config = cm.load_host_config(host_name)
    if not host_config:
        raise RuntimeError(f"Host config not found: {host_name}")
    return host_name, host_config


def _get_remote_ops():
    """Build a ``RemoteOperations`` instance."""
    from navig.remote import RemoteOperations
    return RemoteOperations(_get_config_manager())


def _get_discovery(host_config: dict[str, Any]):
    """Build a ``ServerDiscovery`` for DB operations."""
    from navig.discovery import ServerDiscovery
    ssh_config = {
        "host": host_config.get("host", host_config.get("hostname")),
        "user": host_config.get("user", "root"),
        "port": host_config.get("port", 22),
        "ssh_key": host_config.get("ssh_key"),
        "ssh_password": host_config.get("ssh_password"),
    }
    return ServerDiscovery(ssh_config)


def _cp_to_str(cp: subprocess.CompletedProcess) -> str:
    """Extract human-readable text from a ``CompletedProcess``."""
    parts: list[str] = []
    if cp.stdout:
        parts.append(cp.stdout if isinstance(cp.stdout, str) else cp.stdout.decode("utf-8", errors="replace"))
    if cp.stderr:
        err = cp.stderr if isinstance(cp.stderr, str) else cp.stderr.decode("utf-8", errors="replace")
        if err.strip():
            parts.append(f"[stderr] {err}")
    return "\n".join(parts) or "(no output)"


async def _run_sync(fn, *args, **kwargs):
    """Run a synchronous function in the default executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# ═════════════════════════════════════════════════════════════
# Tool implementations
# ═════════════════════════════════════════════════════════════


class NavigRunTool(BaseTool):
    """Execute an arbitrary command on the active remote host."""

    name = "navig_run"
    description = (
        "Execute a shell command on the active remote host via SSH.  "
        "For complex commands with special characters, set b64=true and pass "
        "the base64-encoded command string."
    )
    owner_only = True
    parameters = [
        {"name": "command", "type": "string", "description": "Shell command to execute", "required": True},
        {"name": "b64", "type": "boolean", "description": "If true, 'command' is base64-encoded", "required": False},
        {"name": "host", "type": "string", "description": "Target host name (default: active host)", "required": False},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            command = args.get("command", "")
            if not command:
                return ToolResult(name=self.name, success=False, error="'command' is required")

            host_name, host_config = _resolve_host(args.get("host"))

            if args.get("b64"):
                # Wrap for remote execution: echo <b64> | base64 -d | bash
                command = f"echo '{command}' | base64 -d | bash"

            await self._emit(on_status, "ssh", f"Running on {host_name}", 10)
            remote_ops = _get_remote_ops()
            cp = await _run_sync(remote_ops.execute_command, command, host_config, capture_output=True)
            output = _truncate(_cp_to_str(cp))

            success = cp.returncode == 0
            return ToolResult(
                name=self.name,
                success=success,
                output=output,
                error=None if success else f"exit code {cp.returncode}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


# ─────── File operations ────────────────────────────────────


class NavigFileAddTool(BaseTool):
    """Upload a local file to the remote host."""

    name = "navig_file_add"
    description = "Upload a local file or directory to a path on the remote host."
    owner_only = True
    parameters = [
        {"name": "local_path", "type": "string", "description": "Local file path to upload", "required": True},
        {"name": "remote_path", "type": "string", "description": "Destination path on remote host", "required": True},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            local_path = Path(args.get("local_path", "")).expanduser()
            remote_path = args.get("remote_path", "")
            if not local_path.exists():
                return ToolResult(name=self.name, success=False, error=f"Local path not found: {local_path}")
            if not remote_path:
                return ToolResult(name=self.name, success=False, error="'remote_path' is required")

            host_name, host_config = _resolve_host()
            await self._emit(on_status, "upload", f"{local_path.name} → {host_name}:{remote_path}", 10)

            remote_ops = _get_remote_ops()
            ok = await _run_sync(remote_ops.upload_file, local_path, remote_path, host_config)
            return ToolResult(
                name=self.name,
                success=ok,
                output=f"Uploaded {local_path} → {host_name}:{remote_path}" if ok else None,
                error=None if ok else "Upload failed",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


class NavigFileGetTool(BaseTool):
    """Download a file from the remote host."""

    name = "navig_file_get"
    description = "Download a file from the remote host to a local path."
    owner_only = True
    parameters = [
        {"name": "remote_path", "type": "string", "description": "Remote file path to download", "required": True},
        {"name": "local_path", "type": "string", "description": "Local destination path (default: current dir)", "required": False},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            remote_path = args.get("remote_path", "")
            if not remote_path:
                return ToolResult(name=self.name, success=False, error="'remote_path' is required")

            local_path = Path(args.get("local_path", "") or Path.cwd() / Path(remote_path).name)
            host_name, host_config = _resolve_host()
            await self._emit(on_status, "download", f"{host_name}:{remote_path} → {local_path}", 10)

            remote_ops = _get_remote_ops()
            ok = await _run_sync(remote_ops.download_file, remote_path, local_path, host_config)
            return ToolResult(
                name=self.name,
                success=ok,
                output=f"Downloaded {host_name}:{remote_path} → {local_path}" if ok else None,
                error=None if ok else "Download failed",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


class NavigFileShowTool(BaseTool):
    """Read a remote file's contents via SSH."""

    name = "navig_file_show"
    description = (
        "Read the contents of a file on the remote host.  "
        "Supports --tail and --lines for partial reads."
    )
    owner_only = True
    parameters = [
        {"name": "remote_path", "type": "string", "description": "Remote file path to read", "required": True},
        {"name": "tail", "type": "boolean", "description": "Read from end of file", "required": False},
        {"name": "lines", "type": "integer", "description": "Number of lines to read (default: 100)", "required": False},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            remote_path = args.get("remote_path", "")
            if not remote_path:
                return ToolResult(name=self.name, success=False, error="'remote_path' is required")

            n_lines = args.get("lines", 100)
            tail = args.get("tail", False)
            cmd = f"tail -n {n_lines} {shlex.quote(remote_path)}" if tail else f"head -n {n_lines} {shlex.quote(remote_path)}"

            host_name, host_config = _resolve_host()
            await self._emit(on_status, "read", f"{host_name}:{remote_path}", 10)

            remote_ops = _get_remote_ops()
            cp = await _run_sync(remote_ops.execute_command, cmd, host_config, capture_output=True)
            output = _truncate(_cp_to_str(cp))
            success = cp.returncode == 0
            return ToolResult(
                name=self.name,
                success=success,
                output=output,
                error=None if success else f"exit code {cp.returncode}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


# ─────── Database operations ────────────────────────────────


class NavigDbQueryTool(BaseTool):
    """Execute a SQL query on the active host's database."""

    name = "navig_db_query"
    description = (
        "Execute a SQL query on a database via SSH.  "
        "Returns the query result as tab-separated text."
    )
    owner_only = True
    parameters = [
        {"name": "sql", "type": "string", "description": "SQL query to execute", "required": True},
        {"name": "database", "type": "string", "description": "Target database name", "required": True},
        {"name": "db_type", "type": "string", "description": "Database type: mysql, mariadb, postgresql (auto-detected if omitted)", "required": False},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            sql = args.get("sql", "")
            database = args.get("database", "")
            if not sql:
                return ToolResult(name=self.name, success=False, error="'sql' is required")
            if not database:
                return ToolResult(name=self.name, success=False, error="'database' is required")

            db_type = args.get("db_type", "mysql")
            host_name, host_config = _resolve_host()
            discovery = _get_discovery(host_config)
            await self._emit(on_status, "query", f"{database}@{host_name}", 10)

            from navig.commands.db import _build_db_command
            cmd = _build_db_command(db_type, sql, "root", None, database, None)
            ok, stdout, stderr = await _run_sync(discovery._execute_ssh, cmd)

            output_parts = []
            if stdout:
                output_parts.append(stdout)
            if stderr and stderr.strip():
                output_parts.append(f"[stderr] {stderr}")
            output = _truncate("\n".join(output_parts) or "(no output)")

            return ToolResult(
                name=self.name,
                success=ok,
                output=output,
                error=None if ok else (stderr or "Query failed"),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


class NavigDbDumpTool(BaseTool):
    """Dump/backup a database to a file on the remote host."""

    name = "navig_db_dump"
    description = (
        "Dump a database to a file on the remote host.  "
        "Use .gz extension for automatic compression."
    )
    owner_only = True
    parameters = [
        {"name": "database", "type": "string", "description": "Database name to dump", "required": True},
        {"name": "output", "type": "string", "description": "Output file path on remote host", "required": True},
        {"name": "db_type", "type": "string", "description": "Database type: mysql, mariadb, postgresql", "required": False},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            database = args.get("database", "")
            output_path = args.get("output", "")
            if not database:
                return ToolResult(name=self.name, success=False, error="'database' is required")
            if not output_path:
                return ToolResult(name=self.name, success=False, error="'output' is required")

            db_type = args.get("db_type", "mysql")
            host_name, host_config = _resolve_host()
            discovery = _get_discovery(host_config)
            await self._emit(on_status, "dump", f"{database}@{host_name} → {output_path}", 10)

            # Build dump command
            out_safe = shlex.quote(output_path)
            if db_type in ("mysql", "mariadb"):
                cmd = f"mysqldump -u root {shlex.quote(database)}"
            else:
                cmd = f"pg_dump -U postgres {shlex.quote(database)}"

            if output_path.endswith(".gz"):
                cmd = f"{cmd} | gzip > {out_safe}"
            else:
                cmd = f"{cmd} > {out_safe}"

            ok, stdout, stderr = await _run_sync(discovery._execute_ssh, cmd)
            return ToolResult(
                name=self.name,
                success=ok,
                output=f"Dumped {database} → {host_name}:{output_path}" if ok else None,
                error=None if ok else (stderr or "Dump failed"),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


class NavigDbListTool(BaseTool):
    """List databases on the active remote host."""

    name = "navig_db_list"
    description = "List all databases on the active remote host."
    owner_only = True
    parameters = [
        {"name": "db_type", "type": "string", "description": "Database type: mysql, mariadb, postgresql", "required": False},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            db_type = args.get("db_type", "mysql")
            host_name, host_config = _resolve_host()
            discovery = _get_discovery(host_config)
            await self._emit(on_status, "list-dbs", host_name, 10)

            if db_type in ("mysql", "mariadb"):
                cmd = "mysql -u root -e 'SHOW DATABASES;'"
            else:
                cmd = "psql -U postgres -l"

            ok, stdout, stderr = await _run_sync(discovery._execute_ssh, cmd)
            output = _truncate(stdout or "(no output)")
            return ToolResult(
                name=self.name,
                success=ok,
                output=output,
                error=None if ok else (stderr or "Failed to list databases"),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


# ─────── Docker operations ──────────────────────────────────


class NavigDockerPsTool(BaseTool):
    """List Docker containers on the remote host."""

    name = "navig_docker_ps"
    description = "List Docker containers on the active remote host."
    owner_only = True
    parameters = [
        {"name": "all", "type": "boolean", "description": "Show all containers including stopped (default: false)", "required": False},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            show_all = args.get("all", False)
            host_name, host_config = _resolve_host()
            await self._emit(on_status, "docker-ps", host_name, 10)

            fmt = '--format "table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}"'
            cmd = f"docker ps -a {fmt}" if show_all else f"docker ps {fmt}"

            remote_ops = _get_remote_ops()
            cp = await _run_sync(remote_ops.execute_command, cmd, host_config, capture_output=True)
            output = _truncate(_cp_to_str(cp))
            success = cp.returncode == 0
            return ToolResult(
                name=self.name,
                success=success,
                output=output,
                error=None if success else f"exit code {cp.returncode}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


class NavigDockerLogsTool(BaseTool):
    """Retrieve container logs from the remote host."""

    name = "navig_docker_logs"
    description = (
        "View logs for a Docker container on the active remote host.  "
        "Always limits output with -n to avoid context overflow."
    )
    owner_only = True
    parameters = [
        {"name": "container", "type": "string", "description": "Container name or ID", "required": True},
        {"name": "n", "type": "integer", "description": "Number of tail lines (default: 100)", "required": False},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            container = args.get("container", "")
            if not container:
                return ToolResult(name=self.name, success=False, error="'container' is required")

            n_lines = args.get("n", 100)
            host_name, host_config = _resolve_host()
            await self._emit(on_status, "docker-logs", f"{container}@{host_name}", 10)

            cmd = f"docker logs --tail {n_lines} {shlex.quote(container)} 2>&1"
            remote_ops = _get_remote_ops()
            cp = await _run_sync(remote_ops.execute_command, cmd, host_config, capture_output=True)
            output = _truncate(_cp_to_str(cp))
            success = cp.returncode == 0
            return ToolResult(
                name=self.name,
                success=success,
                output=output,
                error=None if success else f"exit code {cp.returncode}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


class NavigDockerExecTool(BaseTool):
    """Execute a command inside a Docker container on the remote host."""

    name = "navig_docker_exec"
    description = "Run a command inside a Docker container on the active remote host."
    owner_only = True
    parameters = [
        {"name": "container", "type": "string", "description": "Container name or ID", "required": True},
        {"name": "command", "type": "string", "description": "Command to execute inside the container", "required": True},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            container = args.get("container", "")
            command = args.get("command", "")
            if not container:
                return ToolResult(name=self.name, success=False, error="'container' is required")
            if not command:
                return ToolResult(name=self.name, success=False, error="'command' is required")

            host_name, host_config = _resolve_host()
            await self._emit(on_status, "docker-exec", f"{container}@{host_name}", 10)

            cmd = f"docker exec {shlex.quote(container)} {command}"
            remote_ops = _get_remote_ops()
            cp = await _run_sync(remote_ops.execute_command, cmd, host_config, capture_output=True)
            output = _truncate(_cp_to_str(cp))
            success = cp.returncode == 0
            return ToolResult(
                name=self.name,
                success=success,
                output=output,
                error=None if success else f"exit code {cp.returncode}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


class NavigDockerRestartTool(BaseTool):
    """Restart a Docker container on the remote host."""

    name = "navig_docker_restart"
    description = "Restart a Docker container on the active remote host."
    owner_only = True
    parameters = [
        {"name": "container", "type": "string", "description": "Container name or ID", "required": True},
        {"name": "timeout", "type": "integer", "description": "Timeout in seconds before killing (default: 10)", "required": False},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            container = args.get("container", "")
            if not container:
                return ToolResult(name=self.name, success=False, error="'container' is required")

            timeout = args.get("timeout", 10)
            host_name, host_config = _resolve_host()
            await self._emit(on_status, "docker-restart", f"{container}@{host_name}", 10)

            cmd = f"docker restart -t {timeout} {shlex.quote(container)}"
            remote_ops = _get_remote_ops()
            cp = await _run_sync(remote_ops.execute_command, cmd, host_config, capture_output=True)
            output = _truncate(_cp_to_str(cp))
            success = cp.returncode == 0
            return ToolResult(
                name=self.name,
                success=success,
                output=f"Restarted {container}" if success else output,
                error=None if success else f"exit code {cp.returncode}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


# ─────── Host operations ────────────────────────────────────


class NavigHostShowTool(BaseTool):
    """Show current active host information."""

    name = "navig_host_show"
    description = "Show the currently active host name, config, and connection details."
    owner_only = True
    parameters = []

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            cm = _get_config_manager()
            host_name = cm.get_active_server() or cm.get_active_host()
            if not host_name:
                return ToolResult(name=self.name, success=False, error="No active host configured")

            host_config = cm.load_host_config(host_name)
            if not host_config:
                return ToolResult(name=self.name, success=False, error=f"Host config not found: {host_name}")

            # Build safe summary (no passwords)
            safe_keys = ("host", "hostname", "user", "port", "type", "ssh_key", "description", "tags")
            info_lines = [f"Host: {host_name}"]
            for k in safe_keys:
                v = host_config.get(k)
                if v is not None:
                    info_lines.append(f"  {k}: {v}")

            return ToolResult(
                name=self.name,
                success=True,
                output="\n".join(info_lines),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


class NavigHostTestTool(BaseTool):
    """Test SSH connectivity to the active host."""

    name = "navig_host_test"
    description = "Test SSH connectivity to the active remote host."
    owner_only = True
    parameters = []

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            host_name, host_config = _resolve_host()
            await self._emit(on_status, "ssh-test", host_name, 10)

            remote_ops = _get_remote_ops()
            cp = await _run_sync(remote_ops.execute_command, "echo ok", host_config, capture_output=True)
            success = cp.returncode == 0

            return ToolResult(
                name=self.name,
                success=success,
                output=f"SSH to {host_name}: {'OK' if success else 'FAILED'}",
                error=None if success else f"SSH test failed (exit {cp.returncode})",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


class NavigHostMonitorTool(BaseTool):
    """Show server monitoring information (disk, CPU, memory)."""

    name = "navig_host_monitor"
    description = (
        "Show server health metrics: disk usage, CPU, memory, load average.  "
        "Set disk=true for disk-only, resources=true for CPU/memory-only."
    )
    owner_only = True
    parameters = [
        {"name": "disk", "type": "boolean", "description": "Show disk usage only", "required": False},
        {"name": "resources", "type": "boolean", "description": "Show CPU/memory only", "required": False},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            host_name, host_config = _resolve_host()
            await self._emit(on_status, "monitor", host_name, 10)

            disk_only = args.get("disk", False)
            res_only = args.get("resources", False)

            commands: list[str] = []
            if disk_only:
                commands.append("df -h")
            elif res_only:
                commands.append("free -h && echo '---' && uptime")
            else:
                commands.append("echo '=== Disk ===' && df -h && echo '=== Memory ===' && free -h && echo '=== Load ===' && uptime")

            remote_ops = _get_remote_ops()
            cp = await _run_sync(remote_ops.execute_command, commands[0], host_config, capture_output=True)
            output = _truncate(_cp_to_str(cp))
            success = cp.returncode == 0
            return ToolResult(
                name=self.name,
                success=success,
                output=output,
                error=None if success else f"exit code {cp.returncode}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


# ─────── Web server operations ──────────────────────────────


class NavigWebVhostsTool(BaseTool):
    """List virtual hosts / sites on the remote web server."""

    name = "navig_web_vhosts"
    description = "List virtual hosts (sites) configured on the remote web server (nginx/apache)."
    owner_only = True
    parameters = []

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            host_name, host_config = _resolve_host()
            await self._emit(on_status, "vhosts", host_name, 10)

            # Detect nginx or apache and list enabled sites
            cmd = (
                "if command -v nginx >/dev/null 2>&1; then "
                "echo '=== Nginx ===' && ls -1 /etc/nginx/sites-enabled/ 2>/dev/null || nginx -T 2>/dev/null | grep server_name; "
                "elif command -v apache2ctl >/dev/null 2>&1; then "
                "echo '=== Apache ===' && apache2ctl -S 2>&1; "
                "elif command -v httpd >/dev/null 2>&1; then "
                "echo '=== Apache ===' && httpd -S 2>&1; "
                "else echo 'No web server detected'; fi"
            )

            remote_ops = _get_remote_ops()
            cp = await _run_sync(remote_ops.execute_command, cmd, host_config, capture_output=True)
            output = _truncate(_cp_to_str(cp))
            success = cp.returncode == 0
            return ToolResult(
                name=self.name,
                success=success,
                output=output,
                error=None if success else f"exit code {cp.returncode}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


class NavigWebReloadTool(BaseTool):
    """Reload the web server configuration (nginx/apache)."""

    name = "navig_web_reload"
    description = (
        "Safely reload the web server config on the remote host.  "
        "Tests config syntax first, then reloads."
    )
    owner_only = True
    parameters = []

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            host_name, host_config = _resolve_host()
            await self._emit(on_status, "web-reload", host_name, 10)

            # Test-then-reload pattern
            cmd = (
                "if command -v nginx >/dev/null 2>&1; then "
                "nginx -t 2>&1 && systemctl reload nginx && echo 'Nginx reloaded'; "
                "elif command -v apache2ctl >/dev/null 2>&1; then "
                "apache2ctl configtest 2>&1 && systemctl reload apache2 && echo 'Apache reloaded'; "
                "elif command -v httpd >/dev/null 2>&1; then "
                "httpd -t 2>&1 && systemctl reload httpd && echo 'Apache reloaded'; "
                "else echo 'No web server detected'; fi"
            )

            remote_ops = _get_remote_ops()
            cp = await _run_sync(remote_ops.execute_command, cmd, host_config, capture_output=True)
            output = _truncate(_cp_to_str(cp))
            success = cp.returncode == 0
            return ToolResult(
                name=self.name,
                success=success,
                output=output,
                error=None if success else f"Config test or reload failed: {_cp_to_str(cp)[:500]}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


# ─────── App operations ─────────────────────────────────────


class NavigAppListTool(BaseTool):
    """List applications configured on the active host."""

    name = "navig_app_list"
    description = "List all applications configured for the active host."
    owner_only = True
    parameters = []

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            cm = _get_config_manager()
            host_name = cm.get_active_server() or cm.get_active_host()
            if not host_name:
                return ToolResult(name=self.name, success=False, error="No active host configured")

            # Apps are stored in the host config under 'apps' key
            host_config = cm.load_host_config(host_name)
            apps = host_config.get("apps", {}) if host_config else {}
            active_app = cm.get_active_app() if hasattr(cm, "get_active_app") else None

            if not apps:
                return ToolResult(
                    name=self.name,
                    success=True,
                    output=f"No apps configured for host {host_name}",
                    elapsed_ms=(time.monotonic() - t0) * 1000,
                )

            lines = [f"Apps on {host_name}:"]
            for app_name in sorted(apps.keys()) if isinstance(apps, dict) else apps:
                marker = " (active)" if app_name == active_app else ""
                lines.append(f"  - {app_name}{marker}")
            return ToolResult(
                name=self.name,
                success=True,
                output="\n".join(lines),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


class NavigAppShowTool(BaseTool):
    """Show configuration for the active application."""

    name = "navig_app_show"
    description = "Show configuration details for the currently active application."
    owner_only = True
    parameters = [
        {"name": "app_name", "type": "string", "description": "Application name (default: active app)", "required": False},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        try:
            cm = _get_config_manager()
            host_name = cm.get_active_server() or cm.get_active_host()
            if not host_name:
                return ToolResult(name=self.name, success=False, error="No active host configured")

            app_name = args.get("app_name") or (cm.get_active_app() if hasattr(cm, "get_active_app") else None)
            if not app_name:
                return ToolResult(name=self.name, success=False, error="No active app — specify 'app_name' or run 'navig app use <name>'")

            host_config = cm.load_host_config(host_name)
            apps = host_config.get("apps", {}) if host_config else {}
            app_config = apps.get(app_name) if isinstance(apps, dict) else None

            if not app_config:
                return ToolResult(name=self.name, success=False, error=f"App '{app_name}' not found on host {host_name}")

            # Build safe display (strip sensitive fields)
            sensitive = {"password", "token", "secret", "api_key", "key"}
            lines = [f"App: {app_name}  (host: {host_name})"]
            for k, v in sorted(app_config.items()) if isinstance(app_config, dict) else []:
                if any(s in k.lower() for s in sensitive):
                    lines.append(f"  {k}: ****")
                else:
                    lines.append(f"  {k}: {v}")

            return ToolResult(
                name=self.name,
                success=True,
                output="\n".join(lines),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc), elapsed_ms=(time.monotonic() - t0) * 1000)


# ═════════════════════════════════════════════════════════════
# Registration
# ═════════════════════════════════════════════════════════════

_ALL_DEVOPS_TOOLS: list[type[BaseTool]] = [
    # Remote execution
    NavigRunTool,
    # File operations
    NavigFileAddTool,
    NavigFileGetTool,
    NavigFileShowTool,
    # Database operations
    NavigDbQueryTool,
    NavigDbDumpTool,
    NavigDbListTool,
    # Docker operations
    NavigDockerPsTool,
    NavigDockerLogsTool,
    NavigDockerExecTool,
    NavigDockerRestartTool,
    # Host operations
    NavigHostShowTool,
    NavigHostTestTool,
    NavigHostMonitorTool,
    # Web server operations
    NavigWebVhostsTool,
    NavigWebReloadTool,
    # App operations
    NavigAppListTool,
    NavigAppShowTool,
]


def register_devops_tools() -> None:
    """Register all DevOps tools under the ``"devops"`` toolset."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    for tool_cls in _ALL_DEVOPS_TOOLS:
        try:
            _AGENT_REGISTRY.register(tool_cls(), toolset="devops")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to register devops tool %s: %s", tool_cls.name, exc)
    logger.debug(
        "Agent devops tools registered: %s",
        ", ".join(t.name for t in _ALL_DEVOPS_TOOLS),
    )
