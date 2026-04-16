from __future__ import annotations

from typing import Any


def send_command(payload: dict[str, Any]) -> bool:
    """Compatibility shim for legacy daemon command dispatch."""
    return False
