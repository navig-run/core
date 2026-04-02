"""
navig.agent.tools.file_tools — File system agent tools.

Provides lightweight ``read_file``, ``write_file``, and ``list_files`` tools
that integrate with the :class:`~navig.agent.agent_tool_registry.AgentToolRegistry`.

These tools are intentionally minimal — they do NOT replace navig's structured
``navig file`` command group; they provide the LLM with direct file I/O
during an agentic session on the local (development) filesystem.

For remote-host file operations use the ``navig_file_*`` tools in
``navig.agent.tools.devops_tools`` (MVP3).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_MAX_READ_CHARS = 8_000
_MAX_WRITE_CHARS = 50_000
_MAX_DIR_ENTRIES = 200


class ReadFileTool(BaseTool):
    """Read a local file and return its contents as a string."""

    name = "read_file"
    description = (
        "Read the contents of a local file.  Returns the file content as text.  "
        "Use relative or absolute paths.  Large files are truncated."
    )
    owner_only = False
    parameters = [
        {
            "name": "path",
            "type": "string",
            "description": "Absolute or relative file path to read",
            "required": True,
        },
        {
            "name": "start_line",
            "type": "integer",
            "description": "1-based line number to start reading from (optional)",
            "required": False,
        },
        {
            "name": "end_line",
            "type": "integer",
            "description": "1-based line number to stop reading at (inclusive, optional)",
            "required": False,
        },
        {
            "name": "encoding",
            "type": "string",
            "description": "File encoding (default: utf-8)",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        raw_path = args.get("path", "")
        if not raw_path:
            return ToolResult(name=self.name, success=False, error="'path' arg is required")

        path = Path(raw_path).expanduser()
        if not path.exists():
            return ToolResult(
                name=self.name,
                success=False,
                error=f"File not found: {path}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        if not path.is_file():
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Path is not a file: {path}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        encoding = args.get("encoding") or "utf-8"
        start_line = args.get("start_line")
        end_line = args.get("end_line")

        try:
            text = path.read_text(encoding=encoding, errors="replace")
        except PermissionError:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Permission denied reading: {path}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        if start_line or end_line:
            lines = text.splitlines(keepends=True)
            s = max(0, (int(start_line) - 1 if start_line else 0))
            e = int(end_line) if end_line else len(lines)
            text = "".join(lines[s:e])

        if len(text) > _MAX_READ_CHARS:
            text = text[:_MAX_READ_CHARS] + f"\n…[truncated at {_MAX_READ_CHARS} chars]"

        return ToolResult(
            name=self.name,
            success=True,
            output=text,
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


class WriteFileTool(BaseTool):
    """Write content to a local file (create or overwrite)."""

    name = "write_file"
    description = (
        "Write text content to a local file.  Creates the file and parent "
        "directories if they do not exist.  Overwrites existing content."
    )
    owner_only = True
    parameters = [
        {
            "name": "path",
            "type": "string",
            "description": "Absolute or relative file path to write",
            "required": True,
        },
        {
            "name": "content",
            "type": "string",
            "description": "Text content to write to the file",
            "required": True,
        },
        {
            "name": "encoding",
            "type": "string",
            "description": "File encoding (default: utf-8)",
            "required": False,
        },
        {
            "name": "append",
            "type": "boolean",
            "description": "If true, append to file instead of overwriting (default: false)",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        raw_path = args.get("path", "")
        content = args.get("content", "")
        if not raw_path:
            return ToolResult(name=self.name, success=False, error="'path' arg is required")
        if not isinstance(content, str):
            return ToolResult(name=self.name, success=False, error="'content' must be a string")

        if len(content) > _MAX_WRITE_CHARS:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Content too large ({len(content):,} chars; max {_MAX_WRITE_CHARS:,})",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        path = Path(raw_path).expanduser()
        encoding = args.get("encoding") or "utf-8"
        mode = "a" if args.get("append") else "w"

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.open(mode, encoding=encoding).write(content)
        except PermissionError:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Permission denied writing: {path}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except OSError as exc:
            return ToolResult(
                name=self.name,
                success=False,
                error=str(exc),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        return ToolResult(
            name=self.name,
            success=True,
            output=f"Written {len(content):,} chars to {path}",
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


class ListFilesTool(BaseTool):
    """List the contents of a local directory."""

    name = "list_files"
    description = (
        "List files and directories at a given path.  Returns a structured "
        "listing with names, types, and sizes."
    )
    owner_only = False
    parameters = [
        {
            "name": "path",
            "type": "string",
            "description": "Directory path to list (default: current directory)",
            "required": False,
        },
        {
            "name": "recursive",
            "type": "boolean",
            "description": "If true, recurse into subdirectories (default: false)",
            "required": False,
        },
        {
            "name": "show_hidden",
            "type": "boolean",
            "description": "If true, include hidden files (default: false)",
            "required": False,
        },
        {
            "name": "pattern",
            "type": "string",
            "description": "Glob pattern to filter files (e.g. '*.py')",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        raw_path = args.get("path") or os.getcwd()
        path = Path(raw_path).expanduser()

        if not path.exists():
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Path not found: {path}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        recursive = bool(args.get("recursive", False))
        show_hidden = bool(args.get("show_hidden", False))
        pattern = args.get("pattern") or "*"

        try:
            if recursive:
                entries_iter = path.rglob(pattern)
            else:
                entries_iter = path.glob(pattern)

            entries: list[dict[str, Any]] = []
            for entry in sorted(entries_iter, key=lambda p: (p.is_file(), p.name)):
                if not show_hidden and entry.name.startswith("."):
                    continue
                try:
                    stat = entry.stat()
                    entries.append(
                        {
                            "name": str(entry.relative_to(path)),
                            "type": "file" if entry.is_file() else "dir",
                            "size": stat.st_size if entry.is_file() else None,
                        }
                    )
                except (PermissionError, OSError):
                    continue
                if len(entries) >= _MAX_DIR_ENTRIES:
                    entries.append({"name": f"…and more (limit {_MAX_DIR_ENTRIES})", "type": "truncated", "size": None})
                    break

        except PermissionError:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Permission denied listing: {path}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        return ToolResult(
            name=self.name,
            success=True,
            output={"path": str(path), "entries": entries, "count": len(entries)},
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )
