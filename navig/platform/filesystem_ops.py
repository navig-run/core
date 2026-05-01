"""
Cross-platform filesystem helper functions for structured file operations.

Ported and adapted from Windows-MCP ``filesystem/service.py`` (MIT).
All functions are pure stdlib — no Windows-specific dependencies.

Public API
----------
- ``read_file``         – read a text file (with optional line range)
- ``write_file``        – create / overwrite / append a text file
- ``copy_path``         – copy a file or directory
- ``move_path``         – move or rename a file or directory
- ``delete_path``       – delete a file or directory
- ``list_directory``    – list directory contents with optional glob filter
- ``search_files``      – glob-search for files under a root
- ``get_file_info``     – return detailed file/directory metadata
"""

from __future__ import annotations

import fnmatch
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_MAX_READ_SIZE: int = 10 * 1024 * 1024  # 10 MB
_MAX_RESULTS: int = 500


# ── Data models ───────────────────────────────────────────────────────────────


def _format_size(num_bytes: int) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes //= 1024
    return f"{num_bytes:.1f} PB"


@dataclass
class _FileInfo:
    path: str
    type: str
    size: int
    created: datetime
    modified: datetime
    accessed: datetime
    read_only: bool
    extension: Optional[str] = None
    link_target: Optional[str] = None
    contents_files: Optional[int] = None
    contents_dirs: Optional[int] = None

    def to_string(self) -> str:
        lines = [
            f"Path: {self.path}",
            f"Type: {self.type}",
            f"Size: {_format_size(self.size)} ({self.size:,} bytes)",
            f"Created: {self.created.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Modified: {self.modified.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Accessed: {self.accessed.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Read-only: {self.read_only}",
        ]
        if self.contents_files is not None and self.contents_dirs is not None:
            lines.append(f"Contents: {self.contents_files} files, {self.contents_dirs} directories")
        if self.extension is not None:
            lines.append(f"Extension: {self.extension}")
        if self.link_target is not None:
            lines.append(f"Link target: {self.link_target}")
        return "\n".join(lines)


@dataclass
class _DirEntry:
    name: str
    is_dir: bool
    size: int = 0

    def to_string(self, relative_path: str | None = None) -> str:
        entry_type = "DIR " if self.is_dir else "FILE"
        size_str = f"  {_format_size(self.size)}" if not self.is_dir else ""
        display = relative_path or self.name
        return f"{entry_type}  {display}{size_str}"


# ── Public functions ──────────────────────────────────────────────────────────


def read_file(
    path: str,
    offset: int | None = None,
    limit: int | None = None,
    encoding: str = "utf-8",
) -> str:
    """Read the contents of a text file.

    Args:
        path: Absolute path to the file.
        offset: 1-based starting line number (inclusive).
        limit: Maximum number of lines to return.
        encoding: Text encoding (default ``utf-8``).

    Returns:
        File contents as a string, prefixed with path and line info when
        a range is requested.
    """
    file_path = Path(path).resolve()

    if not file_path.exists():
        return f"Error: File not found: {file_path}"
    if not file_path.is_file():
        return f"Error: Path is not a file: {file_path}"
    if file_path.stat().st_size > _MAX_READ_SIZE:
        return (
            f"Error: File too large ({file_path.stat().st_size:,} bytes). "
            f"Maximum is {_MAX_READ_SIZE:,} bytes. Use offset/limit to read in chunks."
        )

    try:
        with open(file_path, "r", encoding=encoding, errors="replace") as fh:
            if offset is not None or limit is not None:
                lines = fh.readlines()
                start = max(0, (offset or 1) - 1)  # convert 1-based to 0-based
                end = start + limit if limit else len(lines)
                selected = lines[start:end]
                total = len(lines)
                content = "".join(selected)
                return (
                    f"File: {file_path}\nLines {start + 1}-{min(end, total)} of {total}:\n{content}"
                )
            content = fh.read()
            return f"File: {file_path}\n{content}"
    except UnicodeDecodeError:
        return f'Error: Unable to read file as text with encoding "{encoding}". File may be binary.'
    except PermissionError:
        return f"Error: Permission denied: {file_path}"
    except Exception as exc:  # noqa: BLE001
        return f"Error reading file: {exc}"


