"""Script Library for AHK (AutoHotkey) automation."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from navig.platform.paths import config_dir


@dataclass
class ScriptEntry:
    """A stored AutoHotkey script with usage metadata."""

    id: str
    goal: str
    script: str
    created_at: str
    success_count: int = 0
    last_used: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class ScriptLibrary:
    """Persistent library of reusable AHK goal-to-script mappings.

    Scripts are keyed by a deterministic MD5 hash of the normalised goal
    string and stored on disk in two files:

    - ``<storage_dir>/index.json`` — metadata for all entries.
    - ``<storage_dir>/scripts/<id>.ahk`` — raw script text.

    All disk writes use atomic ``os.replace()`` to avoid partial writes on
    crash.
    """

    def __init__(self, storage_dir: Path | None = None) -> None:
        self.storage_dir = storage_dir or (config_dir() / "ahk_library")
        self.index_file = self.storage_dir / "index.json"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        (self.storage_dir / "scripts").mkdir(exist_ok=True)

        self._index: dict[str, ScriptEntry] = {}
        self._load_index()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_index(self) -> None:
        """Load the script index from disk, silently recovering from corruption."""
        if not self.index_file.exists():
            return
        try:
            raw = self.index_file.read_text(encoding="utf-8")
            data: dict = json.loads(raw)
            self._index = {
                k: ScriptEntry(**v) for k, v in data.items()
            }
        except Exception:
            # Corrupt or unreadable index — start fresh rather than crashing.
            self._index = {}

    def _save_index(self) -> None:
        """Atomically persist the current index to disk."""
        data = {k: v.to_dict() for k, v in self._index.items()}
        tmp_path: Path | None = None
        try:
            fd, tmp = tempfile.mkstemp(
                dir=self.index_file.parent, suffix=".tmp"
            )
            tmp_path = Path(tmp)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp_path, self.index_file)
            tmp_path = None  # ownership transferred to os.replace
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _goal_id(goal: str) -> str:
        """Compute the deterministic 8-character ID for *goal*."""
        return hashlib.md5(goal.lower().encode()).hexdigest()[:8]

    def save_script(
        self, goal: str, script: str, tags: list[str] | None = None
    ) -> str:
        """Save or overwrite a script entry.  Returns the script ID.

        The ID is derived from the normalised goal string so identical goals
        always map to the same slot.

        Args:
            goal:   Human-readable goal description.
            script: AHK script body.
            tags:   Optional list of classification tags.

        Returns:
            The 8-character script ID.
        """
        script_id = self._goal_id(goal)
        now = datetime.now().isoformat()

        entry = ScriptEntry(
            id=script_id,
            goal=goal,
            script=script,
            created_at=now,
            success_count=0,
            last_used=now,
            tags=tags or [],
        )

        # Atomically write the script file.
        script_path = self.storage_dir / "scripts" / f"{script_id}.ahk"
        tmp_path: Path | None = None
        try:
            fd, tmp = tempfile.mkstemp(
                dir=script_path.parent, suffix=".tmp"
            )
            tmp_path = Path(tmp)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(script)
            os.replace(tmp_path, script_path)
            tmp_path = None
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        self._index[script_id] = entry
        self._save_index()
        return script_id

    def find_script(self, goal: str) -> ScriptEntry | None:
        """Find a script by exact (case-insensitive) goal match.

        .. note::
            This uses an exact hash match.  For larger libraries consider
            fuzzy matching or semantic embeddings (tracked as NAVIG-TODO-AHK-001).
        """
        return self._index.get(self._goal_id(goal))

    def record_usage(self, script_id: str, success: bool) -> None:
        """Update usage statistics for *script_id*."""
        entry = self._index.get(script_id)
        if entry is None:
            return
        if success:
            entry.success_count += 1
        entry.last_used = datetime.now().isoformat()
        self._save_index()

    def list_scripts(self) -> list[ScriptEntry]:
        """Return all stored script entries."""
        return list(self._index.values())
