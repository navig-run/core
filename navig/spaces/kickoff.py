from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from navig.spaces.progress import _parse_frontmatter_map

_PENDING_CHECKBOX_RE = re.compile(r"^\s*-\s*\[\s\]\s*(.+)$", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class SpaceKickoff:
    space: str
    goal: str
    actions: list[str]


def _vision_goal(vision_text: str, fallback: str) -> str:
    fm = _parse_frontmatter_map(vision_text)
    goal = (fm.get("goal") or "").strip()
    if goal:
        return goal

    for line in (vision_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _extract_pending_actions(markdown: str) -> list[str]:
    pending = [m.group(1).strip() for m in _PENDING_CHECKBOX_RE.finditer(markdown or "") if m.group(1).strip()]
    if pending:
        return pending

    actions: list[str] = []
    for match in _BULLET_RE.finditer(markdown or ""):
        item = match.group(1).strip()
        if not item or item.startswith("["):
            continue
        if item.startswith("#"):
            continue
        actions.append(item)
    return actions


def build_space_kickoff(
    space_name: str,
    space_path: Path,
    cwd: Path | None = None,
    max_items: int = 3,
) -> SpaceKickoff:
    from navig.spaces.plan_files import read_plan  # noqa: PLC0415

    vision_text = read_plan(space_path, "VISION.md")
    phase_text = read_plan(space_path, "CURRENT_PHASE.md")

    goal = _vision_goal(vision_text, f"{space_name} priorities")

    candidates: list[str] = []
    seen: set[str] = set()

    def _append(items: list[str]) -> None:
        for item in items:
            normalized = re.sub(r"\s+", " ", item).strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(normalized)

    _append(_extract_pending_actions(phase_text))

    # ⚠ These used to be read from ``cwd / ".navig" / "plans"`` — the PROCESS's
    # directory, not this space's. A briefing built for space X by a daemon sitting
    # anywhere else read whatever that folder happened to hold, or nothing at all.
    # `cwd` is kept in the signature because callers pass it; it no longer decides
    # which space's plans are read.
    for name in ("DEV_PLAN.md", "ROADMAP.md", "CURRENT_PHASE.md"):
        _append(_extract_pending_actions(read_plan(space_path, name)))

    return SpaceKickoff(
        space=space_name,
        goal=goal,
        actions=candidates[: max(1, max_items)],
    )
