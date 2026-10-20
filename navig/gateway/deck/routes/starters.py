"""GET /api/deck/suggestions/starters — chat starter suggestions (see agent/proactive/starters).

Cached for five minutes per process: the empty chat screen asks on every new chat, and
the answer (pending memories, connected accounts) changes rarely. ``?fresh=1`` bypasses
the cache (after the user keeps a memory or connects an account).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None

CACHE_SECONDS = 300
_cache: dict[str, Any] = {"at": 0.0, "data": None}


async def handle_starters(request: "web.Request") -> "web.Response":
    from navig.agent.proactive.starters import build_starters

    fresh = (request.query.get("fresh") or "").lower() in ("1", "true", "yes")
    now = time.monotonic()
    if fresh or _cache["data"] is None or now - _cache["at"] > CACHE_SECONDS:
        _cache["data"] = await asyncio.to_thread(build_starters)
        _cache["at"] = now
    return web.json_response({"starters": _cache["data"]})
