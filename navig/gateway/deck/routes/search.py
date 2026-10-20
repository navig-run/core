"""Deck API — web-search control plane (OS Settings → Personalization → Search engine).

``GET  /api/deck/search/status`` → the authoritative status document::

    {"enabled": bool, "provider": "deepseek_search"|"auto"|…,
     "providers": [{"id", "label", "has_key"}…], "cascade": [id…]}

``POST /api/deck/search/config`` {"provider": id} → validate against the shared
registry, then ``set_global("web.search.provider", canonical)``; responds with the
status read back, so the page renders what was actually stored. This is the ONE
UI write path for the engine — the same config key the CLI, agent tools, MCP and
Telegram read (``web.search.provider``).

``POST /api/deck/search/test`` {} → a real 3-result query through
``web_search()`` with caching off, reporting WHICH provider answered, latency and
result count. Disabled search returns ``{"ok": false, …}`` with HTTP 200 so the
button can render the reason.

``POST /api/deck/search`` {"query", "count"} → thin proxy for NAVIG OS's Pi agent
``web_search`` tool: one backend for every consumer. Kill switch
(``web.search.enabled``) → 403.

Keys never leave the vault: status exposes ``has_key`` booleans only.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_MAX_TEST_RESULTS = 3
_MAX_PROXY_RESULTS = 10


def _ok(data: Any, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


def _search_enabled() -> bool:
    from navig.core.coerce import coerce_bool
    from navig.tools.web import get_web_config

    cfg = get_web_config().get("search", {}) or {}
    return coerce_bool(cfg.get("enabled", True), default=True)


async def handle_deck_search_status(request: "web.Request") -> "web.Response":
    """GET /api/deck/search/status — active engine, key presence, cascade order."""
    from navig.tools.web import provider_status

    try:
        status = await asyncio.to_thread(provider_status)
    except Exception as exc:  # noqa: BLE001
        logger.error("search status failed: %s", exc)
        return _err(str(exc), 500)
    return _ok(status)


async def handle_deck_search_config(request: "web.Request") -> "web.Response":
    """POST /api/deck/search/config {"provider": id} — validate, write, read back."""
    from navig.tools.search_providers import (
        SEARCH_PROVIDER_LABELS,
        SUPPORTED_SEARCH_PROVIDERS,
    )
    from navig.tools.web import canonical_search_provider

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err("invalid JSON body", 400)

    raw = body.get("provider") if isinstance(body, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return _err("provider must be a non-empty string", 400)
    canonical = canonical_search_provider(raw)
    if canonical != "auto" and canonical not in SUPPORTED_SEARCH_PROVIDERS:
        allowed = ", ".join(["auto", *SUPPORTED_SEARCH_PROVIDERS])
        return _err(f"web search provider must be one of: {allowed}", 400)

    def _apply() -> dict[str, Any]:
        from navig.config import get_config_manager
        from navig.tools.web import provider_status

        get_config_manager().set_global("web.search.provider", canonical)
        return provider_status()

    try:
        status = await asyncio.to_thread(_apply)
    except Exception as exc:  # noqa: BLE001
        logger.warning("search provider save failed: %s", exc)
        return _err(f"Couldn't save: {exc}", 500)
    logger.info("web search provider set to %s (label: %s)",
                canonical, SEARCH_PROVIDER_LABELS.get(canonical, canonical))
    return _ok(status)


async def handle_deck_search_test(request: "web.Request") -> "web.Response":
    """POST /api/deck/search/test — live connection test; reports the answering engine."""
    if not _search_enabled():
        return _ok({
            "ok": False,
            "provider_used": None,
            "result_count": 0,
            "latency_ms": 0,
            "error": "Web search is disabled in configuration",
        })

    from navig.tools.web import web_search

    query = "NAVIG search connectivity test"

    def _run() -> tuple[Any, int]:
        start = time.perf_counter()
        result = web_search(
            query, count=_MAX_TEST_RESULTS, use_cache=False
        )
        return result, int((time.perf_counter() - start) * 1000)

    try:
        result, latency_ms = await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        logger.warning("search test failed: %s", exc)
        return _ok({
            "ok": False,
            "provider_used": None,
            "result_count": 0,
            "latency_ms": 0,
            "error": str(exc),
        })
    return _ok({
        "ok": bool(result.success),
        "provider_used": result.provider,
        "result_count": len(result.results or []),
        "latency_ms": latency_ms,
        **({} if result.success else {"error": result.error or "search failed"}),
    })


async def handle_deck_search(request: "web.Request") -> "web.Response":
    """POST /api/deck/search {"query", "count"} — the Pi agent's web-search proxy."""
    if not _search_enabled():
        return _err("Web search is disabled in configuration", 403)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err("invalid JSON body", 400)

    query = body.get("query") if isinstance(body, dict) else None
    if not isinstance(query, str) or not query.strip():
        return _err("query is required", 400)
    query = query.strip()
    try:
        count = int(body.get("count", 5)) if isinstance(body, dict) else 5
    except (TypeError, ValueError):
        count = 5
    count = max(1, min(count, _MAX_PROXY_RESULTS))

    from navig.tools.web import web_search

    try:
        result = await asyncio.to_thread(
            web_search, query=query, count=count, use_cache=False
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("deck search proxy failed: %s", exc)
        return _err(f"search failed: {exc}", 502)
    if not result.success:
        return _err(result.error or "search failed", 502)
    return _ok({
        "query": result.query,
        "provider": result.provider,
        "cached": bool(result.cached),
        "results": [
            {
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "age": r.age,
            }
            for r in (result.results or [])
        ],
    })
