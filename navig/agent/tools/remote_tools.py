"""
navig.agent.tools.remote_tools — Agent tools for remote host execution (FC3).

Four :class:`BaseTool` subclasses that expose :class:`RemoteAgentExecutor`
to the agentic ReAct loop:

* ``remote_execute``    — run a single command on a (optionally specified) host
* ``remote_file_read``  — read a remote file via ``navig file show``
* ``remote_host_switch``— switch the active host context
* ``remote_multi_host`` — execute the same command across multiple hosts in parallel
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from navig.agent.agent_tool_registry import _AGENT_REGISTRY
from navig.agent.remote_agent import (
    RemoteAgentExecutor,
    RemoteCommand,
)
from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

# Shared singleton — created lazily on first tool call
_executor: RemoteAgentExecutor | None = None


def _get_executor() -> RemoteAgentExecutor:
    global _executor  # noqa: PLW0603
    if _executor is None:
        _executor = RemoteAgentExecutor()
    return _executor


# ═════════════════════════════════════════════════════════════
# Tool implementations
# ═════════════════════════════════════════════════════════════


class RemoteExecuteTool(BaseTool):
    """Execute an arbitrary command on a remote host via the navig CLI pipeline."""

    name = "remote_execute"
    description = (
        "Execute a shell command on a remote host.  The command is routed "
        "through the full navig pipeline (SSH tunneling, host resolution, "
        "confirmation gates).  Special characters are auto-detected and "
        "base64-encoded when necessary."
    )
    owner_only = True
    parameters = [
        {
            "name": "command",
            "type": "string",
            "description": "Shell command to execute",
            "required": True,
        },
        {
            "name": "host",
            "type": "string",
            "description": "Target host name (default: active host)",
            "required": False,
        },
        {
            "name": "b64",
            "type": "boolean",
            "description": "Force base64 encoding (auto-detected if omitted)",
            "required": False,
        },
        {
            "name": "timeout",
            "type": "integer",
            "description": "Timeout in seconds (default: 120)",
            "required": False,
        },
    ]

    async def run(
        self, args: dict[str, Any], on_status: StatusCallback | None = None
    ) -> ToolResult:
        t0 = time.monotonic()
        try:
            command = args.get("command", "")
            if not command:
                return ToolResult(name=self.name, success=False, error="'command' is required")

            host = args.get("host")
            use_b64 = args.get("b64")
            timeout = args.get("timeout")

            await self._emit(on_status, "remote", f"Executing on {host or 'active host'}", 10)

            executor = _get_executor()
            result = await executor.execute_command(
                command,
                host=host,
                use_b64=use_b64,
                timeout=timeout,
            )

            return ToolResult(
                name=self.name,
                success=result.success,
                output=result.output,
                error=result.error,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                name=self.name,
                success=False,
                error=str(exc),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )


class RemoteFileReadTool(BaseTool):
    """Read a file on the remote host via ``navig file show``."""

    name = "remote_file_read"
    description = (
        "Read the contents of a file on the active (or specified) remote host.  "
        "Supports --tail and --lines options for large files."
    )
    owner_only = False
    parameters = [
        {
            "name": "path",
            "type": "string",
            "description": "Remote file path to read",
            "required": True,
        },
        {
            "name": "host",
            "type": "string",
            "description": "Target host (default: active host)",
            "required": False,
        },
        {
            "name": "tail",
            "type": "boolean",
            "description": "Read from end of file",
            "required": False,
        },
        {
            "name": "lines",
            "type": "string",
            "description": "Line limit (e.g. '50' or '100-200')",
            "required": False,
        },
    ]

    async def run(
        self, args: dict[str, Any], on_status: StatusCallback | None = None
    ) -> ToolResult:
        t0 = time.monotonic()
        try:
            path = args.get("path", "")
            if not path:
                return ToolResult(name=self.name, success=False, error="'path' is required")

            # Build navig file show command
            parts = ["navig", "file", "show", f'"{path}"']
            host = args.get("host")
            if host:
                parts.extend(["--host", host])
            if args.get("tail"):
                parts.append("--tail")
            lines = args.get("lines")
            if lines:
                parts.extend(["--lines", str(lines)])

            cmd = " ".join(parts)
            await self._emit(on_status, "remote", f"Reading {path}", 10)

            executor = _get_executor()
            result = await executor.execute_command(
                cmd,
                host=None,  # Already part of the navig command
                use_b64=False,
                timeout=args.get("timeout", 60),
            )

            return ToolResult(
                name=self.name,
                success=result.success,
                output=result.output,
                error=result.error,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                name=self.name,
                success=False,
                error=str(exc),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )


class RemoteHostSwitchTool(BaseTool):
    """Switch the active remote host context."""

    name = "remote_host_switch"
    description = (
        "Switch the active remote host context so subsequent remote commands "
        "target the new host.  Equivalent to ``navig host use <host>``."
    )
    owner_only = False
    parameters = [
        {
            "name": "host",
            "type": "string",
            "description": "Name of the host to switch to",
            "required": True,
        },
        {
            "name": "verify",
            "type": "boolean",
            "description": "Run connectivity test after switching (default: true)",
            "required": False,
        },
    ]

    async def run(
        self, args: dict[str, Any], on_status: StatusCallback | None = None
    ) -> ToolResult:
        t0 = time.monotonic()
        try:
            host = args.get("host", "")
            if not host:
                return ToolResult(name=self.name, success=False, error="'host' is required")

            await self._emit(on_status, "host", f"Switching to {host}", 20)

            executor = _get_executor()
            result = await executor.set_active_host(host)
            if not result.success:
                return ToolResult(
                    name=self.name,
                    success=False,
                    output=result.output,
                    error=result.error or f"Failed to switch to host {host}",
                    elapsed_ms=(time.monotonic() - t0) * 1000,
                )

            # Optionally verify connectivity (default: yes)
            verify = args.get("verify", True)
            if verify:
                await self._emit(on_status, "host", f"Verifying {host}", 60)
                test_result = await executor.verify_host(host)
                if not test_result.success:
                    return ToolResult(
                        name=self.name,
                        success=False,
                        output=f"Switched to {host} but connectivity test failed:\n{test_result.output}",
                        error=test_result.error,
                        elapsed_ms=(time.monotonic() - t0) * 1000,
                    )

            return ToolResult(
                name=self.name,
                success=True,
                output=f"Active host switched to: {host}"
                + ("\nConnectivity verified." if verify else ""),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                name=self.name,
                success=False,
                error=str(exc),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )


class RemoteMultiHostTool(BaseTool):
    """Execute the same command across multiple hosts in parallel."""

    name = "remote_multi_host"
    description = (
        "Execute a command across multiple remote hosts simultaneously.  "
        "Results are returned per-host.  Concurrency is bounded to 5 hosts.  "
        "This tool is internally parallel — do NOT call it in parallel with itself."
    )
    owner_only = True
    parameters = [
        {
            "name": "command",
            "type": "string",
            "description": "Shell command to execute on each host",
            "required": True,
        },
        {
            "name": "hosts",
            "type": "array",
            "description": "List of host names to target",
            "required": True,
        },
        {
            "name": "b64",
            "type": "boolean",
            "description": "Force base64 encoding",
            "required": False,
        },
        {
            "name": "timeout",
            "type": "integer",
            "description": "Per-host timeout in seconds",
            "required": False,
        },
    ]

    async def run(
        self, args: dict[str, Any], on_status: StatusCallback | None = None
    ) -> ToolResult:
        t0 = time.monotonic()
        try:
            command = args.get("command", "")
            hosts = args.get("hosts", [])
            if not command:
                return ToolResult(name=self.name, success=False, error="'command' is required")
            if not hosts or not isinstance(hosts, list):
                return ToolResult(
                    name=self.name, success=False, error="'hosts' must be a non-empty list"
                )

            use_b64 = args.get("b64")
            timeout = args.get("timeout")

            await self._emit(
                on_status,
                "remote",
                f"Dispatching to {len(hosts)} hosts",
                10,
            )

            commands = [
                RemoteCommand(
                    command=command,
                    host=h,
                    use_b64=use_b64 if use_b64 is not None else False,
                    timeout=timeout or 120,
                    description=f"exec on {h}",
                )
                for h in hosts
            ]

            executor = _get_executor()
            results = await executor.execute_parallel(commands)

            # Format results per-host
            output_lines = []
            all_success = True
            for r in results:
                status = "OK" if r.success else "FAIL"
                output_lines.append(
                    f"[{r.host}] {status} (exit={r.return_code}, {r.elapsed_s:.1f}s)"
                )
                if r.stdout.strip():
                    # Indent output under host header
                    for line in r.stdout.strip().split("\n")[:20]:
                        output_lines.append(f"  {line}")
                if r.error:
                    output_lines.append(f"  ERROR: {r.error}")
                if not r.success:
                    all_success = False

            return ToolResult(
                name=self.name,
                success=all_success,
                output="\n".join(output_lines),
                error=None
                if all_success
                else f"{sum(1 for r in results if not r.success)}/{len(results)} hosts failed",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                name=self.name,
                success=False,
                error=str(exc),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )


# ── Registration ─────────────────────────────────────────────


def register_remote_executor_tools() -> None:
    """Register all remote executor tools into the agent registry."""
    _AGENT_REGISTRY.register(RemoteExecuteTool(), toolset="remote")
    _AGENT_REGISTRY.register(RemoteFileReadTool(), toolset="remote")
    _AGENT_REGISTRY.register(RemoteHostSwitchTool(), toolset="remote")
    _AGENT_REGISTRY.register(RemoteMultiHostTool(), toolset="remote")
    logger.debug(
        "Agent remote executor tools registered: "
        "remote_execute, remote_file_read, remote_host_switch, remote_multi_host"
    )
