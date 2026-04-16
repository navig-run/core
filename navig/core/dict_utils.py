"""Shared dictionary utilities used across config layers."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict.

    - dict values are merged recursively.
    - list values are concatenated (override list appended to base list).
    - all other leaf values from *override* supersede *base* (deep-copied).
    """
    merged: dict[str, Any] = dict(base)
    for key, override_value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            merged[key] = deep_merge(base_value, override_value)
        elif isinstance(base_value, list) and isinstance(override_value, list):
            merged[key] = base_value + override_value
        else:
            merged[key] = copy.deepcopy(override_value)
    return merged


def truncate_output(text: str, limit: int) -> str:
    """Truncate *text* to *limit* characters with a char-count note if trimmed."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated — {len(text)} chars total]"


def utc_now() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (e.g. ``2024-01-15T10:30:45.123456+00:00``)."""
    return datetime.now(timezone.utc).isoformat()
