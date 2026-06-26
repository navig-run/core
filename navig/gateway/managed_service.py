"""navig.gateway.managed_service — uniform subsystem health for the gateway.

The gateway starts ~10 long-lived subsystems (cloud manager, mission scheduler,
heartbeat runner, cron service, channel health monitor, …). Historically each had
its own ad-hoc lifecycle and there was **no per-subsystem health** — a crashed
cloudflared tunnel left the gateway reporting "up" with no signal anywhere.

This module adds a small, non-invasive registry. Subsystems are registered as they
start; a generic ``probe_health`` inspects common shapes (a running asyncio task,
an ``is_running()``/``running`` flag) so existing classes need **no** changes. The
registry snapshot is surfaced via ``GET /health/services``.

Design goals: zero new dependencies, never throw into the hot path, and require no
interface changes from the subsystems it tracks.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Status vocabulary (kept as plain strings so it serialises trivially to JSON).
STATUS_UP = "up"
STATUS_DOWN = "down"
STATUS_DEGRADED = "degraded"
STATUS_UNKNOWN = "unknown"


def probe_health(instance: Any) -> tuple[str, str]:
    """Best-effort health of an arbitrary subsystem instance.

    Returns ``(status, detail)``. Never raises — an unprobeable object reports
    ``("up", "registered")`` rather than failing the whole snapshot.

    Heuristics, in order:
      1. ``None``                       → down (not started / torn down)
      2. a ``health()`` method          → trust it (dict or (status, detail))
      3. an asyncio ``Task`` attribute  → done/cancelled ⇒ down, else up
      4. a boolean ``running``/``is_running`` flag → up/down
      5. otherwise                      → up ("registered")
    """
    if instance is None:
        return STATUS_DOWN, "not started"
    try:
        # 2. Native health() hook (preferred when a subsystem grows one).
        health_fn = getattr(instance, "health", None)
        if callable(health_fn):
            result = health_fn()
            if isinstance(result, dict):
                return str(result.get("status", STATUS_UP)), str(result.get("detail", ""))
            if isinstance(result, tuple) and len(result) == 2:
                return str(result[0]), str(result[1])

        # 3. Background-task shape (HeartbeatRunner, health monitor, scheduler).
        for attr in ("_task", "task", "_run_task", "_loop_task"):
            task = getattr(instance, attr, None)
            if isinstance(task, asyncio.Task):
                if task.done():
                    exc = None if task.cancelled() else task.exception()
                    return STATUS_DOWN, f"task ended ({exc})" if exc else "task stopped"
                return STATUS_UP, "task running"

        # 4. Boolean flag shapes.
        for attr in ("is_running", "running", "_running", "is_connected"):
            flag = getattr(instance, attr, None)
            if callable(flag):
                try:
                    flag = flag()
                except Exception:  # noqa: BLE001
                    continue
            if isinstance(flag, bool):
                return (STATUS_UP, attr) if flag else (STATUS_DOWN, attr)
    except Exception as exc:  # noqa: BLE001
        return STATUS_UNKNOWN, f"probe error: {exc}"

    # 5. Present but opaque — assume up.
    return STATUS_UP, "registered"


@dataclass
class _Entry:
    name: str
    instance: Any
    probe: Callable[[Any], tuple[str, str]]


@dataclass
class ServiceRegistry:
    """Holds registered gateway subsystems and produces a health snapshot."""

    _entries: dict[str, _Entry] = field(default_factory=dict)

    def register(
        self,
        name: str,
        instance: Any,
        probe: Callable[[Any], tuple[str, str]] | None = None,
    ) -> None:
        """Register (or replace) a named subsystem. ``instance`` may be ``None``
        (reports ``down``) so callers can register unconditionally."""
        self._entries[name] = _Entry(name=name, instance=instance, probe=probe or probe_health)

    def unregister(self, name: str) -> None:
        self._entries.pop(name, None)

    def snapshot(self) -> dict[str, Any]:
        """Return ``{"status": <aggregate>, "services": {name: {...}}}``.

        Aggregate is ``up`` only when every registered service is up; ``degraded``
        if some are down/unknown but at least one is up; ``down`` if all are down.
        """
        services: dict[str, dict[str, str]] = {}
        for name, entry in self._entries.items():
            try:
                status, detail = entry.probe(entry.instance)
            except Exception as exc:  # noqa: BLE001
                status, detail = STATUS_UNKNOWN, f"probe raised: {exc}"
            services[name] = {"status": status, "detail": detail}

        statuses = [s["status"] for s in services.values()]
        if not statuses:
            aggregate = STATUS_UNKNOWN
        elif all(s == STATUS_UP for s in statuses):
            aggregate = STATUS_UP
        elif all(s == STATUS_DOWN for s in statuses):
            aggregate = STATUS_DOWN
        else:
            aggregate = STATUS_DEGRADED
        return {"status": aggregate, "services": services}
