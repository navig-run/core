"""
System Tool Pack - system_info, file_read.

Lightweight system inspection tools (safe, read-only).
"""
from __future__ import annotations
import platform
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.tools.router import ToolRegistry


def _system_info(**kwargs):
    """Return basic system information."""
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "node": platform.node(),
    }


def _file_read(path: str, max_lines: int = 200, **kwargs):
    """Read a local file (capped at max_lines)."""
    from pathlib import Path
    p = Path(path).expanduser()
    if not p.is_file():
        return {"error": f"File not found: {path}"}
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    truncated = len(lines) > max_lines
    return {
        "path": str(p),
        "lines": lines[:max_lines],
        "total_lines": len(lines),
        "truncated": truncated,
    }


def register_tools(registry: "ToolRegistry") -> None:
    from navig.tools.router import ToolMeta, ToolDomain, SafetyLevel

    registry.register(
        ToolMeta(
            name="system_info",
            domain=ToolDomain.SYSTEM,
            description="Get basic system/platform information.",
            safety=SafetyLevel.SAFE,
            parameters_schema={},
            tags=["system", "info", "diagnostics"],
        ),
        handler=_system_info,
    )

    registry.register(
        ToolMeta(
            name="file_read",
            domain=ToolDomain.SYSTEM,
            description="Read a local file (capped output).",
            safety=SafetyLevel.MODERATE,
            parameters_schema={
                "path": {"type": "string", "required": True, "description": "File path"},
                "max_lines": {"type": "integer", "default": 200, "description": "Max lines to return"},
            },
            tags=["file", "read", "system"],
        ),
        handler=_file_read,
    )
