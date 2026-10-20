"""Changelog adapter — ONE-OFF migration of legacy changelog files into Recall.

Not in ``ADAPTERS``: ``navig recall ingest`` never discovers it, so the auto-ingest
loop can never re-read or re-write these sources. The only entry point is
``Ingestor.import_changelog`` (the ``migrate`` command), which is idempotent on an
unchanged file (``head_sha`` short-circuit, content-addressed record ids).

Classification decides fate: ``private_dev`` / ``sample_artifact`` entries may be
retired (with backup, hash and an empty consumer scan); ``release_notes`` are indexed
but the file stays — published release documentation is not a legacy database.
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


def find_consumers(root: Path, target: Path) -> list[str]:
    """Files that could actually OPEN *target* — the migration refuses to retire while
    any exist. Bounded walk: depth ≤ 5, known source/doc extensions, 1 MB per file.

    Matching is PATH-AWARE, not bare-basename: a nested file is referenced by a tail of
    its path (``navig-forge/changelog.md``), or by the bare name only from a file in the
    SAME directory (relative links resolve that way). The old bare-substring rule
    counted 49 consumers for a nested sample — 48 of them mentions of
    ``core/CHANGELOG.md`` instead (pilot, 2026-10-20). A repo-root target keeps the
    old behavior: its bare basename IS its full identity.
    """
    root = Path(root)
    target = Path(target)
    try:
        parts = target.resolve().relative_to(root.resolve()).parts
    except (OSError, ValueError):
        parts = (target.name,)
    base = parts[-1].casefold()
    if len(parts) == 1:
        tails = [base]  # root-level file: the bare name is its full identity
    else:
        # ≥2 trailing segments only — never the bare name across directories
        tails = ["/".join(parts[i:]).casefold()
                 for i in range(max(0, len(parts) - 3), len(parts) - 1)]
    try:
        same_dir = target.resolve().parent
    except OSError:
        same_dir = Path(".")
    # the scanner describing its own example path in a docstring is not a consumer
    own_file = Path(__file__).resolve()
    hits: list[str] = []
    for dirpath, dirs, files in os.walk(root):
        rel_parts = Path(dirpath).relative_to(root).parts
        # `.navig/history/**` is the runtime operation log — it ECHOES command paths
        # (including this file's) and is never a document that depends on the file.
        if ".navig" in rel_parts and rel_parts[rel_parts.index(".navig") + 1:][:1] == ("history",):
            dirs.clear()
            continue
        if len(rel_parts) >= _MAX_DEPTH:
            dirs.clear()
            continue
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            p = Path(dirpath) / name
            try:
                if p.resolve() == own_file:
                    continue
            except OSError:
                pass
            if name.casefold() == base:
                if len(parts) == 1:
                    continue  # root target: same-named files are never its consumers
                # nested target: skip the file itself and any other same-named file
                # (another CHANGELOG.md elsewhere is not this one)
                try:
                    if p.resolve().parent == same_dir:
                        continue  # the target itself
                except OSError:
                    continue
                continue
            if Path(name).suffix.lower() not in _SCAN_EXTS:
                continue
            try:
                if p.stat().st_size > _MAX_SCAN_BYTES:
                    continue
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            low = text.casefold()
            same = False
            try:
                same = p.resolve().parent == same_dir
            except OSError:
                pass
            if any(t in low for t in tails) or (same and name.casefold() != base and base in low):
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
