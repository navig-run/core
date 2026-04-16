"""
File History — per-session file snapshots taken before every AI-driven edit.

Ported and adapted from Claude Code's TypeScript ``utils/fileHistory.ts``.

Before any agent-driven ``file_write`` / ``file_edit`` operation, calling
``checkpoint()`` atomically copies the current file to a versioned backup in
``~/.navig/file-cache/<session_id>/<turn_id>/``.  Up to
``file_history.max_snapshots_per_session`` snapshots are kept per session;
older ones are evicted when the cap is exceeded.

This allows the ``navig snapshot diff`` and ``navig snapshot restore``
subcommands to show what changed or roll back to any prior state.

Usage::

    from navig.file_history import get_file_history_store

    store = get_file_history_store()
    version_path = store.checkpoint("/var/www/app/config.php", session_id, turn_id)
    # ... agent makes edits ...
    versions = store.list_versions("/var/www/app/config.php", session_id)
    diff = store.diff_versions(versions[-2], versions[-1])
    store.restore(versions[-2])
"""

from __future__ import annotations

import difflib
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navig.platform.paths import config_dir

logger = logging.getLogger("navig.file_history")

# ── Module-level constants ────────────────────────────────────────────────────
_DEFAULT_MAX_SNAPSHOTS: int = 100
_DEFAULT_CACHE_DIR_NAME = "file-cache"   # inside ~/.navig/
_BACKUP_SUFFIX = ".bak"


@dataclass(frozen=True)
class FileVersion:
    """Metadata for one stored snapshot of a file."""

    original_path: str      # Absolute path of the original file
    backup_path: Path       # Where the snapshot lives
    session_id: str
    turn_id: str
    captured_at: datetime
    size_bytes: int

    def __lt__(self, other: "FileVersion") -> bool:
        return self.captured_at < other.captured_at


