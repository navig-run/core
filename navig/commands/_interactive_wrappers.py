"""Shared helpers for interactive menu command wrappers.

These wrappers are intentionally thin and should not contain business logic.
"""

from __future__ import annotations

from typing import Any, Callable


def run_menu_wrapper(command: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """Execute an interactive-menu wrapper target command.

    Keeps wrapper implementations consistent and avoids duplicated one-line
    forwarding functions across command modules.
    """
    command(*args, **kwargs)
