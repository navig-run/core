"""
navig.plans.review_queue — Manage ``.md.review`` items awaiting human decision.

Items land in ``.md.review`` state when the reconciliation pipeline cannot
make a confident decision (duplicates, conflicts, pipeline exceptions).

Human operators use this module to:

* List items in review.
* Inspect individual items.
* Commit an item (triggers re-reconciliation; if it fails again → stays review).
* Archive an item (renames ``.md.review`` → ``.md.archive``).
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\n([\s\S]*?)\n---\n?", re.MULTILINE)


@dataclass(frozen=True)
class ReviewItem:
    """A single item in the review queue."""

    path: Path
    """Absolute path to the ``.md.review`` file."""

    name: str
    """Canonical base name (e.g. ``my_task.md``)."""

    title: str
    """Title from frontmatter, or derived from filename."""

    frontmatter: dict[str, str]
    """Parsed frontmatter key/values."""

    body: str
    """Content after frontmatter."""

    reason: str
    """Why the item was routed to review (from frontmatter or empty)."""


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse ``---``-delimited frontmatter, return (dict, body)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        values[key.strip()] = val.strip()
    return values, text[match.end():]


def _canonical_name(filename: str) -> str:
    """Strip lifecycle suffixes from a filename."""
    lower = filename.lower()
    for suffix in (".done", ".archive", ".review"):
        if lower.endswith(suffix):
            return filename[: -len(suffix)]
    return filename


class ReviewQueue:
    """Manage items in ``.md.review`` state across plans directories.

    Scans:
    - ``.navig/inbox/`` — review items from inbox processing
    - ``.navig/plans/tasks/review/`` — review items from task routing

    Parameters
    ----------
    root:
        Project root directory containing ``.navig/``.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._navig_dir = self._root / ".navig"

    def _review_dirs(self) -> list[Path]:
        """Return directories to scan for ``.md.review`` files."""
        candidates = [
            self._navig_dir / "inbox",
            self._navig_dir / "plans" / "tasks" / "review",
        ]
        return [d for d in candidates if d.is_dir()]

    def list_items(self) -> list[ReviewItem]:
        """Return all items currently in review state.

        Returns
        -------
        list[ReviewItem]
            Sorted by filename for deterministic order.
        """
        items: list[ReviewItem] = []
        for directory in self._review_dirs():
            for entry in sorted(directory.iterdir()):
                if not entry.is_file():
                    continue
                if not entry.name.lower().endswith(".md.review"):
                    continue
                item = self._read_review_item(entry)
                if item is not None:
                    items.append(item)
        return items

    def get_item_detail(self, filename: str) -> ReviewItem | None:
        """Get a single review item by filename.

        Searches all review directories for the named file.
        """
        for directory in self._review_dirs():
            path = directory / filename
            if path.is_file():
                return self._read_review_item(path)
        return None

    def commit_item(self, filename: str) -> bool:
        """Re-reconcile a review item. On failure it stays ``.md.review``.

        The item is renamed from ``.md.review`` → ``.md`` (active) and
        returned to the inbox for re-processing.  If the rename fails,
        the item stays in review.

        Parameters
        ----------
        filename:
            Name of the ``.md.review`` file.

        Returns
        -------
        bool
            ``True`` if the item was successfully committed back to active.
        """
        for directory in self._review_dirs():
            source = directory / filename
            if not source.is_file():
                continue

            base = _canonical_name(filename)
            target = self._navig_dir / "inbox" / base

            try:
                # Move back to inbox as active .md
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                logger.debug("Committed review item %s → %s", source, target)
                return True
            except OSError:
                logger.exception("Failed to commit review item %s", filename)
                return False

        logger.debug("Review item %s not found in any review directory", filename)
        return False

    def archive_item(self, filename: str) -> bool:
        """Archive a review item: rename ``.md.review`` → ``.md.archive``.

        Parameters
        ----------
        filename:
            Name of the ``.md.review`` file.

        Returns
        -------
        bool
            ``True`` if successfully archived.
        """
        for directory in self._review_dirs():
            source = directory / filename
            if not source.is_file():
                continue

            archive_name = filename.replace(".md.review", ".md.archive")
            target = source.parent / archive_name

            try:
                source.rename(target)
                logger.debug("Archived review item %s → %s", source, target)
                return True
            except OSError:
                logger.exception("Failed to archive review item %s", filename)
                return False

        logger.debug("Review item %s not found", filename)
        return False

    def _read_review_item(self, path: Path) -> ReviewItem | None:
        """Parse a single ``.md.review`` file into a ReviewItem."""
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return None

        frontmatter, body = _parse_frontmatter(content)

        title = frontmatter.get("title", "")
        if not title:
            title = (
                _canonical_name(path.name)
                .replace(".md", "")
                .replace("_", " ")
                .replace("-", " ")
            )

        return ReviewItem(
            path=path.resolve(),
            name=_canonical_name(path.name),
            title=title,
            frontmatter=frontmatter,
            body=body,
            reason=frontmatter.get("review_reason", ""),
        )
