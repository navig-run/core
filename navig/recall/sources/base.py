"""Shared adapter types and the incremental, partial-line-safe JSONL reader."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navig.recall.model import Turn

HEAD_BYTES = 64 * 1024
TAIL_BYTES = 4 * 1024


@dataclass
class SourceRef:
    source_id: str
    adapter: str
    path: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    turns: list[Turn]
    end_offset: int
    title: str = ""
    cwd: str = ""
    first_ts: str = ""
    last_ts: str = ""
    bad_lines: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class FileState:
    size: int
    mtime: float


def file_state(path: Path) -> FileState:
    st = path.stat()
    return FileState(size=st.st_size, mtime=st.st_mtime)


def head_len(cursor: int) -> int:
    """How many leading bytes the head fingerprint covers: only bytes already read, so an
    append to a small file never looks like a rewrite."""
    return max(0, min(HEAD_BYTES, cursor))


def head_sha(path: Path, length: int) -> str:
    if length <= 0:
        return ""
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read(length)).hexdigest()[:16]


def tail_sha(path: Path, cursor: int) -> str:
    """Hash of the bytes just before *cursor* — detects a rewrite of already-read content."""
    if cursor <= 0:
        return ""
    start = max(0, cursor - TAIL_BYTES)
    with open(path, "rb") as fh:
        fh.seek(start)
        data = fh.read(cursor - start)
    return hashlib.sha256(data).hexdigest()[:16]


def iter_jsonl(path: Path, start: int = 0) -> Iterator[tuple[int, int, dict[str, Any] | None]]:
    """Yield ``(line_offset, next_offset, record_or_None)`` for COMPLETE lines from *start*.

    A trailing line without ``\\n`` is a write in progress: it is not yielded, and the
    caller's cursor stays before it. A line that is not valid JSON yields ``None`` (counted,
    never silently dropped).
    """
    with open(path, "rb") as fh:
        fh.seek(start)
        offset = start
        for raw in fh:
            if not raw.endswith(b"\n"):
                return
            nxt = offset + len(raw)
            line = raw.strip()
            if line:
                try:
                    rec = json.loads(line.decode("utf-8", errors="replace"))
                    yield offset, nxt, rec if isinstance(rec, dict) else None
                except ValueError:
                    yield offset, nxt, None
            offset = nxt


def real_key(path: Path) -> str:
    """Identity of a file across junctions/symlinks (the audit found 15 junction-duplicate dirs)."""
    try:
        return os.path.normcase(os.path.realpath(path))
    except OSError:
        return os.path.normcase(str(path))
