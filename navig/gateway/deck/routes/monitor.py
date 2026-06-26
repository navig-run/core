"""System monitoring handlers for the Deck API.

Wraps navig.commands.monitor (pure psutil collection) and exposes:
- GET /api/deck/monitor          — full snapshot (used by the Deck UI Monitor tab)
- GET /api/deck/monitor/disk     — individual section endpoints
- GET /api/deck/monitor/memory
- GET /api/deck/monitor/cpu
- GET /api/deck/monitor/uptime
- GET /api/deck/monitor/services
- GET /api/deck/monitor/ports
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

try:
    from aiohttp import web
except ImportError:
    web = None

from navig.commands import monitor

logger = logging.getLogger(__name__)


def _ok(data: object) -> "web.Response":
    return web.json_response({"ok": True, "data": data})


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


# ── Threaded + cached collection ─────────────────────────────────────────────
# psutil collection is *blocking* (a cold/network drive can stall disk reads for
# seconds). Calling it directly in an async handler froze the whole daemon event
# loop, stalling every other request. We instead (a) run it in a worker thread so
# the loop stays responsive, and (b) cache each result for a short TTL so the
# Deck's 30s polls and multiple concurrent consumers share one collection.
_TTL_SECONDS = 8.0
_cache: dict[str, tuple[float, object]] = {}
_locks: dict[str, asyncio.Lock] = {}


async def _cached(key: str, fn: Callable[[], object]) -> object:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < _TTL_SECONDS:
        return hit[1]
    # One in-flight collection per key — concurrent callers await the same result
    # instead of each spawning their own (possibly slow) scan.
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < _TTL_SECONDS:
            return hit[1]
        data = await asyncio.to_thread(fn)
        _cache[key] = (time.monotonic(), data)
        return data


async def handle_deck_monitor_all(request: "web.Request") -> "web.Response":
    try:
        return _ok(await _cached("all", monitor.get_all_monitoring))
    except Exception as exc:
        logger.exception("monitor snapshot failed")
        return _err(str(exc))


async def handle_deck_monitor_disk(request: "web.Request") -> "web.Response":
    try:
        return _ok({"disk": await _cached("disk", monitor.get_disk_info)})
    except Exception as exc:
        logger.exception("monitor/disk failed")
        return _err(str(exc))


async def handle_deck_monitor_memory(request: "web.Request") -> "web.Response":
    try:
        return _ok({"memory": await _cached("memory", monitor.get_memory_info)})
    except Exception as exc:
        logger.exception("monitor/memory failed")
        return _err(str(exc))


async def handle_deck_monitor_cpu(request: "web.Request") -> "web.Response":
    try:
        return _ok({"cpu": await _cached("cpu", monitor.get_cpu_info)})
    except Exception as exc:
        logger.exception("monitor/cpu failed")
        return _err(str(exc))


async def handle_deck_monitor_uptime(request: "web.Request") -> "web.Response":
    try:
        return _ok({"uptime": await _cached("uptime", monitor.get_uptime_info)})
    except Exception as exc:
        logger.exception("monitor/uptime failed")
        return _err(str(exc))


async def handle_deck_monitor_services(request: "web.Request") -> "web.Response":
    try:
        return _ok({"services": await _cached("services", monitor.get_services_info)})
    except Exception as exc:
        logger.exception("monitor/services failed")
        return _err(str(exc))


async def handle_deck_monitor_ports(request: "web.Request") -> "web.Response":
    try:
        return _ok({"ports": await _cached("ports", monitor.get_ports_info)})
    except Exception as exc:
        logger.exception("monitor/ports failed")
        return _err(str(exc))
