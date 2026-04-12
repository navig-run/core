from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from navig.plans.frontmatter import (
    _safe_read,
    first_h1 as _first_h1,
    parse_frontmatter as _parse_frontmatter_map,
)
from navig.spaces.resolver import discover_space_paths

_CHECKBOX_RE = re.compile(r"^\s*-\s*\[([ xX])\]", re.MULTILINE)


@dataclass(frozen=True)
class SpaceProgress:
    name: str
    scope: str
    path: Path
    goal: str
    completion_pct: float
    last_updated: str


def _completion_from_markdown(text: str) -> float:
    checks = _CHECKBOX_RE.findall(text)
    if not checks:
        return 0.0
    done = sum(1 for c in checks if c.lower() == "x")
    return round((done / len(checks)) * 100.0, 1)


def read_space_progress(space_name: str, space_path: Path, scope: str) -> SpaceProgress:
    vision = space_path / "VISION.md"
    current_phase = space_path / "CURRENT_PHASE.md"

    vision_text = _safe_read(vision)
    phase_text = _safe_read(current_phase)

    vision_fm = _parse_frontmatter_map(vision_text)
    phase_fm = _parse_frontmatter_map(phase_text)

    goal = (
        vision_fm.get("goal")
        or phase_fm.get("goal")
        or _first_h1(vision_text)
        or f"{space_name} goals"
    )

    completion_raw = phase_fm.get("completion_pct", "")
    try:
        completion_pct = float(completion_raw)
    except ValueError:
        completion_pct = _completion_from_markdown(phase_text)

    last_updated = phase_fm.get("last_updated", "").strip()
    if not last_updated:
        try:
            ts = current_phase.stat().st_mtime
            last_updated = datetime.fromtimestamp(ts, timezone.utc).strftime(
                "%Y-%m-%d"
            )
        except OSError:
            last_updated = "n/a"

    return SpaceProgress(
        name=space_name,
        scope=scope,
        path=space_path,
        goal=goal,
        completion_pct=completion_pct,
        last_updated=last_updated,
    )


def collect_spaces_progress(cwd: Path | None = None) -> list[SpaceProgress]:
    discovered = discover_space_paths(cwd=cwd)
    rows: list[SpaceProgress] = []
    for space_name, cfg in sorted(discovered.items()):
        rows.append(read_space_progress(space_name, cfg.path, cfg.scope))
    return rows


def format_spaces_progress_lines(
    rows: list[SpaceProgress],
    max_items: int = 5,
) -> list[str]:
    if not rows:
        return ["_No spaces discovered yet._"]

    lines: list[str] = []
    for row in rows[:max_items]:
        lines.append(
            f"- `{row.name}` ({row.scope}) — {row.completion_pct:.1f}% · {row.goal}"
        )
    return lines