def write_file(
    path: str,
    content: str,
    append: bool = False,
    encoding: str = "utf-8",
    create_parents: bool = True,
) -> str:
    """Write or append text content to a file.

    Args:
        path: Absolute path to the target file.
        content: Text content to write.
        append: If ``True``, append instead of overwriting.
        encoding: Text encoding.
        create_parents: Automatically create missing parent directories.
    """
    file_path = Path(path).resolve()

    try:
        if create_parents:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(file_path, mode, encoding=encoding) as fh:
            fh.write(content)
        action = "Appended to" if append else "Written to"
        size = file_path.stat().st_size
        return f"{action} {file_path} ({size:,} bytes)"
    except PermissionError:
        return f"Error: Permission denied: {file_path}"
    except Exception as exc:  # noqa: BLE001
        return f"Error writing file: {exc}"


def copy_path(source: str, destination: str, overwrite: bool = False) -> str:
    """Copy a file or directory to a new location."""
    src = Path(source).resolve()
    dst = Path(destination).resolve()

    if not src.exists():
        return f"Error: Source not found: {src}"
    if dst.exists() and not overwrite:
        return f"Error: Destination already exists: {dst}. Set overwrite=True to replace."

    try:
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            return f"Copied file: {src} -> {dst}"
        if src.is_dir():
            if dst.exists() and overwrite:
                shutil.rmtree(str(dst))
            shutil.copytree(str(src), str(dst))
            return f"Copied directory: {src} -> {dst}"
        return f"Error: Unsupported file type: {src}"
    except PermissionError:
        return "Error: Permission denied."
    except Exception as exc:  # noqa: BLE001
        return f"Error copying: {exc}"


def move_path(source: str, destination: str, overwrite: bool = False) -> str:
    """Move or rename a file or directory."""
    src = Path(source).resolve()
    dst = Path(destination).resolve()

    if not src.exists():
        return f"Error: Source not found: {src}"
    if dst.exists() and not overwrite:
        return f"Error: Destination already exists: {dst}. Set overwrite=True to replace."

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and overwrite:
            if dst.is_dir():
                shutil.rmtree(str(dst))
            else:
                dst.unlink()
        shutil.move(str(src), str(dst))
        return f"Moved: {src} -> {dst}"
    except PermissionError:
        return "Error: Permission denied."
    except Exception as exc:  # noqa: BLE001
        return f"Error moving: {exc}"


def delete_path(path: str, recursive: bool = False) -> str:
    """Delete a file or directory."""
    target = Path(path).resolve()

    if not target.exists():
        return f"Error: Path not found: {target}"

    try:
        if target.is_file() or target.is_symlink():
            target.unlink()
            return f"Deleted file: {target}"
        if target.is_dir():
            if not recursive:
                if any(target.iterdir()):
                    return (
                        f"Error: Directory is not empty: {target}. "
                        "Set recursive=True to delete non-empty directories."
                    )
                target.rmdir()
            else:
                shutil.rmtree(str(target))
            return f"Deleted directory: {target}"
        return f"Error: Unsupported file type: {target}"
    except PermissionError:
        return f"Error: Permission denied: {target}"
    except Exception as exc:  # noqa: BLE001
        return f"Error deleting: {exc}"


