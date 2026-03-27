"""
navig.ui.formatters — KV diagnostic pairs and command row alignment.

  render_kv_diagnostics(pairs)       — aligned key→value diagnostic block
  render_command_row(label, command) — left-padded label + cyan command
"""

from __future__ import annotations

import sys
from typing import List, Optional, Tuple

from navig.ui.theme import console

_LABEL_WIDTH = 22
_CMD_WIDTH = 28


def render_kv_diagnostics(
    pairs: List[Tuple[str, str]],
    *,
    title: Optional[str] = None,
    label_width: int = _LABEL_WIDTH,
) -> None:
    """Render aligned key → value diagnostic pairs. Never raises."""
    try:
        if not pairs:
            return
        if title:
            console.print(f"[bold]{title}[/bold]")
        for key, value in pairs:
            label = key.ljust(label_width)
            console.print(f"  [dim]{label}[/dim]  {value}")
    except Exception:
        try:
            if title:
                print(f"  {title}", file=sys.stdout)
            for key, value in pairs:
                print(f"  {key}: {value}", file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical


def render_command_row(
    label: str,
    command: str,
    *,
    description: Optional[str] = None,
    label_width: int = _LABEL_WIDTH,
) -> None:
    """Print one padded label + bold cyan command line. Never raises."""
    try:
        lbl = label.ljust(label_width)
        desc = f"  [dim]{description}[/dim]" if description else ""
        console.print(f"  [dim]{lbl}[/dim]  [bold cyan]{command}[/bold cyan]{desc}")
    except Exception:
        try:
            print(f"  {label}: {command}", file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical


def render_section_divider(title: str = "") -> None:
    """Print a thin horizontal rule with optional centered title. Never raises."""
    try:
        if title:
            console.rule(f"[dim]{title}[/dim]", style="dim")
        else:
            console.rule(style="dim")
    except Exception:
        try:
            print(f"  ── {title} ──" if title else "  ──────────", file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
