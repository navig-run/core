"""
navig.plans.current_phase_manager — Reads and mutates ``CURRENT_PHASE.md``.

Responsible for:

1. Parsing the frontmatter + ``## Active Tasks`` section of
   ``CURRENT_PHASE.md``.
2. Advancing to the next phase (atomic rename → write).
3. Blocking the current phase with a reason.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from navig.core.yaml_io import atomic_write_text as _atomic_write_text
from navig.plans.frontmatter import (
    FRONTMATTER_RE as _FRONTMATTER_RE,
)
from navig.plans.frontmatter import (
    parse_frontmatter as _parse_frontmatter,
)
from navig.plans.frontmatter import (
    render_frontmatter as _render_frontmatter,
)

logger = logging.getLogger(__name__)

_ACTIVE_TASKS_RE = re.compile(
    r"^## Active Tasks\s*\n([\s\S]*?)(?=\n## |\Z)",
    re.MULTILINE,
)


@dataclass
class PhaseState:
    """Parsed state of ``CURRENT_PHASE.md``."""

    phase: str
    """Phase identifier (e.g. ``01``, ``02``)."""

    title: str
    """Human-readable phase title."""

    started: str
    """ISO date string when the phase started."""

    milestone: str
    """Milestone key this phase belongs to (e.g. ``MVP1``)."""

    status: str
    """Current status: ``active``, ``blocked``, ``completed``."""

    blocked_by: str
    """Blocking reason, or ``~`` / empty when unblocked."""

    active_tasks: list[str]
    """Bullet-list entries from the ``## Active Tasks`` section."""

    raw_content: str
    """Full original file content."""

    source_path: Path
    """Absolute path to the source file."""


def _parse_active_tasks(text: str) -> list[str]:
    """Extract bullet items from the ``## Active Tasks`` heading."""
    match = _ACTIVE_TASKS_RE.search(text)
    if not match:
        return []
    tasks: list[str] = []
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            tasks.append(stripped[2:].strip())
    return tasks


class CurrentPhaseManager:
    """Read and mutate ``CURRENT_PHASE.md`` in ``.navig/plans/phases/``.

    Falls back to ``.navig/CURRENT_PHASE.md`` if the phases subdirectory
    does not contain the file.

    Parameters
    ----------
    root:
        Project root directory containing ``.navig/``.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._navig_dir = self._root / ".navig"
        self._phases_dir = self._navig_dir / "plans" / "phases"

    def _locate_phase_file(self) -> Path | None:
        """Find ``CURRENT_PHASE.md`` in canonical locations."""
        primary = self._phases_dir / "CURRENT_PHASE.md"
        if primary.is_file():
            return primary
        fallback = self._navig_dir / "CURRENT_PHASE.md"
        if fallback.is_file():
            return fallback
        return None

    def get_current_phase(self) -> PhaseState | None:
        """Parse ``CURRENT_PHASE.md`` and return phase state.

        Returns
        -------
        PhaseState | None
            Parsed state, or ``None`` if file is missing or unreadable.
        """
        path = self._locate_phase_file()
        if path is None:
            return None

        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            logger.debug("Failed to read CURRENT_PHASE.md at %s", path)
            return None

        fm = _parse_frontmatter(content)
        tasks = _parse_active_tasks(content)

        return PhaseState(
            phase=fm.get("phase", ""),
            title=fm.get("title", ""),
            started=fm.get("started", ""),
            milestone=fm.get("milestone", ""),
            status=fm.get("status", "active"),
            blocked_by=fm.get("blocked_by", "~"),
            active_tasks=tasks,
            raw_content=content,
            source_path=path.resolve(),
        )

    def advance_phase(
        self,
        next_phase_file: Path,
        *,
        archive_current: bool = True,
    ) -> PhaseState | None:
        """Replace ``CURRENT_PHASE.md`` with *next_phase_file*.

        The operation is failure-safe: the old phase is backed up first,
        then the new file is copied in.  If the copy fails the backup is
        restored.

        Parameters
        ----------
        next_phase_file:
            Path to the new phase markdown file.
        archive_current:
            When ``True`` (default), rename the old phase to
            ``CURRENT_PHASE.md.archive`` before copying the new one.

        Returns
        -------
        PhaseState | None
            The newly parsed phase, or ``None`` on failure.
        """
        current_path = self._locate_phase_file()
        if current_path is None:
            # No existing phase — just copy in the new one.
            target = self._phases_dir / "CURRENT_PHASE.md"
            self._phases_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(next_phase_file), str(target))
            except OSError:
                logger.exception("Failed to install new phase file")
                return None
            return self.get_current_phase()

        backup = current_path.with_suffix(current_path.suffix + ".bak")

        try:
            # 1. Back up existing file
            shutil.copy2(str(current_path), str(backup))

            # 2. Copy in the new phase file
            shutil.copy2(str(next_phase_file), str(current_path))

            # 3. Mark old phase as archived (or remove backup)
            if archive_current:
                archive_path = current_path.parent / (
                    f"phase_{self._read_phase_id(backup)}.md.archive"
                )
                backup.rename(archive_path)
            else:
                backup.unlink(missing_ok=True)

        except OSError:
            logger.exception("Phase advance failed; restoring backup")
            # Attempt rollback
            if backup.is_file():
                try:
                    shutil.copy2(str(backup), str(current_path))
                except OSError:
                    logger.exception("Rollback also failed")
            return None

        return self.get_current_phase()

    def block_phase(self, reason: str) -> PhaseState | None:
        """Set ``status: blocked`` and ``blocked_by: <reason>`` in frontmatter.

        Parameters
        ----------
        reason:
            Human-readable reason for blocking.

        Returns
        -------
        PhaseState | None
            Updated phase state, or ``None`` if the file cannot be written.
        """
        return self._update_frontmatter_fields(
            {"status": "blocked", "blocked_by": reason}
        )

    def unblock_phase(self) -> PhaseState | None:
        """Remove the block, setting ``status: active`` and ``blocked_by: ~``."""
        return self._update_frontmatter_fields(
            {"status": "active", "blocked_by": "~"}
        )

    def complete_phase(self) -> PhaseState | None:
        """Mark current phase as completed."""
        return self._update_frontmatter_fields({"status": "completed"})

    # ── Private helpers ───────────────────────────────────────

    def _update_frontmatter_fields(
        self, updates: dict[str, str]
    ) -> PhaseState | None:
        """Rewrite frontmatter in ``CURRENT_PHASE.md`` with updated fields."""
        path = self._locate_phase_file()
        if path is None:
            return None

        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return None

        fm = _parse_frontmatter(content)
        fm.update(updates)

        # Rebuild the file: new frontmatter + everything after old frontmatter
        match = _FRONTMATTER_RE.match(content)
        body = content[match.end():] if match else content

        new_content = _render_frontmatter(fm) + body

        try:
            _atomic_write_text(path, new_content)
        except OSError:
            logger.exception("Failed to update CURRENT_PHASE.md")
            return None

        return self.get_current_phase()

    @staticmethod
    def _read_phase_id(path: Path) -> str:
        """Extract the ``phase`` value from a file's frontmatter."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return "unknown"
        fm = _parse_frontmatter(text)
        return fm.get("phase", "unknown")
