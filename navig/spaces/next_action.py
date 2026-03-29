from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from navig.spaces.progress import collect_spaces_progress, read_space_progress
from navig.spaces.resolver import resolve_space

_PENDING_RE = re.compile(r"^\s*-\s*\[\s\]\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class SpaceNextAction:
    space: str
    scope: str
    goal: str
    completion_pct: float
    next_task: str


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def first_pending_task(current_phase_text: str) -> str:
    match = _PENDING_RE.search(current_phase_text or "")
    if not match:
        return ""
    return match.group(1).strip()


def get_space_next_action(
    space: str,
    cwd: Path | None = None,
) -> SpaceNextAction | None:
    cfg = resolve_space(space, cwd=cwd)
    if not cfg.path.exists():
        return None

    current_phase = cfg.path / "CURRENT_PHASE.md"
    task = first_pending_task(_safe_read(current_phase))
    progress = read_space_progress(cfg.canonical_name, cfg.path, cfg.scope)

    return SpaceNextAction(
        space=cfg.canonical_name,
        scope=cfg.scope,
        goal=progress.goal,
        completion_pct=progress.completion_pct,
        next_task=task,
    )


def select_best_next_action(cwd: Path | None = None) -> SpaceNextAction | None:
    rows = collect_spaces_progress(cwd=cwd)
    if not rows:
        return None

    candidates: list[SpaceNextAction] = []
    for row in rows:
        task = first_pending_task(_safe_read(row.path / "CURRENT_PHASE.md"))
        candidates.append(
            SpaceNextAction(
                space=row.name,
                scope=row.scope,
                goal=row.goal,
                completion_pct=row.completion_pct,
                next_task=task,
            )
        )

    with_pending = [c for c in candidates if c.next_task]
    target_pool = with_pending or candidates
    target_pool.sort(key=lambda item: item.completion_pct)
    return target_pool[0] if target_pool else None


def build_continuation_prompt(
    preferred_space: str | None = None,
    cwd: Path | None = None,
) -> str:
    action = (
        get_space_next_action(preferred_space, cwd=cwd)
        if preferred_space
        else select_best_next_action(cwd=cwd)
    )

    base = (
        "Continue autonomously with one concrete next step only. "
        "Be concise, action-oriented, and avoid repeating earlier text."
    )
    if not action:
        return base

    task = action.next_task or "identify the next highest-impact pending step"
    return (
        f"{base} "
        f"Work in `{action.space}` space ({action.scope}); goal: {action.goal}; "
        f"progress: {action.completion_pct:.1f}%; next task: {task}."
    )