class FileHistoryStore:
    """Manages versioned file snapshots for one NAVIG process."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        max_snapshots: int = _DEFAULT_MAX_SNAPSHOTS,
    ) -> None:
        self._cache_dir = cache_dir
        self._max_snapshots = max_snapshots

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def checkpoint(
        self,
        filepath: str | Path,
        session_id: str,
        turn_id: str,
    ) -> Path | None:
        """Snapshot *filepath* before it is modified.

        Returns the path of the backup file, or ``None`` when:
        - the file does not exist (nothing to back up)
        - file_history is disabled in config
        - an I/O error occurs (logged at DEBUG, never raises)
        """
        if not self._is_enabled():
            return None
        src = Path(filepath)
        if not src.exists():
            logger.debug("file_history.checkpoint: %s does not exist — skipping", src)
            return None

        dest_dir = self._snapshot_dir(session_id, turn_id)
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            safe_name = src.name + _BACKUP_SUFFIX
            dest = dest_dir / safe_name
            shutil.copy2(str(src), str(dest))
            logger.debug(
                "file_history.checkpoint: %s → %s (%d bytes)",
                src,
                dest,
                dest.stat().st_size,
            )
            self._evict_old_snapshots(session_id)
            return dest
        except OSError as exc:
            logger.debug("file_history.checkpoint failed: %s", exc)
            return None

    def list_versions(
        self, filepath: str | Path, session_id: str
    ) -> list[FileVersion]:
        """Return all stored versions of *filepath* for *session_id*, oldest first."""
        src = Path(filepath)
        base = self._session_cache_dir(session_id)
        if not base.exists():
            return []

        versions: list[FileVersion] = []
        safe_name = src.name + _BACKUP_SUFFIX

        for turn_dir in sorted(base.iterdir()):
            if not turn_dir.is_dir():
                continue
            candidate = turn_dir / safe_name
            if not candidate.exists():
                continue
            stat = candidate.stat()
            versions.append(
                FileVersion(
                    original_path=str(src),
                    backup_path=candidate,
                    session_id=session_id,
                    turn_id=turn_dir.name,
                    captured_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    size_bytes=stat.st_size,
                )
            )
        return sorted(versions)

    def restore(self, version: FileVersion) -> bool:
        """Restore *original_path* from the given *version*.

        Returns ``True`` on success, ``False`` on error.
        """
        if not version.backup_path.exists():
            logger.warning("file_history.restore: backup %s not found", version.backup_path)
            return False
        try:
            shutil.copy2(str(version.backup_path), version.original_path)
            logger.info(
                "file_history.restore: %s → %s",
                version.backup_path,
                version.original_path,
            )
            return True
        except OSError as exc:
            logger.warning("file_history.restore failed: %s", exc)
            return False

    def diff_versions(self, v1: FileVersion, v2: FileVersion) -> str:
        """Return a unified diff between two versions (or one version and the live file)."""
        try:
            text1 = v1.backup_path.read_text(encoding="utf-8", errors="replace")
            # If v2's backup exists, compare snapshots; otherwise compare to live file
            if v2.backup_path.exists():
                text2 = v2.backup_path.read_text(encoding="utf-8", errors="replace")
                from_label = f"{v1.original_path!r} @ {v1.captured_at.strftime('%H:%M:%S')}"
                to_label = f"{v2.original_path!r} @ {v2.captured_at.strftime('%H:%M:%S')}"
            else:
                live = Path(v2.original_path)
                text2 = live.read_text(encoding="utf-8", errors="replace") if live.exists() else ""
                from_label = str(v1.backup_path)
                to_label = f"{v2.original_path!r} (live)"

            diff = list(
                difflib.unified_diff(
                    text1.splitlines(keepends=True),
                    text2.splitlines(keepends=True),
                    fromfile=from_label,
                    tofile=to_label,
                )
            )
            return "".join(diff) if diff else "(no differences)"
        except OSError as exc:
            return f"(diff unavailable: {exc})"

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_enabled(self) -> bool:
        try:
            from navig.config import get_config_manager
            cfg = get_config_manager()
            return bool(cfg.get("file_history.enabled", False))
        except Exception:  # noqa: BLE001
            return False

    def _resolve_cache_root(self) -> Path:
        if self._cache_dir is not None:
            return self._cache_dir
        try:
            from navig.config import get_config_manager
            cfg = get_config_manager()
            custom = cfg.get("file_history.cache_dir")
            if custom:
                p = Path(str(custom)).expanduser()
                p.mkdir(parents=True, exist_ok=True)
                return p
        except Exception:  # noqa: BLE001
            pass
        root = config_dir() / _DEFAULT_CACHE_DIR_NAME
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _session_cache_dir(self, session_id: str) -> Path:
        safe = session_id.replace(":", "_").replace("/", "_")
        return self._resolve_cache_root() / safe

    def _snapshot_dir(self, session_id: str, turn_id: str) -> Path:
        safe_turn = str(turn_id).replace(":", "_").replace("/", "_")
        return self._session_cache_dir(session_id) / safe_turn

    def _evict_old_snapshots(self, session_id: str) -> None:
        """Remove the oldest turn-directories when cap is exceeded."""
        try:
            # Use the instance's max_snapshots directly (set at construction time,
            # already resolved from config by get_file_history_store()).
            max_snaps = self._max_snapshots

            base = self._session_cache_dir(session_id)
            if not base.exists():
                return
            turn_dirs = sorted(d for d in base.iterdir() if d.is_dir())
            excess = max(0, len(turn_dirs) - max_snaps)
            for old_dir in turn_dirs[:excess]:
                shutil.rmtree(old_dir, ignore_errors=True)
                logger.debug("file_history: evicted %s", old_dir)
        except Exception as exc:  # noqa: BLE001
            logger.debug("file_history._evict_old_snapshots: %s", exc)


# ── Process-wide singleton ────────────────────────────────────────────────────

_store: FileHistoryStore | None = None


def get_file_history_store() -> FileHistoryStore:
    """Return the process-wide ``FileHistoryStore``.

    Config is resolved lazily on first access.
    """
    global _store
    if _store is None:
        _store = FileHistoryStore()
    return _store
