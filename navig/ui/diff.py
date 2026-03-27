"""
navig.ui.diff — Unified diff preview with semantic coloring.

  + green  → additions
  - red    → removals
  context  → dim

Only rendered when debug context is active or explicitly requested.
"""

from __future__ import annotations

import sys

from navig.ui.models import DiffLine, DiffPreview
from navig.ui.theme import console

_OP_STYLE = {
    "add": ("green", "+"),
    "remove": ("red", "-"),
    "context": ("dim", " "),
}


def render_diff_preview(
    diff: DiffPreview,
    *,
    debug: bool = False,
    max_lines: int = 40,
) -> None:
    """Render a diff block. Skipped unless debug=True or NAVIG_DEBUG env is set.
    Never raises."""
    import os

    if not debug and os.getenv("NAVIG_DEBUG", "0") != "1":
        return
    try:
        if not diff.lines:
            return
        console.print(f"[bold dim]{diff.title}[/bold dim]")
        shown = diff.lines[:max_lines]
        for line in shown:
            style, prefix = _OP_STYLE.get(line.op, ("white", " "))
            content = line.content.rstrip("\n")
            console.print(f"  [{style}]{prefix} {content}[/{style}]")
        if len(diff.lines) > max_lines:
            console.print(f"  [dim]… {len(diff.lines) - max_lines} more lines[/dim]")
    except Exception:
        try:
            print(f"  {diff.title}", file=sys.stdout)
            for line in diff.lines[:max_lines]:
                prefix = {"add": "+", "remove": "-", "context": " "}.get(line.op, " ")
                print(f"  {prefix} {line.content}", file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical


def diff_lines_from_text(before: str, after: str) -> list[DiffLine]:
    """Generate DiffLine list from two multiline strings."""
    import difflib

    lines: list[DiffLine] = []
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    for group in difflib.unified_diff(before_lines, after_lines, lineterm=""):
        if group.startswith("+++") or group.startswith("---") or group.startswith("@@"):
            lines.append(DiffLine(op="context", content=group))
        elif group.startswith("+"):
            lines.append(DiffLine(op="add", content=group[1:]))
        elif group.startswith("-"):
            lines.append(DiffLine(op="remove", content=group[1:]))
        else:
            lines.append(DiffLine(op="context", content=group))
    return lines
