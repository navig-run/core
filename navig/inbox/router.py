"""
navig.inbox.router — Dispatch engine for inbox routing decisions.

Supports three dispatch modes:
  COPY  — copy the source file to the destination
  MOVE  — move the source file to the destination; original removed
  LINK  — create a symlink at the destination pointing to the source

Conflict resolution strategies:
  rename    — append _1, _2 … until unique (default)
  skip      — leave the file in inbox; mark as skipped
  overwrite — replace the existing destination file
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

from navig.inbox.classifier import ClassifyResult

# ── Category → default destination mapping ───────────────────

_CATEGORY_DEST: Dict[str, str] = {
    "wiki/knowledge": ".navig/wiki/knowledge/inbox",
    "wiki/technical": ".navig/wiki/technical/inbox",
    "hub/tasks": ".navig/wiki/hub/tasks",
    "hub/roadmap": ".navig/wiki/hub/roadmap",
    "hub/changelog": ".navig/wiki/hub/changelog",
    "external/business": ".navig/wiki/external/business",
    "external/marketing": ".navig/wiki/external/marketing",
    "archive": ".navig/wiki/archive",
    "ignore": "",  # not routed
}


class RouteMode(str, Enum):
    COPY = "copy"
    MOVE = "move"
    LINK = "link"


class ConflictStrategy(str, Enum):
    RENAME = "rename"
    SKIP = "skip"
    OVERWRITE = "overwrite"


# ── Result ────────────────────────────────────────────────────


@dataclass
class RouteResult:
    source: str
    destination: Optional[str]
    mode: str
    status: str  # "routed" | "skipped" | "ignored" | "error"
    result_path: Optional[str] = None
    error: Optional[str] = None
    category: str = ""
    confidence: float = 0.0


# ── Router ────────────────────────────────────────────────────


class InboxRouter:
    """
    Route an inbox item to its destination based on a ClassifyResult.

    Parameters
    ----------
    project_root:
        Root of the NAVIG project (the dir that contains `.navig/`).
        Used to resolve destination paths.  Falls back to cwd if None.
    mode:
        Default dispatch mode (COPY / MOVE / LINK).
    conflict:
        Default conflict resolution strategy.
    min_confidence:
        Items below this threshold are kept in inbox (not routed).
    dest_override:
        Optional mapping of category → absolute destination path.
        Used in tests and custom routing setups.
    """

    def __init__(
        self,
        project_root: Optional[Path] = None,
        mode: RouteMode = RouteMode.COPY,
        conflict: ConflictStrategy = ConflictStrategy.RENAME,
        min_confidence: float = 0.30,
        dest_override: Optional[Dict[str, str]] = None,
    ) -> None:
        self.project_root = project_root or Path.cwd()
        self.mode = mode
        self.conflict = conflict
        self.min_confidence = min_confidence
        self._dest_override = dest_override or {}

    def route(
        self,
        source_path: Path,
        classify_result: ClassifyResult,
        dry_run: bool = False,
    ) -> RouteResult:
        """
        Route a file based on its classification result.

        Returns a RouteResult describing what happened (or would happen
        in dry_run mode without touching the filesystem).
        """
        category = classify_result.category

        # Ignored category or very low confidence → skip
        if category == "ignore" or classify_result.confidence < self.min_confidence:
            return RouteResult(
                source=str(source_path),
                destination=None,
                mode=self.mode.value,
                status="ignored",
                category=category,
                confidence=classify_result.confidence,
            )

        # Resolve destination directory
        dest_key = self._dest_override.get(category) or _CATEGORY_DEST.get(category, "")
        if not dest_key:
            return RouteResult(
                source=str(source_path),
                destination=None,
                mode=self.mode.value,
                status="ignored",
                category=category,
                confidence=classify_result.confidence,
            )

        if os.path.isabs(dest_key):
            dest_dir = Path(dest_key)
        else:
            dest_dir = self.project_root / dest_key

        dest_file = dest_dir / source_path.name
        resolved = str(dest_file)

        if dry_run:
            return RouteResult(
                source=str(source_path),
                destination=resolved,
                mode=self.mode.value,
                status="routed",
                result_path=None,
                category=category,
                confidence=classify_result.confidence,
            )

        # Handle conflicts
        if dest_file.exists():
            if self.conflict == ConflictStrategy.SKIP:
                return RouteResult(
                    source=str(source_path),
                    destination=resolved,
                    mode=self.mode.value,
                    status="skipped",
                    category=category,
                    confidence=classify_result.confidence,
                )
            elif self.conflict == ConflictStrategy.RENAME:
                dest_file = _unique_path(dest_file)
                resolved = str(dest_file)
            # OVERWRITE falls through to dispatch

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            self._dispatch(source_path, dest_file)
        except Exception as exc:
            return RouteResult(
                source=str(source_path),
                destination=resolved,
                mode=self.mode.value,
                status="error",
                error=str(exc),
                category=category,
                confidence=classify_result.confidence,
            )

        return RouteResult(
            source=str(source_path),
            destination=resolved,
            mode=self.mode.value,
            status="routed",
            result_path=str(dest_file),
            category=category,
            confidence=classify_result.confidence,
        )

    def _dispatch(self, source: Path, dest: Path) -> None:
        if self.mode == RouteMode.COPY:
            shutil.copy2(str(source), str(dest))
        elif self.mode == RouteMode.MOVE:
            shutil.move(str(source), str(dest))
        elif self.mode == RouteMode.LINK:
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            try:
                dest.symlink_to(source.resolve())
            except (NotImplementedError, OSError):
                # Symlinks may require elevated privileges on Windows
                shutil.copy2(str(source), str(dest))

    def route_url(
        self,
        url: str,
        content: str,
        filename: str,
        classify_result: ClassifyResult,
        dry_run: bool = False,
    ) -> RouteResult:
        """
        Route a URL-sourced item (already fetched as string content).

        Writes content to a temp .md file then routes it.
        """
        category = classify_result.category
        dest_key = self._dest_override.get(category) or _CATEGORY_DEST.get(category, "")
        if not dest_key or category == "ignore":
            return RouteResult(
                source=url,
                destination=None,
                mode=self.mode.value,
                status="ignored",
                category=category,
                confidence=classify_result.confidence,
            )

        if os.path.isabs(dest_key):
            dest_dir = Path(dest_key)
        else:
            dest_dir = self.project_root / dest_key

        dest_file = dest_dir / filename
        if dest_file.exists() and self.conflict == ConflictStrategy.RENAME:
            dest_file = _unique_path(dest_file)

        if dry_run:
            return RouteResult(
                source=url,
                destination=str(dest_file),
                mode=self.mode.value,
                status="routed",
                category=category,
                confidence=classify_result.confidence,
            )

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file.write_text(content, encoding="utf-8")
        except Exception as exc:
            return RouteResult(
                source=url,
                destination=str(dest_file),
                mode=self.mode.value,
                status="error",
                error=str(exc),
                category=category,
                confidence=classify_result.confidence,
            )

        return RouteResult(
            source=url,
            destination=str(dest_file),
            mode=self.mode.value,
            status="routed",
            result_path=str(dest_file),
            category=category,
            confidence=classify_result.confidence,
        )


# ── Helpers ───────────────────────────────────────────────────


def _unique_path(path: Path) -> Path:
    """Return a non-conflicting path by appending _1, _2 … before the suffix."""
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
