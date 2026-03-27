"""
navig.ui.actions — Layer 4 action blocks, fallback messages, and action queue.

  render_actions(items)          — numbered recommended actions
  render_fallback(cmd, reason)   — daemon-offline or degraded-path message
  render_action_queue(items)     — pending approval queue
"""

from __future__ import annotations

import sys
from typing import List, Optional

from navig.ui.icons import icon
from navig.ui.models import ActionItem
from navig.ui.theme import console

_RISK_STYLE = {
    "low": "green",
    "medium": "yellow",
    "high": "red",
}


def render_actions(
    items: List[ActionItem],
    *,
    title: str = "Recommended actions",
) -> None:
    """Render numbered action list. Never raises."""
    try:
        if not items:
            return
        console.print(f"\n[bold]{title}[/bold]")
        for item in items:
            risk_style = _RISK_STYLE.get(item.risk, "white")
            val_str = (
                f"  [{risk_style}]{item.estimated_value}[/{risk_style}]"
                if item.estimated_value
                else ""
            )
            console.print(
                f"  [bold cyan]{item.index}.[/bold cyan]  "
                f"{item.description}"
                f"{val_str}"
            )
    except Exception:
        try:
            print(f"\n  {title}", file=sys.stdout)
            for item in items:
                val_str = f"  {item.estimated_value}" if item.estimated_value else ""
                print(f"  {item.index}.  {item.description}{val_str}", file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical


def render_fallback(
    cmd: str,
    reason: str = "Daemon offline",
    *,
    alternatives: Optional[List[str]] = None,
) -> None:
    """Show an inline fallback block when primary path is unavailable. Never raises."""
    try:
        ico = icon("offline")
        console.print(f"\n[dim]{ico} {reason} — {cmd} unavailable[/dim]")
        if alternatives:
            console.print("[dim]Alternatives:[/dim]")
            for alt in alternatives:
                arr = icon("arrow")
                console.print(f"  [dim]{arr}[/dim]  [bold cyan]{alt}[/bold cyan]")
    except Exception:
        try:
            print(f"\n  [offline] {reason}", file=sys.stdout)
            if alternatives:
                for alt in alternatives:
                    print(f"  -> {alt}", file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical


def render_action_queue(
    items: List[ActionItem],
    *,
    title: str = "Action queue",
) -> None:
    """Render a pending-approval action queue. Never raises."""
    try:
        if not items:
            console.print("[dim]Action queue is empty.[/dim]")
            return
        console.print(f"\n[bold]{title}[/bold]")
        for item in items:
            risk_style = _RISK_STYLE.get(item.risk, "white")
            console.print(
                f"  [{risk_style}]●[/{risk_style}]  "
                f"[bold cyan]{item.index}[/bold cyan]  "
                f"{item.description}"
            )
    except Exception:
        try:
            print(f"\n  {title}", file=sys.stdout)
            for item in items:
                print(f"  {item.index}.  {item.description}", file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
