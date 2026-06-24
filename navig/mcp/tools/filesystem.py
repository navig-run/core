"""MCP tool bundle: cross-platform filesystem operations.

Exposes a single ``desktop_filesystem`` tool with eight modes:
    read, write, copy, move, delete, list, search, info

All operations are backed by :mod:`navig.platform.filesystem_ops` (pure
stdlib — no Windows-specific dependencies).

Tools exposed
-------------
- ``desktop_filesystem`` – unified file/directory CRUD, list, search, and info
"""

from __future__ import annotations

from typing import Any

from navig.platform import filesystem_ops as _fs

# ── Tool schema ───────────────────────────────────────────────────────────────

_TOOLS: dict[str, dict] = {
    "desktop_filesystem": {
        "name": "desktop_filesystem",
        "description": (
            "Manage file system operations with eight modes: "
            "'read' (read text file contents with optional 1-based line offset/limit), "
            "'write' (create or overwrite a file; set append=true to append), "
            "'copy' (copy file or directory to destination), "
            "'move' (move or rename file/directory), "
            "'delete' (delete file or directory; set recursive=true for non-empty dirs), "
            "'list' (list directory contents with optional glob pattern filter), "
            "'search' (find files matching a glob pattern under a root directory), "
            "'info' (get file/directory metadata: size, dates, type). "
            "Use absolute paths. Relative paths are NOT supported."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["read", "write", "copy", "move", "delete", "list", "search", "info"],
                    "description": "Operation mode.",
                },
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file or directory to operate on.",
                },
                "destination": {
                    "type": "string",
                    "description": "Destination path (required for copy and move modes).",
                },
                "content": {
                    "type": "string",
                    "description": "Text content to write (required for write mode).",
                },
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to filter (required for search; optional for list).",
                },
                "recursive": {
                    "type": ["boolean", "string"],
                    "default": False,
                    "description": "Recurse into sub-directories (list, search, delete).",
                },
                "append": {
                    "type": ["boolean", "string"],
                    "default": False,
                    "description": "Append to file instead of overwriting (write mode).",
                },
                "overwrite": {
                    "type": ["boolean", "string"],
                    "default": False,
                    "description": "Allow overwriting existing destination (copy, move).",
                },
                "offset": {
                    "type": "integer",
                    "description": "1-based starting line number for read mode.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to return (read mode).",
                },
                "encoding": {
                    "type": "string",
                    "default": "utf-8",
                    "description": "Text encoding for read/write operations.",
                },
                "show_hidden": {
                    "type": ["boolean", "string"],
                    "default": False,
                    "description": "Include hidden files/dirs starting with '.' (list mode).",
                },
            },
            "required": ["mode", "path"],
        },
    }
}


# ── Registration ──────────────────────────────────────────────────────────────


def register(server: Any) -> None:
    """Register the filesystem tool bundle on *server*."""
    server.tools.update(_TOOLS)
    server._tool_handlers.update(
        {
            "desktop_filesystem": _tool_filesystem,
        }
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _coerce_bool(value: bool | str | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return default


# ── Handler ───────────────────────────────────────────────────────────────────


def _tool_filesystem(server: Any, args: dict[str, Any]) -> Any:
    mode: str = args.get("mode", "")
    path: str = args.get("path", "")
    destination: str | None = args.get("destination")
    content: str | None = args.get("content")
    pattern: str | None = args.get("pattern")
    recursive = _coerce_bool(args.get("recursive"), default=False)
    append = _coerce_bool(args.get("append"), default=False)
    overwrite = _coerce_bool(args.get("overwrite"), default=False)
    show_hidden = _coerce_bool(args.get("show_hidden"), default=False)
    offset: int | None = args.get("offset")
    limit: int | None = args.get("limit")
    encoding: str = args.get("encoding", "utf-8")

    try:
        if mode == "read":
            return _fs.read_file(path, offset=offset, limit=limit, encoding=encoding)
        if mode == "write":
            if content is None:
                return "Error: content parameter is required for write mode."
            return _fs.write_file(path, content, append=append, encoding=encoding)
        if mode == "copy":
            if destination is None:
                return "Error: destination parameter is required for copy mode."
            return _fs.copy_path(path, destination, overwrite=overwrite)
        if mode == "move":
            if destination is None:
                return "Error: destination parameter is required for move mode."
            return _fs.move_path(path, destination, overwrite=overwrite)
        if mode == "delete":
            return _fs.delete_path(path, recursive=recursive)
        if mode == "list":
            return _fs.list_directory(
                path, pattern=pattern, recursive=recursive, show_hidden=show_hidden
            )
        if mode == "search":
            if pattern is None:
                return "Error: pattern parameter is required for search mode."
            return _fs.search_files(path, pattern, recursive=recursive)
        if mode == "info":
            return _fs.get_file_info(path)
        return f'Error: Unknown mode "{mode}". Use: read, write, copy, move, delete, list, search, info.'
    except Exception as exc:  # noqa: BLE001
        return f"Error in desktop_filesystem ({mode}): {exc}"
