"""
navig.agent.remote_agent — Remote Agent Executor (FC3).

Orchestrates command execution across one or more remote hosts via the
existing ``navig run`` CLI pipeline.  Supports sequential multi-step tasks
on a single host and parallel dispatch across multiple hosts.

Uses ``asyncio.create_subprocess_shell`` so every command flows through
navig's full CLI pipeline (host resolution, b64 encoding, confirmation
gates, SSH tunneling).

Usage::

    from navig.agent.remote_agent import RemoteAgentExecutor

    executor = RemoteAgentExecutor()
    result = await executor.execute_command("ls -la /var/www")
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ── Caps ─────────────────────────────────────────────────────
MAX_CONCURRENT_HOSTS = 5
MAX_OUTPUT_CHARS = 30_000
COMMAND_TIMEOUT = int(os.environ.get("NAVIG_REMOTE_TIMEOUT", "120"))


# ── Data structures ──────────────────────────────────────────


class CommandState(Enum):
    """Lifecycle state of a remote command."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass
class RemoteCommand:
    """Single command to execute on a remote host."""

    command: str
    host: str | None = None
    use_b64: bool = False
    timeout: int = COMMAND_TIMEOUT
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "host": self.host,
            "use_b64": self.use_b64,
            "timeout": self.timeout,
            "description": self.description,
        }


@dataclass
class RemoteResult:
    """Outcome of a single remote command execution."""

    host: str
    state: CommandState
    stdout: str = ""
    stderr: str = ""
    return_code: int = -1
    elapsed_s: float = 0.0
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.state == CommandState.COMPLETED and self.return_code == 0

    @property
    def output(self) -> str:
        """Merged stdout + stderr for display."""
        parts: list[str] = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(f"[stderr] {self.stderr}")
        return "\n".join(parts) or "(no output)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "state": self.state.value,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_code": self.return_code,
            "elapsed_s": round(self.elapsed_s, 2),
            "error": self.error,
            "success": self.success,
        }


@dataclass
class RemoteTask:
    """Multi-step sequential task on a single host."""

    host: str
    commands: list[RemoteCommand] = field(default_factory=list)
    results: list[RemoteResult] = field(default_factory=list)
    stop_on_error: bool = True

    @property
    def success(self) -> bool:
        return all(r.success for r in self.results)

    @property
    def summary(self) -> str:
        passed = sum(1 for r in self.results if r.success)
        total = len(self.results)
        return f"{self.host}: {passed}/{total} commands succeeded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "commands": [c.to_dict() for c in self.commands],
            "results": [r.to_dict() for r in self.results],
            "stop_on_error": self.stop_on_error,
            "success": self.success,
            "summary": self.summary,
        }


# ── Executor ─────────────────────────────────────────────────


