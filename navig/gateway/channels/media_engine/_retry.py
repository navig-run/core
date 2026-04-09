"""Shared async retry helper for media-engine stages."""

from __future__ import annotations

import asyncio


async def with_retry(coro_func, retries: int = 2):
    """Retry async callable up to *retries* times on exception."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await coro_func()
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(1.0 * (attempt + 1))
    raise last_exc  # type: ignore[misc]
