"""GC-safe fire-and-forget background tasks.

``asyncio`` keeps only a **weak** reference to the result of ``create_task()`` /
``ensure_future()``. A background task whose reference the caller drops can be
garbage-collected at any time — *even before it runs* — silently losing whatever
it was going to do (establish a connection, forward an event, register a URL, send
a keepalive). That is a documented asyncio footgun and, in a long-lived daemon, a
real source of "it just never happened" bugs.

``spawn()`` is the canonical fix: it holds a strong reference until the task
finishes (then releases it, so the registry stays bounded) and logs any exception
the task raises — a bare ``create_task`` only emits an unhelpful "Task exception
was never retrieved" at GC time, if ever.

Use it for genuine fire-and-forget work. For a task you need to *cancel* later
(a supervised loop), keep the returned handle on your instance instead.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger("navig.tasks")

# Strong references to in-flight background tasks. add_done_callback removes each
# on completion, so this never grows without bound.
_bg_tasks: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task[Any]:
    """Schedule *coro* as a GC-safe background task.

    Holds a strong reference until the task completes, then releases it, and logs
    any exception the task raises instead of letting it vanish. Must be called from
    a thread that has a running event loop.
    """
    task = asyncio.ensure_future(coro)
    if name:
        try:
            task.set_name(name)
        except Exception:  # pragma: no cover — set_name is best-effort
            pass
    _bg_tasks.add(task)

    def _done(t: asyncio.Task[Any]) -> None:
        _bg_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.warning("background task %r failed: %r", t.get_name(), exc, exc_info=exc)

    task.add_done_callback(_done)
    return task
