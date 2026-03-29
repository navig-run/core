from __future__ import annotations

from pathlib import Path

from navig.spaces.next_action import select_best_next_action
from navig.spaces.progress import collect_spaces_progress


def build_spaces_briefing_lines(
    cwd: Path | None = None,
    max_items: int = 5,
) -> list[str]:
    rows = collect_spaces_progress(cwd=cwd)
    if not rows:
        return ["_No spaces available for briefing._"]

    lines: list[str] = ["*Spaces Progress:*"]
    for row in rows[:max_items]:
        lines.append(f"- `{row.name}` ({row.scope}) — {row.completion_pct:.1f}% · {row.goal}")

    action = select_best_next_action(cwd=cwd)
    if action:
        lines.append("")
        lines.append("*Action Focus:*")
        lines.append(
            f"- `{action.space}` ({action.scope}) — "
            f"{action.next_task or 'Define next concrete task in CURRENT_PHASE.md'}"
        )

    return lines
