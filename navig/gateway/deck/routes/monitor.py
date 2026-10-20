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
# A reading older than the TTL is still served at once, as long as it is younger than this,
# while a refresh runs behind it. A collection can be slow: the CPU section's top-process
# walk took 6–11 s inside the daemon (0.12 s from a shell). psutil times every process,
# and for each process the daemon's token may not open, it falls back to a snapshot of
# the WHOLE process table (`_pswindows.cpu_times` → `_proc_info`), seen in a py-spy dump.
# The dashboard polls less often than the TTL, so before this every poll was cold. On a
# linked NAVIG OS that cold poll plus the relay crossed the 15 s proxy ceiling and the
# dashboard read "No brain connected".
_STALE_SERVE_SECONDS = 300.0
_cache: dict[str, tuple[float, object]] = {}
_locks: dict[str, asyncio.Lock] = {}
_refreshing: set[str] = set()


async def _collect(key: str, fn: Callable[[], object]) -> object:
    # One in-flight collection per key: concurrent callers await the same result
    # instead of each spawning their own (possibly slow) scan.
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < _TTL_SECONDS:
            return hit[1]
        data = await asyncio.to_thread(fn)
        _cache[key] = (time.monotonic(), data)
        return data


def _refresh_behind(key: str, fn: Callable[[], object]) -> None:
    if key in _refreshing:
        return
    _refreshing.add(key)

    async def _run() -> None:
        try:
            await _collect(key, fn)
        finally:
            _refreshing.discard(key)

    from navig.core.background import spawn  # noqa: PLC0415

    spawn(_run(), name=f"deck-monitor-refresh:{key}")


async def _cached(key: str, fn: Callable[[], object]) -> object:
    hit = _cache.get(key)
    if hit is not None:
        age = time.monotonic() - hit[0]
        if age < _TTL_SECONDS:
            return hit[1]
        if age < _STALE_SERVE_SECONDS:
            _refresh_behind(key, fn)
            return hit[1]
    return await _collect(key, fn)


async def warm_monitor_cache(_app: object = None) -> None:
    """Collect the dashboard snapshot once at startup, so the first poll is not cold.

    Registered as an aiohttp ``on_startup`` hook; runs behind the start, never blocks it.
    """
    _refresh_behind("all", monitor.get_all_monitoring)


async def handle_deck_monitor_all(request: "web.Request") -> "web.Response":
    try:
        # ?scope=vitals → cpu/mem/disk/uptime only, for the dashboards' 5s poll:
        # services + listening ports are the expensive half and no dashboard shows
        # them. Its own cache key, so it can never serve (or be served by) the full
        # snapshot.
        if request.query.get("scope") == "vitals":
            return _ok(await _cached("vitals", monitor.get_vitals))
        # ?services=all → every installed service with its real status (the
        # System app's "Show all services" setting). Separate cache key — the
        # two variants must not serve each other's payload.
        if request.query.get("services") == "all":
            return _ok(await _cached(
                "all_full", lambda: monitor.get_all_monitoring(all_services=True),
            ))
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
        if request.query.get("all") in ("1", "true", "all"):
            return _ok({"services": await _cached(
                "services_all",
                lambda: monitor.get_services_info(max_services=1000, include_stopped=True),
            )})
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
