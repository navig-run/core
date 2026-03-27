"""
navig.ui.timeline — Timestamped event timeline renderer.

Each event is rendered on one line:
  timestamp  icon  label  —  detail
"""

from __future__ import annotations

import sys

from navig.ui.models import Event
from navig.ui.theme import COLOR_STYLE, SAFE_MODE, console


def render_event_timeline(
    events: list[Event],
    *,
    title: str = "Timeline",
    show_title: bool = True,
) -> None:
    """Render timestamped events as a vertical list. Never raises."""
    try:
        if not events:
            return
        if show_title and title:
            console.print(f"[bold]{title}[/bold]")
        for ev in events:
            color = COLOR_STYLE.get(ev.color, ev.color)
            sep = "—" if not SAFE_MODE else "-"
            console.print(
                f"  [dim]{ev.timestamp}[/dim]  "
                f"[{color}]{ev.icon}[/{color}]  "
                f"[{color}]{ev.label}[/{color}]  "
                f"[dim]{sep}  {ev.detail}[/dim]"
            )
    except Exception:
        try:
            if show_title and title:
                print(f"  {title}", file=sys.stdout)
            for ev in events:
                print(
                    f"  {ev.timestamp}  {ev.icon_safe if SAFE_MODE else ev.icon}  {ev.label}  {ev.detail}",
                    file=sys.stdout,
                )
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
