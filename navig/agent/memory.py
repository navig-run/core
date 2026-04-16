from __future__ import annotations

from typing import Any


class _LegacyMemoryAdapter:
    """Compatibility adapter exposing get_recent() for legacy callers."""

    def get_recent(self, user_id: str, limit: int = 8) -> list[dict[str, Any]]:
        return []


def get_memory() -> _LegacyMemoryAdapter:
    return _LegacyMemoryAdapter()
