"""Effect execution — every tool runs the CLI command it is bound to.

The router never reimplements a device action: a tool's ``command`` tuple + its
resolved target is turned into a real ``navig …`` argv and run through an
injectable :class:`CommandRunner`. That is what makes the plan phase provably pure
(a test's fake runner records argv and can assert NOTHING mutating ran) and what
keeps the router free of plugin imports.

The subprocess bridge follows the house rules: ``navig.platform.process.spawn_kwargs``
for a console-free child, an explicit ``encoding="utf-8"`` (the source guard asks
for a decision, not a default), a timeout, and JSON parsed only when the command
declares ``--json`` (read-only tools do; mutating ones print their human result).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from navig.reflex.manifest import ToolManifest

#: A single bound command may not hang the turn; read-only ones are slower.
TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class CommandOutcome:
    """What one bound command did."""

    argv: tuple[str, ...]
    ok: bool
    exit_code: int = 0
    payload: Any = None
    output: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "ok": self.ok,
            "exit_code": self.exit_code,
            "output": self.output,
        }


class CommandRunner(Protocol):
    """Anything that can run a ``navig`` argv — injected in tests, real by default."""

    def run(self, argv: Sequence[str]) -> CommandOutcome:
        """Run *argv* and report the outcome. Never raises for a non-zero exit."""


class SubprocessRunner:
    """The production runner: a fixed-argv ``navig`` child with no console window."""

    def __init__(self, *, timeout: int = TIMEOUT_SECONDS) -> None:
        self._timeout = int(timeout)

    def run(self, argv: Sequence[str]) -> CommandOutcome:
        args = [str(item) for item in argv]
        try:
            from navig.platform.process import spawn_kwargs  # noqa: PLC0415

            extra = spawn_kwargs()
        except Exception:  # noqa: BLE001 — a broken platform layer still runs the CLI
            extra = {}
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
                stdin=subprocess.DEVNULL,
                **extra,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return CommandOutcome(argv=tuple(args), ok=False, error=str(exc))
        output = (completed.stdout or "").strip()
        error = (completed.stderr or "").strip()
        payload = _parse_json(output)
        return CommandOutcome(
            argv=tuple(args),
            ok=completed.returncode == 0,
            exit_code=completed.returncode,
            payload=payload,
            output=output,
            error=error,
        )


@dataclass
class ExecutionResult:
    """Every command a commit ran, in order."""

    outcomes: list[CommandOutcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and all(item.ok for item in self.outcomes)

    @property
    def payload(self) -> Any:
        for item in self.outcomes:
            if item.payload is not None:
                return item.payload
        return None

    @property
    def output(self) -> str:
        lines = [item.output for item in self.outcomes if item.output]
        return lines[0] if lines else ""

    @property
    def error(self) -> str:
        for item in self.outcomes:
            if item.error and not item.ok:
                return item.error
        return ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "commands": [item.as_dict() for item in self.outcomes],
        }


def _parse_json(output: str) -> Any:
    if not output:
        return None
    try:
        return json.loads(output)
    except (TypeError, ValueError):
        return None


def _navig_executable() -> str:
    """The console entry point, or ``python -m navig`` when the script is not on PATH."""
    found = shutil.which("navig")
    if found:
        return found
    return sys.executable


def logical_argv(tool: ToolManifest, slots: dict[str, Any], target_id: str = "") -> list[str]:
    """The command as an operator would read it: ``navig home off desk-lamp``."""
    argv = ["navig", *tool.command, *_arguments(tool, slots, target_id)]
    if tool.read_only:
        argv.append("--json")
    return argv


def command_argv(tool: ToolManifest, slots: dict[str, Any], target_id: str = "") -> list[str]:
    """Build the argv for one target (without running it).

    ``--json`` is appended only for read-only tools: all three status commands
    accept it, while the mutating TV commands do not. Mutating home commands print
    a human result, which becomes the Answer's display text.
    """
    logical = logical_argv(tool, slots, target_id)
    executable = _navig_executable()
    if executable.lower().endswith(("python.exe", "python")):
        return [executable, "-m", *logical]
    return [executable, *logical[1:]]


def _format_value(capability: str, value: Any) -> str:
    if capability == "target_temp":
        return f"target_temp={float(value)}"
    if capability == "color_temp":
        return f"color_temp={int(value)}"
    if capability == "color":
        return f"color={value}"
    if capability == "brightness":
        return f"brightness={int(value)}"
    return f"{capability}={value}"


def _device_arg(target_id: str) -> str:
    """The inventory id a ``navig home …`` command expects.

    The endpoint contract's stable id is ``<provider>:<device id>`` (``manual:desk-lamp``),
    but the home CLI resolves devices against ``devices.yaml`` by their bare inventory id
    (``desk-lamp``). Passing the endpoint id would make every ``navig home on|off|toggle|
    set|status <id>`` fail "device not found", so the command gets the device part while
    the plan, blast radius and Answer keep the stable endpoint id.
    """
    text = str(target_id or "")
    provider, sep, device = text.partition(":")
    return device if sep and device else text


def _arguments(tool: ToolManifest, slots: dict[str, Any], target_id: str) -> list[str]:
    name = tool.name
    device = _device_arg(target_id)
    if name in {"home.on", "home.off", "home.toggle"}:
        return [device] if device else []
    if name == "home.set":
        capability = str(slots.get("capability") or "brightness")
        if device and slots.get("value") is not None:
            return [device, _format_value(capability, slots["value"])]
        return [device] if device else []
    if name == "home.status":
        return [device] if device else []
    if name == "tv.power":
        return ["--on"] if slots.get("on") else []
    if name == "tv.volume":
        direction = str(slots.get("direction") or "up")
        return ["mute"] if direction == "mute" else [direction]
    if name == "coordination.inbox":
        return [str(slots.get("recipient") or "operator")]
    return []


def execute(
    tool: ToolManifest,
    slots: dict[str, Any],
    target_ids: Sequence[str],
    runner: CommandRunner,
) -> ExecutionResult:
    """Run one command per target (a batch is N idempotent commands under one token)."""
    result = ExecutionResult()
    targets: list[str] = [str(item) for item in target_ids if str(item)]
    if not targets:
        targets = [""]
    for target_id in targets:
        result.outcomes.append(runner.run(command_argv(tool, slots, target_id)))
    return result


__all__ = [
    "CommandOutcome",
    "CommandRunner",
    "ExecutionResult",
    "SubprocessRunner",
    "TIMEOUT_SECONDS",
    "command_argv",
    "execute",
]
