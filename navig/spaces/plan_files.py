"""Where a space keeps its plan files — one answer, for every reader.

`CURRENT_PHASE.md`, `DEV_PLAN.md`, `ROADMAP.md` and `VISION.md` are read by name across
the tree, and four readers each decided independently where to look. Measured on this
install, over the 19 discovered spaces:

    CURRENT_PHASE.md at the space ROOT      :  1
    CURRENT_PHASE.md at .navig/plans/       : 10
    no CURRENT_PHASE.md at all              :  8

Every reader looked at the ROOT. So `navig space next`, the kickoff briefing and the
progress bar found nothing for **10 of the 11 spaces that have a phase file** — and the
failure is silent by construction: a missing plan file is indistinguishable from a space
with no plan, so all three surfaces answered "nothing to do" and looked correct.

`kickoff.py` already half-knew: it read the pending actions from `.navig/plans` while
reading the phase text from the root, in the same function.

⚠ It read them from ``cwd / ".navig" / "plans"`` — the PROCESS's directory, not the
space's. A briefing built for space X by a daemon sitting somewhere else read whatever
that directory happened to hold, or nothing. That is a different bug wearing the same
symptom, and it is why this module takes a `space_path` and never a cwd.

Search order is nearest-first: `.navig/plans/` (where they live), then `plans/`, then
the space root (the legacy markdown-only layout, still one real space here). Writing
prefers `.navig/plans/`.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["PLAN_DIRS", "PLAN_FILES", "plan_file", "plans_dir", "read_plan"]

#: The plan documents read by name across the tree. Renaming one of these is a synaptic
#: change — ~20 sites read them — so the list is here to be imported, not retyped.
PLAN_FILES: tuple[str, ...] = (
    "CURRENT_PHASE.md",
    "DEV_PLAN.md",
    "ROADMAP.md",
    "VISION.md",
    "SPEC.md",
    "TASKS.md",
)

#: Where to look, nearest first. "" is the space root — the legacy layout, kept because
#: one real space still uses it and dropping it would break that space silently.
PLAN_DIRS: tuple[str, ...] = (".navig/plans", "plans", "")


def plan_file(space_path: Path, name: str) -> Path | None:
    """The first existing copy of *name*, or None.

    None means "this space has no such plan", which is a real and common answer — 8 of
    19 spaces here have no `CURRENT_PHASE.md` at all — so it is returned rather than
    raised.
    """
    for folder in PLAN_DIRS:
        candidate = (space_path / folder / name) if folder else (space_path / name)
        try:
            if candidate.is_file():
                return candidate
        except OSError:  # a permission problem on one space must not stop the others
            logger.debug("plan lookup failed for %s", candidate)
    return None


def read_plan(space_path: Path, name: str) -> str:
    """The text of a plan file, or "" when it is absent or unreadable.

    "" for both, deliberately: every caller treats an absent plan and an empty one the
    same way (there is nothing to extract), and raising would turn one unreadable space
    into a briefing that fails for all of them.
    """
    path = plan_file(space_path, name)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("plan read failed for %s: %s", path, exc)
        return ""


def plans_dir(space_path: Path) -> Path:
    """Where a NEW plan file should be written.

    `.navig/plans/` unless this space already keeps them somewhere else — moving a
    space's plans out from under it because a writer preferred a different folder is
    how you end up with two `CURRENT_PHASE.md` files and no way to tell which is read.
    """
    for folder in PLAN_DIRS:
        base = (space_path / folder) if folder else space_path
        if any((base / name).is_file() for name in PLAN_FILES):
            return base
    return space_path / ".navig" / "plans"
