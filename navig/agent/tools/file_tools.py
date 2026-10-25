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

import difflib
import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_MAX_READ_CHARS = 8_000
_MAX_WRITE_CHARS = 50_000
_MAX_DIR_ENTRIES = 200


def _active_cwd() -> Path:
    """The active workshop's working directory (gateway-safe via the session
    ContextVar; falls back to the process cwd)."""
    try:
        from navig.spaces.active import get_active_working_dir  # noqa: PLC0415

        return get_active_working_dir()
    except Exception:  # noqa: BLE001
        return Path(os.getcwd())


def _workshop_path(raw_path: str | None) -> Path:
    """Resolve a tool path against the active workshop: absolute paths as-is,
    relative (or empty) paths against the active working directory — so an
    agent's relative reads/writes land inside the space, not the repo root."""
    base = _active_cwd()
    if not raw_path:
        return base
    p = Path(raw_path).expanduser()
    return p if p.is_absolute() else (base / p)


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

        path = _workshop_path(raw_path)
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

        path = _workshop_path(raw_path)
        encoding = args.get("encoding") or "utf-8"
        mode = "a" if args.get("append") else "w"

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Close inside the try (via the context manager): text-mode writes buffer
            # and the real OS write happens at flush/close, so a disk-full / quota
            # error surfaces there. Without `with`, the handle is finalized by GC
            # *after* this block and that close-time OSError is swallowed — the tool
            # would then report "Written N chars" over an empty or partial file.
            with path.open(mode, encoding=encoding) as fh:
                fh.write(content)
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
        path = _workshop_path(args.get("path"))

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


