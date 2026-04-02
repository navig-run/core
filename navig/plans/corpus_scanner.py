"""
navig.plans.corpus_scanner — Full-corpus duplicate and conflict detection.

Extends the per-item checks in ``inbox_processor`` to scan across the
entire ``tasks/`` and ``decisions/`` directories.  Useful for periodic
hygiene runs and pre-commit validations.

Operates on the file system directly — no database, no embeddings.
Primary signal is substring matching on titles and key sentences.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from navig.plans.inbox_reader import (
    InboxItem,
    _parse_frontmatter,
    canonical_name,
    parse_suffix_state,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DuplicateMatch:
    """A pair of files detected as potential duplicates."""

    file_a: Path
    file_b: Path
    reason: str
    """Why these are considered duplicates."""


@dataclass(frozen=True)
class ConflictMatch:
    """A pair of files with contradicting assertions."""

    file_a: Path
    file_b: Path
    reason: str
    """Description of the detected conflict."""


def _read_as_item(path: Path) -> InboxItem | None:
    """Read a markdown file into an InboxItem for analysis."""
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


def _extract_title(item: InboxItem) -> str:
    """Get normalised title from frontmatter or filename."""
    title = item.frontmatter.get("title", "")
    if title:
        return title.lower().strip()
    return (
        canonical_name(item.path.name)
        .replace(".md", "")
        .replace("_", " ")
        .replace("-", " ")
        .lower()
        .strip()
    )


def _extract_sentences(text: str) -> list[str]:
    """Split text into sentence-like chunks for conflict analysis."""
    raw = re.split(r"[.!?\n]+", text)
    return [s.strip().lower() for s in raw if len(s.strip()) > 10]


_NEGATION_PREFIXES = ("not ", "no ", "never ", "don't ", "shouldn't ", "cannot ")


def _is_contradiction(a: str, b: str) -> bool:
    """Heuristic: one sentence negates the core of the other."""
    for prefix in _NEGATION_PREFIXES:
        if a.startswith(prefix):
            core = a[len(prefix):]
            if len(core) >= 5 and core in b:
                return True
        if b.startswith(prefix):
            core = b[len(prefix):]
            if len(core) >= 5 and core in a:
                return True
    return False


class CorpusScanner:
    """Scan ``tasks/`` and ``decisions/`` for duplicates and conflicts.

    Parameters
    ----------
    root:
        Project root directory containing ``.navig/``.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._navig_dir = self._root / ".navig"

    def _corpus_dirs(self) -> list[Path]:
        """Return directories to scan."""
        candidates = [
            self._navig_dir / "plans" / "tasks" / "active",
            self._navig_dir / "plans" / "tasks" / "done",
            self._navig_dir / "plans" / "tasks" / "review",
            self._navig_dir / "plans" / "decisions",
            self._navig_dir / "inbox",
        ]
        return [d for d in candidates if d.is_dir()]

    def _collect_items(self) -> list[InboxItem]:
        """Read all markdown files from corpus directories."""
        items: list[InboxItem] = []
        seen_paths: set[Path] = set()

        for directory in self._corpus_dirs():
            for entry in sorted(directory.iterdir()):
                if not entry.is_file():
                    continue
                if entry.resolve() in seen_paths:
                    continue
                lower = entry.name.lower()
                if not (
                    lower.endswith(".md")
                    or lower.endswith(".md.done")
                    or lower.endswith(".md.archive")
                    or lower.endswith(".md.review")
                ):
                    continue

                item = _read_as_item(entry)
                if item is not None:
                    items.append(item)
                    seen_paths.add(item.path)

        return items

    def scan_for_duplicates(self) -> list[DuplicateMatch]:
        """Scan the full corpus for potential duplicates.

        Uses bidirectional substring matching on titles.

        Returns
        -------
        list[DuplicateMatch]
            Pairs of files detected as potential duplicates.
        """
        items = self._collect_items()
        title_map: dict[Path, str] = {}
        for item in items:
            title_map[item.path] = _extract_title(item)

        matches: list[DuplicateMatch] = []
        checked: set[tuple[Path, Path]] = set()

        for i, item_a in enumerate(items):
            title_a = title_map[item_a.path]
            if len(title_a) < 3:
                continue

            for item_b in items[i + 1:]:
                pair = (item_a.path, item_b.path)
                if pair in checked:
                    continue
                checked.add(pair)

                title_b = title_map[item_b.path]
                if len(title_b) < 3:
                    continue

                if title_a in title_b or title_b in title_a:
                    matches.append(
                        DuplicateMatch(
                            file_a=item_a.path,
                            file_b=item_b.path,
                            reason=f"Title substring match: '{title_a}' ↔ '{title_b}'",
                        )
                    )

        return matches

    def scan_for_conflicts(self) -> list[ConflictMatch]:
        """Scan the full corpus for contradicting assertions.

        Uses negation-prefix heuristics on extracted sentences.

        Returns
        -------
        list[ConflictMatch]
            Pairs of files with detected conflicts.
        """
        items = self._collect_items()
        sentence_map: dict[Path, list[str]] = {}
        for item in items:
            sentence_map[item.path] = _extract_sentences(item.body)

        matches: list[ConflictMatch] = []
        checked: set[tuple[Path, Path]] = set()

        for i, item_a in enumerate(items):
            sents_a = sentence_map[item_a.path]
            if not sents_a:
                continue

            for item_b in items[i + 1:]:
                pair = (item_a.path, item_b.path)
                if pair in checked:
                    continue
                checked.add(pair)

                sents_b = sentence_map[item_b.path]
                if not sents_b:
                    continue

                for sa in sents_a:
                    found = False
                    for sb in sents_b:
                        if _is_contradiction(sa, sb):
                            matches.append(
                                ConflictMatch(
                                    file_a=item_a.path,
                                    file_b=item_b.path,
                                    reason=f"Contradiction: '{sa[:60]}…' vs '{sb[:60]}…'",
                                )
                            )
                            found = True
                            break
                    if found:
                        break

        return matches

    def full_scan(self) -> tuple[list[DuplicateMatch], list[ConflictMatch]]:
        """Run both duplicate and conflict scans.

        Returns
        -------
        tuple[list[DuplicateMatch], list[ConflictMatch]]
            (duplicates, conflicts)
        """
        return self.scan_for_duplicates(), self.scan_for_conflicts()
