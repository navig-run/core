"""
navig.tools.bash_exec — Safe shell command execution tool.

Security notes
--------------
- Commands are executed via ``asyncio.create_subprocess_exec`` with ``shell=False``
  and argument splitting performed by :func:`shlex.split`.  Shell metacharacter
  injection is therefore not possible.
- Working directory and environment inheritance are configurable but default to
  safe values (current CWD, minimal environment).
- Commands that require approval must set ``requires_approval=True`` in args; the
  tool will raise an :class:`ApprovalRequiredError` rather than execute them unless
  :envvar:`NAVIG_ALLOW_ALL_COMMANDS` is set (test environments only).

Registered as ``"bash_exec"`` in the default tool registry.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time
from dataclasses import dataclass
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0
_MAX_OUTPUT_CHARS = 8_000


def split_command(command: str) -> list[str]:
    """Split *command* into argv the way the operator meant it.

    On Windows ``shlex.split(posix=False)`` keeps backslashes (paths survive) but
    ALSO keeps the quote characters, so ``python -c "print(42)"`` passed the
    program the literal ``"print(42)"`` — a string expression that prints nothing —
    and ``curl -H "Accept: x"`` sent a header with quotes in it. Strip one pair of
    matching outer quotes from each token.
    """
    if os.name != "nt":
        return shlex.split(command, posix=True)
    argv = shlex.split(command, posix=False)
    return [
        t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in ('"', "'") else t
        for t in argv
    ]


def empty_output_note(returncode: int) -> str:
    """What a command that printed NOTHING reports, instead of an empty string.

    An empty result reads to a model like "no answer came back", and it reported a
    reachable service as down: ``curl -s https://rdap.org/domain/schema.org`` exits 0
    with an empty body because rdap.org answers with a redirect (2026-10-16). Saying
    explicitly that the output was empty — and what that does NOT prove — lets the
    model retry properly instead of concluding the network is broken.
    """
    if returncode == 0:
        return (
            "[empty output: the command exited 0 and printed nothing to stdout or "
            "stderr. This is not evidence of a network or service failure. A silent "
            "`curl -s` usually means a redirect it did not follow (add -L) — for a web "
            "page prefer web_fetch, for a domain's expiry/registrar use domain_lookup.]"
        )
    return (
        f"[empty output: the command exited {returncode} and printed nothing. The "
        "failure has no message — check that the program exists and takes these "
        "arguments before concluding anything about the network.]"
    )


class ApprovalRequiredError(RuntimeError):
    """Raised when a command is marked ``requires_approval=True`` and approval
    has not been granted by the environment."""


@dataclass
class _ExecArgs:
    command: str
    cwd: str | None = None
    env_extra: dict[str, str] | None = None
    timeout: float = _DEFAULT_TIMEOUT
    max_output: int = _MAX_OUTPUT_CHARS
    requires_approval: bool = False


class BashExecTool(BaseTool):
    """Execute a shell command safely (no shell interpolation).

    Args schema (dict)
    ------------------
    command          : str     — Command string to execute (e.g. "ls -la /tmp")
    cwd              : str     — Working directory (default: current directory)
    env_extra        : dict    — Additional env vars merged over os.environ
    timeout          : float   — Max seconds to wait (default 30)
    max_output       : int     — Truncate combined stdout+stderr to this many chars
    requires_approval: bool    — If True, the command does not run unless allowed
    """

    name = "bash_exec"
    description = (
        "Execute a system command safely.  The command string is split by "
        "shlex and passed to the OS without shell interpolation.  "
        "Set requires_approval=True for any command that modifies state."
    )
    owner_only = True
    parameters = [
        {
            "name": "command",
            "type": "string",
            "description": "Command string to execute",
            "required": True,
        },
        {
            "name": "cwd",
            "type": "string",
            "description": "Working directory relative to workspace",
            "required": False,
        },
        {
            "name": "timeout",
            "type": "number",
            "description": "Max seconds to wait",
            "required": False,
        },
        {
            "name": "requires_approval",
            "type": "boolean",
            "description": "Flag for execution gating",
            "required": False,
        },
    ]

    def _build_env(self, extra: dict[str, str] | None) -> dict[str, str]:
        env = dict(os.environ)
        if extra:
            env.update(extra)
        return env

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()

        # --- Parse and validate args ---
        command = args.get("command", "").strip()
        if not command:
            return ToolResult(
                name=self.name,
                success=False,
                output=None,
                error="'command' arg is required and must not be empty",
                elapsed_ms=0.0,
                status_events=[],
            )

        requires_approval = bool(args.get("requires_approval", False))
        allow_all = os.getenv("NAVIG_ALLOW_ALL_COMMANDS", "").lower() in (
            "1",
            "true",
            "yes",
        )
        if requires_approval and not allow_all:
            return ToolResult(
                name=self.name,
                success=False,
                output=None,
                error=(
                    "Command requires approval (requires_approval=True). "
                    "Grant approval before executing."
                ),
                elapsed_ms=(time.monotonic() - t0) * 1000,
                status_events=[],
            )

        timeout = float(args.get("timeout", _DEFAULT_TIMEOUT))
        max_output = int(args.get("max_output", _MAX_OUTPUT_CHARS))
        cwd = args.get("cwd") or None
        env_extra = args.get("env_extra") or {}

        # --- Split command safely ---
        try:
            argv = split_command(command)
        except ValueError as exc:
            return ToolResult(
                name=self.name,
                success=False,
                output=None,
                error=f"Command parse error: {exc}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
                status_events=[],
            )

        if not argv:
            return ToolResult(
                name=self.name,
                success=False,
                output=None,
                error="Empty command after parsing",
                elapsed_ms=(time.monotonic() - t0) * 1000,
                status_events=[],
            )

        status_events: list[str] = []

        async def _status(msg: str) -> None:
            # `on_status` is a StatusCallback — a THREE-argument coroutine function. This
            # used to declare it as `Callable[[str], None]` and call `on_status(msg)`
            # without awaiting, so under the Telegram path (which supplies a real async
            # callback) every status event became a coroutine that was created, dropped,
            # and never ran: the live progress line simply never updated.
            status_events.append(msg)
            await self._emit(on_status, msg)

        await _status(f"exec: {argv[0]} (args={len(argv) - 1})")

        # --- Execute ---
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=self._build_env(env_extra),
            )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return ToolResult(
                    name=self.name,
                    success=False,
                    output=None,
                    error=f"Command timed out after {timeout:.0f}s",
                    elapsed_ms=(time.monotonic() - t0) * 1000,
                    status_events=status_events,
                )

            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
            combined = (stdout + ("\n[stderr]\n" + stderr if stderr.strip() else "")).strip()

            if len(combined) > max_output:
                combined = combined[:max_output] + f"\n… [truncated at {max_output} chars]"

            returncode = proc.returncode or 0
            success = returncode == 0

            await _status(f"exit_code={returncode}")

            if not combined:
                combined = empty_output_note(returncode)

            return ToolResult(
                name=self.name,
                success=success,
                output=combined,
                error=None if success else f"Non-zero exit code: {returncode}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
                status_events=status_events,
            )

        except FileNotFoundError:
            return ToolResult(
                name=self.name,
                success=False,
                output=None,
                error=f"Executable not found: {argv[0]!r}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
                status_events=status_events,
            )
        except PermissionError:
            return ToolResult(
                name=self.name,
                success=False,
                output=None,
                error=f"Permission denied: {argv[0]!r}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
                status_events=status_events,
            )
