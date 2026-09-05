"""
navig.ui.summary — AI/diagnostic summary output and next-step prompt.

Always ends failure output with:
  ⚑ Recommended next step: <bold cyan command>

render_next_step is the signature close of every failure / degraded path.
"""

from __future__ import annotations

import sys

from rich.markup import escape

from navig.ui.icons import icon
from navig.ui.models import SummaryResult
from navig.ui.theme import SAFE_MODE, STYLE_AI, console


def render_next_step(
    command: str,
    *,
    label: str = "Recommended next step",
) -> None:
    """Print the ⚑ next-step line. Always shown at end of failure output. Never raises."""
    try:
        flag = icon("flag")
        console.print(
            f"\n  [{flag}] [dim]{escape(label)}:[/dim]  "
            f"[bold cyan]{escape(command)}[/bold cyan]"
        )
    except Exception:
        try:
            print(f"\n  >> {label}: {command}", file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical


def render_summary(
    result: SummaryResult,
    *,
    title: str = "Summary",
) -> None:
    """Render full AI/diagnostic summary block. Never raises."""
    try:
        # confidence badge
        bar_len = max(1, result.confidence // 10)
        fill = ("\u2588" if not SAFE_MODE else "#") * bar_len
        empty = ("\u2591" if not SAFE_MODE else ".") * (10 - bar_len)

        console.print(f"\n[bold]{escape(title)}[/bold]")
        console.print(
            f"  [dim]confidence[/dim]  "
            f"[{STYLE_AI}]{fill}[/{STYLE_AI}][dim]{empty}[/dim]  "
            f"[{STYLE_AI}]{result.confidence}%[/{STYLE_AI}]"
        )
        console.print(f"  [bold]Root cause[/bold]   {escape(result.root_cause)}")
        console.print(f"  [bold]Recommend[/bold]    {escape(result.recommendation)}")
        if result.action_prompt:
            render_next_step(result.action_prompt)
    except Exception:
        try:
            print(f"  {title}", file=sys.stdout)
            print(f"  Root cause: {result.root_cause}", file=sys.stdout)
            print(f"  Recommend:  {result.recommendation}", file=sys.stdout)
            if result.action_prompt:
                print(f"  >> {result.action_prompt}", file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical


def render_ai_response(
    text: str,
    *,
    title: str | None = None,
) -> None:
    """Render a freeform AI response block. Never raises."""
    try:
        if title:
            console.print(f"\n[{STYLE_AI}]{icon('ai')} {escape(title)}[/{STYLE_AI}]")
        # print lines with consistent dim prefix. `text` is model output — the data most
        # likely of all to contain brackets (citations, paths, code), so it must be escaped:
        # unescaped, "[1]" vanishes and "[/tmp/x]" raises MarkupError and drops the block.
        for line in text.splitlines():
            console.print(f"  {escape(line)}")
    except Exception:
        try:
            if title:
                print(f"\n  {title}", file=sys.stdout)
            print(text, file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
