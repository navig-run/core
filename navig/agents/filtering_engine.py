"""
FilteringEngine — Navig CLI inbox filtering and normalization.

Scans all .md files under .navig/ (except the inbox/ staging area),
applies normalization rules (frontmatter, headings), and updates files
in-place.  Also supports a polling watch loop that re-runs on changes.

Usage
-----
    from navig.agents.filtering_engine import FilteringEngine

    engine = FilteringEngine(project_root=Path("."))
    results = engine.scan_and_filter(dry_run=False)

    # Or start a long-lived watch loop (blocking, run in a thread):
    engine.start_watch(interval_secs=5)
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from navig.core.yaml_io import atomic_write_text

logger = logging.getLogger(__name__)

# ── Public constants ──────────────────────────────────────────────────────────

#: Directories under .navig/ that the engine will scan.  The inbox/ staging
#: area is intentionally excluded — those files are router *inputs*, not
#: processed documents.
SCAN_ROOTS: tuple[str, ...] = (
    ".navig/plans",
    ".navig/wiki",
    ".navig/memory",
    ".navig/plans/briefs",
)

#: Inbox staging area — skipped by the filtering engine.
INBOX_RELATIVE = ".navig/plans/inbox"

# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class NormalizationRule:
    """A single named normalization step applied to file content."""

    name: str
    description: str


@dataclass
class FilterResult:
    """Result of filtering a single file."""

    path: Path
    changed: bool = False  # File was actually written
    would_change: bool = False  # Dry-run detected a potential change
    skipped: bool = False  # File is not eligible (not .md, etc.)
    error: str | None = None  # Non-None if processing failed
    rules_applied: list[str] = field(default_factory=list)


# ── Normalization helpers ─────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\n[\s\S]*?\n---\n?", re.MULTILINE)


def apply_frontmatter(content: str, content_type: str = "other") -> str:
    """
    Ensure the file has a YAML frontmatter block.

    If a block already exists (starts with ``---``), the content is
    returned unchanged.  Otherwise a minimal frontmatter block containing
    ``type``, ``created``, and ``navig_filter`` metadata is prepended.
    """
    if content.lstrip().startswith("---"):
        return content  # already has frontmatter

    today = datetime.now().strftime("%Y-%m-%d")
    fm = f"---\ntype: {content_type}\ncreated: {today}\nnavig_filter: auto\n---\n\n"
    return fm + content


def normalize_headings(content: str) -> str:
    """
    Ensure a single H1 heading exists in the document body.

    * If the body already has an ``# H1``, nothing is changed.
    * If the first heading in the body is ``## H2``, it is promoted to ``# H1``
      so that all downstream tools can rely on a consistent title anchor.

    The frontmatter block (if present) is never modified.
    """
    # Split frontmatter from body
    fm_match = _FRONTMATTER_RE.match(content)
    if fm_match:
        frontmatter = fm_match.group(0)
        body = content[fm_match.end() :]
    else:
        frontmatter = ""
        body = content

    # If H1 already exists in body — nothing to do
    if re.search(r"^# ", body, re.MULTILINE):
        return content

    # Promote first H2 → H1
    promoted = re.sub(r"^## ", "# ", body, count=1, flags=re.MULTILINE)
    # Demote subsequent occurrences of H2 only if we already promoted one
    if promoted != body:
        body = promoted

    return frontmatter + body


def _infer_content_type(path: Path) -> str:
    """Guess content_type from directory location when no frontmatter exists."""
    parts = set(path.parts)
    if "plans" in parts and "briefs" in parts:
        return "brief"
    if "plans" in parts:
        return "task_roadmap"
    if "wiki" in parts:
        return "wiki_knowledge"
    if "memory" in parts:
        return "memory_log"
    return "other"


def _file_hash(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()


# ── FilteringEngine ───────────────────────────────────────────────────────────


class FilteringEngine:
    """
    Scan .navig/** markdown files, apply normalization, write back in-place.

    Parameters
    ----------
    project_root:
        Root of the project; must contain a ``.navig/`` directory.
    on_change:
        Optional callback called with the Path of every file that is about
        to be (or was) changed.  Used by the watch loop and unit tests.
    """

    def __init__(
        self,
        project_root: Path,
        on_change: Callable[[Path], None] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.on_change = on_change
        self._last_hashes: dict[str, str] = {}
        self._stop_event = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────────

    def filter_file(self, path: Path, dry_run: bool = False) -> FilterResult:
        """
        Apply all normalization rules to a single file.

        Parameters
        ----------
        path:    Absolute (or project-relative) path to the target file.
        dry_run: When *True*, compute whether a change is needed but do NOT
                 write anything.

        Returns
        -------
        FilterResult with ``changed``, ``would_change``, ``skipped``, or
        ``error`` set as appropriate.
        """
        path = Path(path)
        result = FilterResult(path=path)

        # Gate: only .md files
        if path.suffix.lower() != ".md":
            result.skipped = True
            return result

        # Gate: file must exist
        if not path.exists():
            result.error = f"File not found: {path}"
            logger.warning("[FilteringEngine] %s", result.error)
            return result

        try:
            original = path.read_text(encoding="utf-8")
        except OSError as exc:
            result.error = str(exc)
            logger.error("[FilteringEngine] Read error %s: %s", path, exc)
            return result

        content_type = _infer_content_type(path)
        processed = original

        # Rule 1: frontmatter
        after_fm = apply_frontmatter(processed, content_type)
        if after_fm != processed:
            result.rules_applied.append("frontmatter")
            processed = after_fm

        # Rule 2: heading normalisation
        after_h = normalize_headings(processed)
        if after_h != processed:
            result.rules_applied.append("normalize_headings")
            processed = after_h

        if processed == original:
            # Nothing to change — already clean
            return result

        result.would_change = True

        if dry_run:
            logger.info(
                "[FilteringEngine] [dry-run] would update %s (rules: %s)",
                path,
                result.rules_applied,
            )
            return result

        # Write atomically via a .tmp sibling to prevent partial-write corruption
        # if the process is interrupted (SIGINT, disk-full) mid-write.
        try:
            import os

            if self.on_change:
                self.on_change(path)
            tmp_path = path.with_suffix(".tmp.md")
            atomic_write_text(tmp_path, processed)
            os.replace(tmp_path, path)
            result.changed = True
            logger.info(
                "[FilteringEngine] Updated %s (rules: %s)",
                path,
                result.rules_applied,
            )
        except OSError as exc:
            # Clean up the tmp file if the rename failed
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass  # best-effort: skip on IO error
            result.error = str(exc)
            logger.error("[FilteringEngine] Write error %s: %s", path, exc)

        return result

    def scan_and_filter(self, dry_run: bool = False) -> list[FilterResult]:
        """
        Walk all eligible .navig/ subdirectories and filter each .md file.

        Files inside ``.navig/plans/inbox/`` are intentionally excluded —
        they are router *inputs* and must not be modified by the filter.

        Returns
        -------
        List of FilterResult for every file that was assessed (changed,
        would_change, skipped, or error).  Files that are clean (no change
        needed) are omitted from the result list.
        """
        results: list[FilterResult] = []
        inbox_abs = (self.project_root / INBOX_RELATIVE).resolve()

        for rel in SCAN_ROOTS:
            scan_dir = self.project_root / rel
            if not scan_dir.exists():
                continue
            for md_path in sorted(scan_dir.iterdir()):
                if md_path.is_dir():
                    continue  # subdirectories handled by their own SCAN_ROOTS entry
                # Skip inbox staging area
                try:
                    md_path.resolve().relative_to(inbox_abs)
                    continue  # it's inside the inbox — skip
                except ValueError:
                    pass  # malformed value; skip

                result = self.filter_file(md_path, dry_run=dry_run)

                # Only collect actionable results
                if result.changed or result.would_change or result.skipped or result.error:
                    results.append(result)

        return results

    # ── Watch loop ────────────────────────────────────────────────────────────

    def start_watch(
        self,
        interval_secs: float = 5.0,
        dry_run: bool = False,
        max_cycles: int | None = None,
    ) -> None:
        """
        Blocking polling loop.  Checks for new or modified files every
        ``interval_secs`` seconds and calls :meth:`filter_file` on each.

        Parameters
        ----------
        interval_secs: How often to poll (default 5 s).
        dry_run:       Passed through to :meth:`filter_file`.
        max_cycles:    Stop after this many cycles (used by tests; *None*
                       means run forever until :meth:`stop_watch` is called).
        """
        self._stop_event.clear()
        cycles = 0
        inbox_abs = (self.project_root / INBOX_RELATIVE).resolve()

        logger.info(
            "[FilteringEngine] Watch loop started (interval=%.1fs, dry_run=%s)",
            interval_secs,
            dry_run,
        )

        while not self._stop_event.is_set():
            self._scan_for_changes(dry_run=dry_run, inbox_abs=inbox_abs)

            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break

            self._stop_event.wait(interval_secs)

        logger.info("[FilteringEngine] Watch loop stopped after %d cycles", cycles)

    def stop_watch(self) -> None:
        """Signal the watch loop to exit on its next iteration."""
        self._stop_event.set()

    # ── Internals ────────────────────────────────────────────────────────────

    def _collect_all_md_files(self, inbox_abs: Path) -> list[Path]:
        """Return all .md files under SCAN_ROOTS, excluding inbox."""
        files: list[Path] = []
        for rel in SCAN_ROOTS:
            scan_dir = self.project_root / rel
            if not scan_dir.exists():
                continue
            for p in sorted(scan_dir.iterdir()):
                if p.is_dir():
                    continue
                if p.suffix.lower() != ".md":
                    continue
                try:
                    p.resolve().relative_to(inbox_abs)
                    continue  # inside inbox — skip
                except ValueError:
                    pass  # malformed value; skip
                files.append(p)
        return files

    def _scan_for_changes(self, dry_run: bool, inbox_abs: Path) -> None:
        """Detect new or modified files; filter them."""
        files = self._collect_all_md_files(inbox_abs)

        for path in files:
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                continue

            current_hash = _file_hash(raw)
            prev_hash = self._last_hashes.get(str(path))

            if current_hash != prev_hash:
                # New file or modified — filter it
                self._last_hashes[str(path)] = current_hash
                if prev_hash is not None:
                    # It's a genuine modification, not just first-seen
                    logger.info("[FilteringEngine] Change detected: %s", path.name)
                    if self.on_change:
                        self.on_change(path)
                    self.filter_file(path, dry_run=dry_run)
                else:
                    # First time we see this file — record hash, fire on_change
                    # (new file) but also run filter so frontmatter etc. are clean
                    if self.on_change:
                        self.on_change(path)
                    self.filter_file(path, dry_run=dry_run)
