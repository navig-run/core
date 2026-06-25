"""
navig.inbox.retention — never-delete safety for the inbox.

Two guarantees:

* :func:`preserve_original` keeps a canonical, content-addressed copy of every
  binary that enters the inbox under ``.navig/wiki/_originals/`` — so when a
  binary is routed as extracted markdown (searchable text), the bytes are never
  lost.
* :func:`archive` is the *only* sanctioned way to "remove" an inbox item: it
  **moves** the file into a dated archive folder with an ``.archived`` sidecar.
  Nothing in the inbox path ever calls ``os.remove`` on a user file.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("navig.inbox.retention")

_ORIGINALS_REL = ".navig/wiki/_originals"
_ARCHIVE_REL = ".navig/wiki/archive/_inbox"


def _sha8(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


def preserve_original(source: Path, project_root: Path) -> str:
    """Copy *source* into ``.navig/wiki/_originals/<sha8>-<name>`` (idempotent).

    Returns the project-relative path string (for routed-doc frontmatter), or ""
    on failure (preservation is best-effort and must never block routing).
    """
    try:
        raw = source.read_bytes()
        sha8 = _sha8(raw)
        dest_dir = project_root / _ORIGINALS_REL
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{sha8}-{source.name}"
        if not dest.exists():
            shutil.copy2(str(source), str(dest))
        return f"{_ORIGINALS_REL}/{dest.name}"
    except Exception as exc:  # noqa: BLE001
        logger.debug("preserve_original(%s) failed: %s", source, exc)
        return ""


def archive(source: Path, project_root: Path, reason: str = "") -> Path | None:
    """Move *source* into a dated archive folder (never delete). Returns the dest path."""
    try:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dest_dir = project_root / _ARCHIVE_REL / day
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / source.name
        if dest.exists():
            sha8 = _sha8(source.read_bytes()) if source.exists() else "dup"
            dest = dest_dir / f"{dest.stem}-{sha8}{dest.suffix}"
        shutil.move(str(source), str(dest))
        try:
            sidecar = dest.with_suffix(dest.suffix + ".archived")
            sidecar.write_text(
                f"from: {source}\nreason: {reason}\narchived_at: {datetime.now(timezone.utc).isoformat()}\n",
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass
        return dest
    except Exception as exc:  # noqa: BLE001
        logger.debug("archive(%s) failed: %s", source, exc)
        return None
