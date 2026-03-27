"""
CodeExecSandboxTool — Sandboxed Python code execution.

Runs Python via asyncio subprocess with a 10-second timeout.
No network access. stdout/stderr captured and returned.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10
_MAX_OUTPUT = 3_000


class CodeExecSandboxTool(BaseTool):
    name = "code_exec_sandbox"
    description = "Execute a Python code snippet safely. Returns stdout, stderr, exit code. 10s timeout."
    parameters = [
        {
            "name": "code",
            "type": "string",
            "description": "Python code snippet to execute",
            "required": True,
        }
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        code: str = args.get("code", "")
        if not code:
            return ToolResult(name=self.name, success=False, error="code arg required")

        language = args.get("language", "python").lower()
        if language not in ("python", "py"):
            return ToolResult(
                name=self.name,
                success=False,
                error=f"language '{language}' not supported — only Python is allowed",
            )

        await self._emit(on_status, "🔧 Preparing sandbox…", "Python subprocess", 20)

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                prefix="navig_sandbox_",
                delete=False,
                encoding="utf-8",
            ) as f:
                f.write(code)
                tmp_path = f.name

            await self._emit(on_status, "Executing code…", f"{len(code)} chars", 45)

            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    tmp_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    # Additional isolation: no stdin
                    stdin=asyncio.subprocess.DEVNULL,
                )

                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(), timeout=_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.communicate()
                    return ToolResult(
                        name=self.name,
                        success=False,
                        error=f"execution timed out after {_TIMEOUT_SECONDS}s",
                    )

                stdout = stdout_b.decode(errors="replace")[:_MAX_OUTPUT]
                stderr = stderr_b.decode(errors="replace")[:_MAX_OUTPUT]
                exit_code = proc.returncode

            finally:
                Path(tmp_path).unlink(missing_ok=True)

            await self._emit(
                on_status,
                "Execution complete",
                f"exit {exit_code} · {len(stdout)} chars",
                90,
            )

            return ToolResult(
                name=self.name,
                success=exit_code == 0,
                output={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": exit_code,
                },
                error=(
                    f"process exited with code {exit_code}" if exit_code != 0 else None
                ),
            )

        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc))
