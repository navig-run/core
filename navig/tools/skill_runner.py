"""SkillRunTool — Execute a NAVIG skill command from the pipeline.

Runs `navig skill run <skill_id> <command> [extra_args...]` as a subprocess
through the canonical ``navig.tools.proc`` engine (non-blocking, shell=False).

Usage::

    result = await SkillRunTool().run({
        "skill_id": "git-ops",
        "command": "summary",
    })
"""

from __future__ import annotations

import shlex
from typing import Any

from loguru import logger

from navig.tools.proc import ProcessOptions, run_process
from navig.tools.registry import BaseTool, StatusCallback, ToolResult

_TIMEOUT_SECS = 30
_OUTPUT_CAP = 6_000


class SkillRunTool(BaseTool):
    """Execute a NAVIG skill command."""

    name = "skill_run"
    description = (
        "Execute a NAVIG skill command. "
        "Call this when user wants to run a specific skill operation."
    )

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        skill_id: str = str(args.get("skill_id", "")).strip()
        command: str = str(args.get("command", "")).strip()
        extra: list[str] = [str(a) for a in args.get("extra_args", [])]

        if not skill_id:
            return ToolResult(
                name=self.name,
                success=False,
                error="skill_id arg required. Use /skill list to see available skills.",
            )

        await self._emit(on_status, f"Running skill `{skill_id}`…", command or "(no command)", 20)

        # Validate that the skill exists
        try:
            from navig.skills.loader import skills_by_id  # lazy

            index = skills_by_id()
            if skill_id not in index:
                available = ", ".join(sorted(index.keys())[:20])
                return ToolResult(
                    name=self.name,
                    success=False,
                    error=(
                        f"Skill `{skill_id}` not found. "
                        f"Available: {available}"
                        f"{'...' if len(index) > 20 else ''}"
                    ),
                )
            skill = index[skill_id]
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill_runner: loader error: {}", exc)
            skill = None  # proceed anyway — let navig CLI report the error

        # If no command, show skill info
        if not command:
            if skill is not None:
                summary = (
                    f"**{skill.name}** (v{skill.version}) — {skill.safety}\n"
                    f"Category: {skill.category}\n"
                    f"Tags: {', '.join(skill.tags) or 'none'}\n\n" + skill.body_markdown[:1500]
                )
                return ToolResult(
                    name=self.name,
                    success=True,
                    output={"info": summary},
                )
            return ToolResult(name=self.name, success=False, error="command arg required")

        # Build CLI invocation
        navig_bin_argv = _find_navig_bin()
        cmd = navig_bin_argv + ["skill", "run", skill_id, command] + extra

        await self._emit(
            on_status,
            f"Executing {skill_id} {command}…",
            " ".join(shlex.quote(c) for c in cmd),
            50,
        )

        proc_result = await run_process(
            cmd,
            ProcessOptions(
                timeout_s=_TIMEOUT_SECS,
                output_cap=_OUTPUT_CAP,
            ),
        )

        await self._emit(
            on_status,
            "Processing output…",
            f"exit {proc_result.returncode} ({proc_result.elapsed_ms:.0f}ms)",
            85,
        )

        combined = (
            proc_result.stdout + ("\n" + proc_result.stderr if proc_result.stderr else "")
        ).strip()

        returncode = proc_result.returncode
        if returncode != 0 or proc_result.termination != "exit":
            return ToolResult(
                name=self.name,
                success=False,
                error=(
                    f"Skill exited {returncode}"
                    if proc_result.termination == "exit"
                    else f"Skill {proc_result.termination.replace('_', ' ')}"
                ),
                output={
                    "output": combined,
                    "skill_id": skill_id,
                    "command": command,
                    "elapsed_ms": proc_result.elapsed_ms,
                    "termination": proc_result.termination,
                },
            )

        return ToolResult(
            name=self.name,
            success=True,
            output={
                "output": combined,
                "skill_id": skill_id,
                "command": command,
                "elapsed_ms": proc_result.elapsed_ms,
                "truncated": proc_result.truncated,
            },
        )


def _find_navig_bin() -> list[str]:
    """Return the argv prefix for the navig CLI binary.

    Always returns a *list* so callers can safely do::

        cmd = _find_navig_bin() + ["skill", "run", skill_id, command]

    The install's entry point is preferred (it may carry its own interpreter in
    a uv-managed env), but it must be a program CreateProcess can execute: the
    first ``navig`` on the Windows PATH is the ``navig.cmd`` shim, which the old
    ``shutil.which("navig")`` happily returned. ``navig_launcher`` refuses the
    shim and falls back to ``sys.executable -m navig``.
    """
    from navig.platform.navig_cli import navig_launcher

    return navig_launcher(prefer_entrypoint=True)