class EditFileTool(BaseTool):
    """Replace exact text in an existing file (str_replace-style edit).

    Deliberately distinct from ``write_file``: this tool never creates a
    file and never rewrites whole content — it performs an exact-match,
    ambiguity-checked, optionally hash-verified in-place edit with an
    atomic replace.  Creation stays ``write_file``'s job.
    """

    name = "edit_file"
    description = (
        "Replace exact text in an existing file. 'old_string' must match exactly "
        "once (or set replace_all=true); zero or multiple matches are refused with "
        "guidance. Optionally pass expected_hash (the SHA-256 hex of the file bytes "
        "you last read) to refuse the edit if the file changed since. Never creates "
        "files — use write_file for that. Returns a small diff of the change."
    )
    owner_only = True
    parameters = [
        {
            "name": "path",
            "type": "string",
            "description": "Absolute or relative path of the file to edit (must exist)",
            "required": True,
        },
        {
            "name": "old_string",
            "type": "string",
            "description": "Exact text to replace (must be non-empty and unique unless replace_all)",
            "required": True,
        },
        {
            "name": "new_string",
            "type": "string",
            "description": "Replacement text (may be empty to delete old_string)",
            "required": True,
        },
        {
            "name": "replace_all",
            "type": "boolean",
            "description": "Replace every occurrence instead of requiring exactly one (default: false)",
            "required": False,
        },
        {
            "name": "expected_hash",
            "type": "string",
            "description": "Optional SHA-256 hex of the file as you last read it — edit is refused on mismatch",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()

        def _fail(msg: str) -> ToolResult:
            return ToolResult(
                name=self.name,
                success=False,
                error=msg,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        raw_path = args.get("path", "")
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        replace_all = bool(args.get("replace_all", False))
        expected_hash = args.get("expected_hash") or ""

        if not raw_path:
            return _fail("'path' arg is required")
        if not isinstance(old_string, str) or not old_string:
            return _fail("'old_string' must be a non-empty string — use write_file to create files")
        if not isinstance(new_string, str):
            return _fail("'new_string' must be a string")
        if len(old_string) > _MAX_WRITE_CHARS or len(new_string) > _MAX_WRITE_CHARS:
            return _fail(f"Strings too large (max {_MAX_WRITE_CHARS:,} chars)")

        path = _workshop_path(raw_path)
        try:
            resolved = path.resolve()
        except OSError as exc:
            return _fail(f"Cannot resolve path: {exc}")

        if not resolved.exists():
            return _fail(f"File not found: {resolved} — use write_file to create it")
        if not resolved.is_file():
            return _fail(f"Path is not a file: {resolved}")

        try:
            data = resolved.read_bytes()
        except PermissionError:
            return _fail(f"Permission denied reading: {resolved}")
        except OSError as exc:
            return _fail(str(exc))

        if expected_hash:
            digest = hashlib.sha256(data).hexdigest()
            if digest.lower() != str(expected_hash).strip().lower():
                return _fail(
                    "File changed since it was read (expected_hash mismatch) — "
                    "re-read the file and retry the edit"
                )

        bom = b""
        if data.startswith(b"\xef\xbb\xbf"):
            bom, data = data[:3], data[3:]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return _fail("Not a UTF-8 text file — refusing to edit (could corrupt encoding)")

        # Match against the ORIGINAL text so untouched regions keep their exact
        # bytes (line endings included).  Models send \n-only strings, so a CRLF
        # file is also matched against a \r\n variant of old_string; the
        # replacement then mirrors the matched variant's endings.
        def _to_crlf(s: str) -> str:
            return re.sub(r"(?<!\r)\n", "\r\n", s)

        candidates: list[str] = []
        for cand in (old_string, _to_crlf(old_string)):
            if cand not in candidates:
                candidates.append(cand)
        counts = [text.count(c) for c in candidates]
        occurrences = sum(counts)
        if occurrences == 0:
            preview = old_string if len(old_string) <= 120 else old_string[:120] + "…"
            return _fail(
                f"old_string not found in {resolved.name!r} — no exact match for: {preview!r}"
            )
        if occurrences > 1 and not replace_all:
            return _fail(
                f"old_string matches {occurrences} times (must be unique) — add surrounding "
                "context to make it unique, or set replace_all=true"
            )

        idx = next(i for i, c in enumerate(counts) if c)
        match_old = candidates[idx]
        repl = _to_crlf(new_string) if "\r\n" in match_old else new_string

        if replace_all:
            updated = text.replace(match_old, repl)
            replaced = occurrences
        else:
            updated = text.replace(match_old, repl, 1)
            replaced = 1

        if len(updated) > _MAX_WRITE_CHARS:
            return _fail(
                f"Result would be too large ({len(updated):,} chars; max {_MAX_WRITE_CHARS:,})"
            )

        out_bytes = bom + updated.encode("utf-8")

        # Bounded diff of the changed region.
        try:
            diff_lines = list(
                difflib.unified_diff(
                    text.splitlines(),
                    updated.splitlines(),
                    fromfile=f"a/{resolved.name}",
                    tofile=f"b/{resolved.name}",
                    lineterm="",
                    n=2,
                )
            )
        except (ValueError, TypeError):
            diff_lines = []
        diff_text = "\n".join(diff_lines[:40])
        if len(diff_lines) > 40:
            diff_text += "\n…[diff truncated]"
        if len(diff_text) > 2_000:
            diff_text = diff_text[:2_000] + "\n…[diff truncated]"

        # Atomic replace: same-directory temp file, then os.replace.  Works on
        # the RESOLVED path so a symlinked file keeps its link (the target's
        # content changes; the link itself is never replaced).
        tmp = resolved.with_name(f".{resolved.name}.navig-edit-{os.getpid()}.tmp")
        try:
            with open(tmp, "wb") as fh:
                fh.write(out_bytes)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.chmod(tmp, resolved.stat().st_mode)
            except OSError:
                pass
            os.replace(tmp, resolved)
        except PermissionError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return _fail(f"Permission denied writing: {resolved}")
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return _fail(str(exc))

        summary = f"Replaced {replaced} occurrence(s) in {resolved}"
        output = summary + (f"\n{diff_text}" if diff_text else "")
        return ToolResult(
            name=self.name,
            success=True,
            output=output,
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )
