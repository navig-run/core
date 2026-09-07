"""Find work already written down in the operator's spaces, and propose it.

Every space carries plan files full of `- [ ]` lines — real commitments, written by
the operator or by an agent working there, that live in a file nobody opens between
sessions. This turns them into suggestions on the one list that gets looked at.

**It proposes. It never decides.** Every row lands with ``origin='agent'`` (rendered
with ✨) and carries an ``origin_ref`` — `<space>:<file>:<line>` — so the same checkbox
is never proposed twice, INCLUDING after the operator deletes it. A dismissed
suggestion that comes back on the next scan is how a helpful assistant becomes a nag,
and it is the single behaviour most likely to get the whole feature switched off.

⚠ **The scan reads NAMED FILES ONLY — never a recursive walk.** Measured on this
machine: `grep -r` over `~/.navig/spaces` does not finish in two minutes, because
spaces contain vendored `.lab/` reference corpora (whole copies of other projects,
with their own issue templates and plan documents) and `.trash-*` directories from
earlier merges. A walk would be slow AND would propose "tasks" from Telegram-iOS's
pull-request template. The named-file list is the feature, not a shortcut.

⚠ **Plan files live at `<space>/.navig/plans/`, not the space root.** Verified on the
operator's own install: 11 spaces keep `CURRENT_PHASE.md` under `.navig/plans/` and
exactly 1 at the root — while `spaces/kickoff.py` and `spaces/next_action.py` read the
ROOT, so they find nothing for almost every space. This module checks both, nearest
first; the pre-existing readers are a separate fix.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["Suggestion", "plan_files", "scan_space", "scan_spaces", "unchecked_boxes"]

#: The plan files worth reading, in the order a person would. Named, not globbed:
#: a glob over `.navig/plans/` would also pick up briefs and archived phases, and a
#: suggestion the operator has to un-suggest is worse than one that never appeared.
PLAN_FILES: tuple[str, ...] = (
    "CURRENT_PHASE.md",
    "DEV_PLAN.md",
    "TASKS.md",
    "ROADMAP.md",
)

#: Where a space keeps them, nearest-first.
PLAN_DIRS: tuple[str, ...] = (".navig/plans", "plans", "")

_UNCHECKED = re.compile(r"^\s*[-*]\s+\[\s\]\s+(?P<text>.+?)\s*$")

# A checkbox line can carry markdown, links and trailing metadata. Strip the noise so
# the task reads like something a person wrote rather than a diff fragment.
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_CODE = re.compile(r"`([^`]*)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")

_MAX_TITLE = 140
_MIN_TITLE = 4


@dataclass(frozen=True)
class Suggestion:
    """One proposed task, and exactly where it came from."""

    title: str
    space: str
    source_file: str
    line_no: int

    @property
    def origin_ref(self) -> str:
        """The dedup key. Stable across scans, and readable in a database."""
        return f"{self.space}:{self.source_file}:{self.line_no}"


def _clean(text: str) -> str:
    text = _LINK.sub(r"\1", text)
    text = _CODE.sub(r"\1", text)
    text = _BOLD.sub(r"\1", text)
    # A trailing "— owner, 2026-09-01" style note is context, not part of the task.
    text = text.split("  ")[0].strip()
    return text[:_MAX_TITLE].strip()


def plan_files(space_path: Path) -> list[Path]:
    """The plan files this space actually has, nearest-first, no duplicates."""
    found: list[Path] = []
    seen: set[Path] = set()
    for folder in PLAN_DIRS:
        base = space_path / folder if folder else space_path
        for name in PLAN_FILES:
            candidate = base / name
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen or not candidate.is_file():
                continue
            seen.add(resolved)
            found.append(candidate)
    return found


def unchecked_boxes(path: Path, *, space: str) -> list[Suggestion]:
    """Every `- [ ]` line in one file.

    Unreadable files are skipped with a log line rather than raising: one space with a
    permissions problem must not stop the other twenty being scanned.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("todo scan: cannot read %s (%s)", path, exc)
        return []

    out: list[Suggestion] = []
    for index, line in enumerate(text.splitlines(), start=1):
        match = _UNCHECKED.match(line)
        if not match:
            continue
        title = _clean(match.group("text"))
        # A one-word or empty box is a template placeholder, not a task.
        if len(title) < _MIN_TITLE:
            continue
        out.append(
            Suggestion(title=title, space=space, source_file=path.name, line_no=index)
        )
    return out


def scan_space(space: str, space_path: Path, *, limit: int = 5) -> list[Suggestion]:
    """Suggestions from one space, capped.

    The cap is per SPACE, not overall: a space with a 200-item roadmap would otherwise
    fill the whole proposal and bury every other space's genuinely-next task. Five is
    what fits on a phone screen without scrolling.
    """
    found: list[Suggestion] = []
    for path in plan_files(space_path):
        found.extend(unchecked_boxes(path, space=space))
        if len(found) >= limit:
            break
    return found[:limit]


def scan_spaces(
    spaces: dict[str, Path] | None = None, *, per_space: int = 5, store: object = None
) -> list[Suggestion]:
    """Suggestions across every space, minus anything already proposed.

    Passing ``store`` filters out sources that are already on the list or were
    dismissed — do that HERE rather than at the point of insert, so a caller rendering
    a "found N things" summary reports the number the operator will actually see.
    """
    if spaces is None:
        spaces = discover()

    out: list[Suggestion] = []
    for name, path in sorted(spaces.items()):
        out.extend(scan_space(name, path, limit=per_space))

    if store is None:
        return out

    fresh: list[Suggestion] = []
    for suggestion in out:
        try:
            if store.todo_exists_for_source(suggestion.origin_ref):  # type: ignore[attr-defined]
                continue
        except Exception:  # noqa: BLE001 — a dedup failure must not lose the scan
            logger.debug("todo scan: dedup check failed for %s", suggestion.origin_ref)
        fresh.append(suggestion)
    return fresh


def discover() -> dict[str, Path]:
    """Every enabled space, by name. Returns {} rather than raising."""
    try:
        from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415

        return {name: cfg.path for name, cfg in discover_space_paths().items()}
    except Exception:  # noqa: BLE001
        logger.exception("todo scan: could not enumerate spaces")
        return {}
