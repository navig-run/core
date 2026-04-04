"""
navig.plans.inbox_reader — Canonical reader for ``.navig/inbox/`` items.

All inbox reads target ``.navig/inbox/`` exclusively.  Zero code paths
read inbox items from ``.navig/plans/``.

Lifecycle state is encoded in the file suffix:

========== ============
Suffix     Meaning
========== ============
``.md``    Active / unprocessed
``.md.done``    Completed
``.md.archive`` Archived (rename to restore)
``.md.review``  Uncertain — awaiting human decision
========== ============
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# ── Public types ──────────────────────────────────────────────

SuffixState = Literal["active", "done", "archive", "review"]

_FRONTMATTER_RE = re.compile(r"^---\n([\s\S]*?)\n---\n?", re.MULTILINE)


@dataclass(frozen=True)
class InboxItem:
    """A single inbox file with parsed metadata."""

    path: Path
    """Absolute path to the file."""

    name: str
    """Filename without lifecycle suffixes (e.g. ``my_idea.md``)."""

    content: str
    """Full file text (UTF-8)."""

    frontmatter: dict[str, str]
    """Parsed YAML-like frontmatter key/value pairs."""

    body: str
    """Content after the frontmatter block."""

    suffix_state: SuffixState
    """Lifecycle state derived from the file suffix."""


def parse_suffix_state(filename: str) -> SuffixState:
    """Derive lifecycle state from a filename's suffix chain.

    Examples
    --------
    >>> parse_suffix_state("idea.md")
    'active'
    >>> parse_suffix_state("old_note.md.done")
    'done'
    >>> parse_suffix_state("uncertain.md.review")
    'review'
    """
    lower = filename.lower()
    if lower.endswith(".md.done"):
        return "done"
    if lower.endswith(".md.archive"):
        return "archive"
    if lower.endswith(".md.review"):
        return "review"
    return "active"


def canonical_name(filename: str) -> str:
    """Strip lifecycle suffixes, returning the base ``.md`` name.

    >>> canonical_name("my_idea.md.archive")
    'my_idea.md'
    >>> canonical_name("task.md")
    'task.md'
    """
    lower = filename.lower()
    for suffix in (".done", ".archive", ".review"):
        if lower.endswith(suffix):
            return filename[: -len(suffix)]
    return filename


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML-like ``---`` delimited frontmatter.

    Returns
    -------
    tuple[dict[str, str], str]
        (frontmatter_dict, body_after_frontmatter)
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    values: dict[str, str] = {}
    for raw in match.group(1).splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        values[key.strip()] = value.strip()

    return values, text[match.end():]


class InboxReader:
    """Read inbox items exclusively from ``.navig/inbox/``.

    Parameters
    ----------
    root:
        Project root directory containing ``.navig/``.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._inbox_dir = self._root / ".navig" / "inbox"

    @property
    def inbox_dir(self) -> Path:
        """Absolute path to the inbox directory."""
        return self._inbox_dir

    def scan(self, *, include_done: bool = False) -> list[InboxItem]:
        """Return all inbox items from ``.navig/inbox/``.

        Parameters
        ----------
        include_done:
            When ``False`` (default), skip ``.md.done`` files.

        Returns
        -------
        list[InboxItem]
            Sorted by filename for deterministic ordering.
        """
        if not self._inbox_dir.is_dir():
            return []

        items: list[InboxItem] = []
        for entry in sorted(self._inbox_dir.iterdir()):
            if entry.is_dir():
                continue
            # Only process markdown-family files
            if not self._is_inbox_file(entry.name):
                continue

            state = parse_suffix_state(entry.name)
            if state == "done" and not include_done:
                continue

            try:
                content = entry.read_text(encoding="utf-8")
            except OSError:
                continue

            frontmatter, body = _parse_frontmatter(content)

            items.append(
                InboxItem(
                    path=entry.resolve(),
                    name=canonical_name(entry.name),
                    content=content,
                    frontmatter=frontmatter,
                    body=body,
                    suffix_state=state,
                )
            )

        return items

    def read_item(self, filename: str) -> InboxItem | None:
        """Read a single inbox item by filename.

        Parameters
        ----------
        filename:
            Filename (with any lifecycle suffix) relative to ``inbox/``.

        Returns
        -------
        InboxItem | None
            The parsed item, or ``None`` if the file does not exist.
        """
        path = self._inbox_dir / filename
        if not path.is_file():
            return None

        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return None

        frontmatter, body = _parse_frontmatter(content)

        return InboxItem(
            path=path.resolve(),
            name=canonical_name(path.name),
            content=content,
            frontmatter=frontmatter,
            body=body,
            suffix_state=parse_suffix_state(path.name),
        )

    @staticmethod
    def _is_inbox_file(filename: str) -> bool:
        """Check whether a filename looks like an inbox markdown entry."""
        lower = filename.lower()
        return (
            lower.endswith(".md")
            or lower.endswith(".md.done")
            or lower.endswith(".md.archive")
            or lower.endswith(".md.review")
        )
