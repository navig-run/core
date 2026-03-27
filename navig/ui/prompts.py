"""
navig.ui.prompts — Keymap footer and action approval prompt.

  render_keymap_footer(keymap)     — dim footer showing key bindings
  render_action_approval(cmd)      — wait-for-y/n approval prompt
"""

from __future__ import annotations

import sys
from typing import Dict, Optional

from navig.ui.theme import console


def render_keymap_footer(
    keymap: Dict[str, str],
    *,
    separator: str = "  ",
) -> None:
    """Print a dim key-binding footer line. Never raises."""
    try:
        if not keymap:
            return
        parts = [
            f"[bold cyan]{k}[/bold cyan] [dim]{v}[/dim]" for k, v in keymap.items()
        ]
        console.print(separator.join(parts))
    except Exception:
        try:
            parts = [f"  {k} {v}" for k, v in keymap.items()]
            print("  " + "  ".join(parts), file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical


def render_action_approval(
    command: str,
    *,
    prompt: str = "Proceed?",
    hint: Optional[str] = None,
) -> bool:
    """Show approval prompt and return True if user confirms. Never raises on display.

    Returns True on y/yes, False on anything else.
    """
    try:
        console.print(f"\n  [bold cyan]{command}[/bold cyan]")
        if hint:
            console.print(f"  [dim]{hint}[/dim]")
        console.print(f"\n  {prompt} [dim](y/n)[/dim] ", end="")
    except Exception:
        try:
            print(f"\n  {command}", file=sys.stdout)
            print(f"  {prompt} (y/n) ", end="", file=sys.stdout)
        except Exception:
            return False

    try:
        answer = input("").strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False
