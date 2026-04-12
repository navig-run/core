"""
navig.plans.milestone_progress — Parse milestones and render progress strips.

Milestones live in ``.navig/plans/milestones/`` as markdown files with
frontmatter containing ``title``, ``status``, ``target_date``, and a
body listing task checkboxes.

Progress is computed from checkbox syntax:

- ``[x]`` or ``[X]`` → completed task
- ``[ ]`` → pending task

Rendering uses a visual strip:

- ``✓`` = completed
- ``●`` = in-progress (the first pending task)
- ``⚠`` = blocked (from frontmatter ``status: blocked``)
- ``○`` = pending
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from navig.plans.frontmatter import parse_frontmatter as _parse_frontmatter

logger = logging.getLogger(__name__)

_CHECKBOX_DONE_RE = re.compile(r"^\s*[-*]\s*\[(?:x|X)\]", re.MULTILINE)
_CHECKBOX_TODO_RE = re.compile(r"^\s*[-*]\s*\[\s\]", re.MULTILINE)


@dataclass
class MilestoneState:
    """Parsed state of a single milestone file."""

    name: str
    """Filename-derived milestone identifier (e.g. ``MVP1``)."""

    title: str
    """Human-readable title from frontmatter."""

    status: str
    """Current status: ``active``, ``blocked``, ``completed``."""

    target_date: str
    """Target completion date (ISO string or empty)."""

    done_count: int
    """Number of completed checkboxes."""

    total_count: int
    """Total number of checkboxes."""

    source_path: Path
    """Absolute path to the milestone file."""

    @property
    def progress_pct(self) -> float:
        """Completion percentage (0.0–100.0)."""
        if self.total_count == 0:
            return 0.0
        return round((self.done_count / self.total_count) * 100, 1)


def _count_checkboxes(text: str) -> tuple[int, int]:
    """Count (done, total) checkboxes in markdown text.

    Uses a single regex pass per pattern — no YAML library needed.
    """
    done = len(_CHECKBOX_DONE_RE.findall(text))
    todo = len(_CHECKBOX_TODO_RE.findall(text))
    return done, done + todo


class MilestoneProgressEngine:
    """Parse milestones from ``.navig/plans/milestones/`` and render progress.

    Parameters
    ----------
    root:
        Project root directory containing ``.navig/``.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._milestones_dir = self._root / ".navig" / "plans" / "milestones"

    def list_milestones(self) -> list[MilestoneState]:
        """Return all milestones sorted by filename.

        Returns
        -------
        list[MilestoneState]
            Parsed milestones, or empty list if directory missing.
        """
        if not self._milestones_dir.is_dir():
            return []

        milestones: list[MilestoneState] = []
        for entry in sorted(self._milestones_dir.iterdir()):
            if not entry.is_file():
                continue
            if not entry.name.lower().endswith(".md"):
                continue

            ms = self._parse_milestone(entry)
            if ms is not None:
                milestones.append(ms)

        return milestones

    def get_milestone(self, name: str) -> MilestoneState | None:
        """Get a single milestone by name (filename stem).

        Parameters
        ----------
        name:
            Milestone name, e.g. ``MVP1``.  Will try ``{name}.md``.
        """
        if self._milestones_dir.is_dir():
            # Iterate to get the actual filename casing (Windows-safe)
            for entry in self._milestones_dir.iterdir():
                if entry.stem.lower() == name.lower() and entry.suffix == ".md":
                    return self._parse_milestone(entry)
        return None

    def render_strip(self, milestone: MilestoneState, *, width: int = 20) -> str:
        """Render a visual progress strip for a milestone.

        Format::

            ✓✓✓✓●○○○○○  MVP1 (40.0%)

        Symbols:
        - ``✓`` = completed
        - ``●`` = in-progress (first pending)
        - ``⚠`` = blocked
        - ``○`` = pending

        Parameters
        ----------
        milestone:
            The milestone to render.
        width:
            Number of characters in the progress bar (default 20).

        Returns
        -------
        str
            Formatted progress strip.
        """
        if milestone.total_count == 0:
            bar = "○" * width
            return f"{bar}  {milestone.name} (no tasks)"

        done_slots = int((milestone.done_count / milestone.total_count) * width)
        remaining = width - done_slots

        if milestone.status == "blocked":
            # Show completed + blocked marker + remaining
            bar = "✓" * done_slots + "⚠" + "○" * max(0, remaining - 1)
        elif done_slots < width:
            # Show completed + current-in-progress + remaining
            bar = "✓" * done_slots + "●" + "○" * max(0, remaining - 1)
        else:
            bar = "✓" * width

        return f"{bar}  {milestone.name} ({milestone.progress_pct}%)"

    def render_all(self, *, width: int = 20) -> str:
        """Render progress strips for all milestones.

        Returns
        -------
        str
            Multi-line string with one strip per milestone.
        """
        milestones = self.list_milestones()
        if not milestones:
            return "No milestones found."

        lines: list[str] = []
        for ms in milestones:
            lines.append(self.render_strip(ms, width=width))
        return "\n".join(lines)

    def _parse_milestone(self, path: Path) -> MilestoneState | None:
        """Parse a single milestone file."""
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            logger.debug("Failed to read milestone %s", path)
            return None

        fm = _parse_frontmatter(content)
        done, total = _count_checkboxes(content)

        name = path.stem
        title = fm.get("title", name)
        status = fm.get("status", "active")
        target_date = fm.get("target_date", fm.get("target", ""))

        return MilestoneState(
            name=name,
            title=title,
            status=status,
            target_date=target_date,
            done_count=done,
            total_count=total,
            source_path=path.resolve(),
        )