def list_directory(
    path: str,
    pattern: str | None = None,
    recursive: bool = False,
    show_hidden: bool = False,
) -> str:
    """List contents of a directory."""
    dir_path = Path(path).resolve()

    if not dir_path.exists():
        return f"Error: Directory not found: {dir_path}"
    if not dir_path.is_dir():
        return f"Error: Path is not a directory: {dir_path}"

    try:
        entries: list[str] = []
        count = 0

        iterator = dir_path.rglob(pattern or "*") if recursive else dir_path.iterdir()

        for entry in sorted(iterator, key=lambda e: (not e.is_dir(), e.name.lower())):
            if not show_hidden and entry.name.startswith("."):
                continue
            if pattern and not recursive and not fnmatch.fnmatch(entry.name, pattern):
                continue

            count += 1
            if count > _MAX_RESULTS:
                entries.append(f"... (truncated, {_MAX_RESULTS}+ items)")
                break

            try:
                size = entry.stat().st_size if entry.is_file() else 0
            except OSError:
                size = 0

            rel = str(entry.relative_to(dir_path)) if recursive else entry.name
            entries.append(
                _DirEntry(name=entry.name, is_dir=entry.is_dir(), size=size).to_string(rel)
            )

        if not entries:
            filter_msg = f' matching "{pattern}"' if pattern else ""
            return f"Directory {dir_path} is empty{filter_msg}."

        header = f"Directory: {dir_path}"
        if pattern:
            header += f" (filter: {pattern})"
        return header + "\n" + "\n".join(entries)
    except PermissionError:
        return f"Error: Permission denied: {dir_path}"
    except Exception as exc:  # noqa: BLE001
        return f"Error listing directory: {exc}"


def search_files(path: str, pattern: str, recursive: bool = True) -> str:
    """Search for files matching a glob pattern under *path*."""
    search_root = Path(path).resolve()

    if not search_root.exists():
        return f"Error: Search path not found: {search_root}"
    if not search_root.is_dir():
        return f"Error: Search path is not a directory: {search_root}"

    try:
        results: list[str] = []
        count = 0

        iterator = search_root.rglob(pattern) if recursive else search_root.glob(pattern)

        for match in sorted(iterator, key=lambda e: e.name.lower()):
            count += 1
            if count > _MAX_RESULTS:
                results.append(f"... (truncated, {_MAX_RESULTS}+ matches)")
                break
            try:
                size = match.stat().st_size if match.is_file() else 0
            except OSError:
                size = 0
            rel = str(match.relative_to(search_root))
            results.append(
                _DirEntry(name=match.name, is_dir=match.is_dir(), size=size).to_string(rel)
            )

        if not results:
            return f'No matches found for "{pattern}" in {search_root}'
        return (
            f'Search: "{pattern}" in {search_root} ({min(count, _MAX_RESULTS)} matches)\n'
            + "\n".join(results)
        )
    except PermissionError:
        return f"Error: Permission denied: {search_root}"
    except Exception as exc:  # noqa: BLE001
        return f"Error searching: {exc}"


def get_file_info(path: str) -> str:
    """Get detailed metadata about a file or directory."""
    target = Path(path).resolve()

    if not target.exists():
        return f"Error: Path not found: {target}"

    try:
        stat = target.stat()
        if target.is_dir():
            file_type = "Directory"
        elif target.is_symlink():
            file_type = "Symlink"
        elif target.is_file():
            file_type = "File"
        else:
            file_type = "Other"

        info = _FileInfo(
            path=str(target),
            type=file_type,
            size=stat.st_size,
            created=datetime.fromtimestamp(stat.st_ctime),
            modified=datetime.fromtimestamp(stat.st_mtime),
            accessed=datetime.fromtimestamp(stat.st_atime),
            read_only=not os.access(target, os.W_OK),
        )

        if target.is_dir():
            try:
                items = list(target.iterdir())
                info.contents_dirs = sum(1 for i in items if i.is_dir())
                info.contents_files = sum(1 for i in items if i.is_file())
            except PermissionError:
                pass

        if target.is_file():
            info.extension = target.suffix or "(none)"

        if target.is_symlink():
            info.link_target = str(os.readlink(target))

        return info.to_string()
    except PermissionError:
        return f"Error: Permission denied: {target}"
    except Exception as exc:  # noqa: BLE001
        return f"Error getting file info: {exc}"
