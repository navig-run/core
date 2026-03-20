"""
Exec Tool Pack — bash_exec: sandboxed shell command execution.

Runs arbitrary shell commands via the canonical ``navig.tools.proc`` engine.
Output is capped at 50,000 characters.  Registered as SafetyLevel.DANGEROUS so
strict-mode deployments block it outright; standard / permissive modes still
apply destructive-pattern checks before allowing execution.

Usage (via ToolRouter)::

    result = await router.async_execute(ToolCallAction(
        tool="bash_exec",
        parameters={"command": "ls -la /tmp", "timeout_seconds": 15},
    ))
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine, Dict, Optional, TYPE_CHECKING

from navig.tools.proc import ProcessOptions, run_process, shell_argv

if TYPE_CHECKING:
    from navig.tools.router import ToolRegistry

logger = logging.getLogger("navig.tools.packs.exec_pack")

# Hard cap on stdout/stderr combined — prevents context overflow
_OUTPUT_CAP = 50_000


# =============================================================================
# Core execution
# =============================================================================

async def _run_shell(
    command: str,
    cwd: Optional[str] = None,
    timeout_seconds: float = 60.0,
    env_extra: Optional[Dict[str, str]] = None,
    on_event: Optional[Callable[[str, str], Coroutine]] = None,
) -> Dict[str, Any]:
    """
    Async shell execution via ``proc.run_process``.

    Returns a dict with keys:
        stdout, stderr, returncode, timed_out, truncated, elapsed_ms, termination
    """
    result = await run_process(
        shell_argv(command),
        ProcessOptions(
            timeout_s=timeout_seconds,
            cwd=cwd,
            env_extra=env_extra,
            output_cap=_OUTPUT_CAP,
            on_event=on_event,
        ),
    )
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
        "timed_out": result.termination in ("timeout", "no_output_timeout"),
        "truncated": result.truncated,
        "elapsed_ms": result.elapsed_ms,
        "termination": result.termination,
    }


# =============================================================================
# Handler
# =============================================================================

def bash_exec_handler(
    command: str,
    cwd: Optional[str] = None,
    timeout_seconds: float = 60.0,
    env_extra: Optional[Dict[str, str]] = None,
    **_: Any,
) -> Dict[str, Any]:
    """
    Sync wrapper around _run_shell for use in the ToolRouter sync path.
    """
    from navig.tools.proc import run_process_sync

    result = run_process_sync(
        shell_argv(command),
        ProcessOptions(
            timeout_s=timeout_seconds,
            cwd=cwd,
            env_extra=env_extra,
            output_cap=_OUTPUT_CAP,
        ),
    )
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
        "timed_out": result.termination in ("timeout", "no_output_timeout"),
        "truncated": result.truncated,
        "elapsed_ms": result.elapsed_ms,
        "termination": result.termination,
    }


async def bash_exec_async_handler(
    command: str,
    cwd: Optional[str] = None,
    timeout_seconds: float = 60.0,
    env_extra: Optional[Dict[str, str]] = None,
    **_: Any,
) -> Dict[str, Any]:
    """
    Async variant — used by the ToolRouter async_execute path so the
    coroutine is properly awaited without spinning a new event loop.
    """
    return await _run_shell(
        command=command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        env_extra=env_extra,
    )


# =============================================================================
# Registration
# =============================================================================

def register_tools(registry: "ToolRegistry") -> None:
    """Register bash_exec with the ToolRegistry."""
    from navig.tools.router import ToolMeta, ToolDomain, SafetyLevel

    registry.register(
        ToolMeta(
            name="bash_exec",
            domain=ToolDomain.SYSTEM,
            description=(
                "Execute a shell command and return stdout, stderr, and exit code. "
                "Output is capped at 50,000 characters. "
                "DANGEROUS — blocked in strict safety mode."
            ),
            safety=SafetyLevel.DANGEROUS,
            parameters_schema={
                "command": {
                    "type": "string",
                    "required": True,
                    "description": "Shell command to execute",
                },
                "cwd": {
                    "type": "string",
                    "required": False,
                    "description": "Working directory (default: process cwd)",
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 60.0,
                    "description": "Max seconds to wait for the command (default: 60)",
                },
                "env_extra": {
                    "type": "object",
                    "required": False,
                    "description": "Extra environment variables to inject",
                },
            },
            tags=["shell", "exec", "bash", "system", "dangerous"],
        ),
        handler=bash_exec_async_handler,
    )
    logger.debug("exec_pack: registered bash_exec")