class RemoteAgentExecutor:
    """Execute commands on remote hosts via the navig CLI pipeline.

    Parameters
    ----------
    max_concurrent : int
        Maximum number of parallel host executions (default 5).
    default_timeout : int
        Per-command timeout in seconds (default from env or 120).
    """

    def __init__(
        self,
        max_concurrent: int = MAX_CONCURRENT_HOSTS,
        default_timeout: int = COMMAND_TIMEOUT,
    ) -> None:
        self.max_concurrent = max_concurrent
        self.default_timeout = default_timeout
        self._semaphore = asyncio.Semaphore(max_concurrent)

    # ── Single command ───────────────────────────────────────

    async def execute_command(
        self,
        command: str,
        *,
        host: str | None = None,
        use_b64: bool | None = None,
        timeout: int | None = None,
    ) -> RemoteResult:
        """Execute a single command on a remote host.

        Args:
            command: Shell command to execute.
            host: Target host (uses active host if None).
            use_b64: Force base64 encoding.  Auto-detected when None.
            timeout: Override per-command timeout.

        Returns:
            :class:`RemoteResult` with stdout/stderr/return_code.
        """
        if use_b64 is None:
            use_b64 = _needs_b64(command)

        navig_cmd = _build_navig_command(command, host=host, use_b64=use_b64)
        effective_timeout = timeout or self.default_timeout
        host_label = host or "(active)"

        logger.debug("remote_agent: executing on %s: %s", host_label, navig_cmd[:120])
        t0 = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_shell(
                navig_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )
            elapsed = time.monotonic() - t0

            stdout_str = _truncate(stdout_bytes.decode("utf-8", errors="replace"))
            stderr_str = _truncate(stderr_bytes.decode("utf-8", errors="replace"))

            return RemoteResult(
                host=host_label,
                state=CommandState.COMPLETED,
                stdout=stdout_str,
                stderr=stderr_str,
                return_code=proc.returncode or 0,
                elapsed_s=elapsed,
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            logger.warning("remote_agent: timeout after %.1fs on %s", elapsed, host_label)
            return RemoteResult(
                host=host_label,
                state=CommandState.TIMED_OUT,
                elapsed_s=elapsed,
                error=f"Timed out after {effective_timeout}s",
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - t0
            logger.warning("remote_agent: error on %s: %s", host_label, exc)
            return RemoteResult(
                host=host_label,
                state=CommandState.FAILED,
                elapsed_s=elapsed,
                error=str(exc),
            )

    # ── Sequential task ──────────────────────────────────────

    async def execute_task(self, task: RemoteTask) -> RemoteTask:
        """Execute a multi-step sequential task on a single host.

        Commands run one at a time.  If ``task.stop_on_error`` is True
        (default), execution halts after the first non-zero exit code.

        Returns the same *task* object with ``results`` populated.
        """
        for cmd in task.commands:
            result = await self.execute_command(
                cmd.command,
                host=cmd.host or task.host,
                use_b64=cmd.use_b64 or None,
                timeout=cmd.timeout,
            )
            task.results.append(result)
            if task.stop_on_error and not result.success:
                logger.info(
                    "remote_agent: stopping task on %s after failure (exit=%d)",
                    task.host,
                    result.return_code,
                )
                break
        return task

    # ── Parallel multi-host ──────────────────────────────────

    async def execute_parallel(
        self,
        commands: list[RemoteCommand],
    ) -> list[RemoteResult]:
        """Execute commands across multiple hosts in parallel.

        Concurrency is bounded by ``self.max_concurrent`` via a semaphore.
        Follows the same pattern as :class:`CoordinatorAgent`.

        Returns a list of :class:`RemoteResult` in the same order as *commands*.
        """
        if not commands:
            return []

        async def _guarded(cmd: RemoteCommand) -> RemoteResult:
            async with self._semaphore:
                return await self.execute_command(
                    cmd.command,
                    host=cmd.host,
                    use_b64=cmd.use_b64 or None,
                    timeout=cmd.timeout,
                )

        results = await asyncio.gather(
            *[_guarded(c) for c in commands],
            return_exceptions=True,
        )

        # Convert exceptions to RemoteResult
        final: list[RemoteResult] = []
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                final.append(
                    RemoteResult(
                        host=commands[i].host or "(active)",
                        state=CommandState.FAILED,
                        error=str(r),
                    )
                )
            else:
                final.append(r)
        return final

    # ── Host management ──────────────────────────────────────

    @staticmethod
    async def verify_host(host: str) -> RemoteResult:
        """Run ``navig host test`` against *host* and return the result."""
        cmd = f"navig host test --host {host} --plain"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=30,
            )
        except asyncio.TimeoutError:
            return RemoteResult(
                host=host,
                state=CommandState.TIMED_OUT,
                error="Host test timed out after 30s",
            )
        return RemoteResult(
            host=host,
            state=CommandState.COMPLETED,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            return_code=proc.returncode or 0,
        )

    @staticmethod
    async def set_active_host(host: str) -> RemoteResult:
        """Switch the active host context via ``navig host use``."""
        cmd = f"navig host use {host}"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=15,
            )
        except asyncio.TimeoutError:
            return RemoteResult(
                host=host,
                state=CommandState.TIMED_OUT,
                error="host use timed out after 15s",
            )
        return RemoteResult(
            host=host,
            state=CommandState.COMPLETED,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            return_code=proc.returncode or 0,
        )


# ── Helpers (module-private) ─────────────────────────────────


def _needs_b64(command: str) -> bool:
    """Heuristic: does *command* contain characters that need b64 wrapping?"""
    # Characters that commonly break shell piping via SSH
    dangerous = set("$!(){}\"'\\`[]|;&<>")
    return bool(dangerous & set(command))


def _build_navig_command(
    command: str,
    *,
    host: str | None = None,
    use_b64: bool = False,
) -> str:
    """Build the full ``navig run ...`` CLI invocation string.

    When *use_b64* is True the command is base64-encoded locally and
    passed via ``--b64`` so the remote shell never interprets special chars.
    """
    parts = ["navig", "run"]

    if host:
        parts.extend(["--host", host])

    # Always auto-confirm for agent-driven execution
    parts.append("--yes")

    if use_b64:
        encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
        parts.extend(["--b64", encoded])
    else:
        # Quote the command for the local shell
        parts.append(f'"{command}"')

    return " ".join(parts)


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Cap output length to prevent token explosion."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated — {len(text)} chars total]"
