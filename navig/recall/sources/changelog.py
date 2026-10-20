"""Changelog adapter — ONE-OFF migration of legacy changelog files into Recall.

Not in ``ADAPTERS``: ``navig recall ingest`` never discovers it, so the auto-ingest
loop can never re-read or re-write these sources. The only entry point is
``Ingestor.import_changelog`` (the ``migrate`` command), which is idempotent on an
unchanged file (``head_sha`` short-circuit, content-addressed record ids).

Classification decides fate: ``private_dev`` entries may be retired (with backup,
hash and an empty consumer scan); ``release_notes`` are indexed but the file stays —
published release documentation is not a legacy database.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

ADAPTER = "changelog"
_SCAN_EXTS = {".md", ".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yml", ".yaml",
              ".toml", ".ps1", ".txt", ".rs", ".sh"}
_SKIP_DIRS = {".git", "node_modules", "dist", "build", "target", "__pycache__", ".venv",
              "venv", ".dev", ".backup", ".lab"}
_MAX_DEPTH = 5
_MAX_SCAN_BYTES = 1_000_000


def source_id_for(project: str, filename: str) -> str:
    return f"{ADAPTER}:{project}:{filename}"


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def find_consumers(root: Path, filename: str) -> list[str]:
    """Files (other than *filename* itself) inside *root* that mention its name —
    the migration refuses to retire while any consumer exists. Bounded walk:
    depth ≤ 5, known source/doc extensions, 1 MB per file."""
    want = filename.casefold()
    hits: list[str] = []
    root = Path(root)
    for dirpath, dirs, files in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        if len(rel.parts) >= _MAX_DEPTH:
            dirs.clear()
            continue
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if name.casefold() == want.casefold():
                continue
            if Path(name).suffix.lower() not in _SCAN_EXTS:
                continue
            p = Path(dirpath) / name
            try:
                if p.stat().st_size > _MAX_SCAN_BYTES:
                    continue
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if want in text.casefold():
                hits.append(str(p.relative_to(root)))
                if len(hits) >= 50:
                    return hits
    return hits


def source_row(project: str, path: Path, digest: str, entries: int, meta: dict[str, Any]) -> dict[str, Any]:
    st = path.stat()
    return {
        "source_id": source_id_for(project, path.name), "adapter": ADAPTER, "path": str(path),
        "project": project, "title": f"Changelog {path.name}", "cwd": "", "size": st.st_size,
        "mtime": st.st_mtime, "head_sha": digest, "tail_sha": "", "cursor": 0, "status": "ok",
        "error": "", "first_ts": "", "last_ts": "", "turns": entries, "meta": meta,
        "updated_at": "",
    }
