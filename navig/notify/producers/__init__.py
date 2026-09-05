"""First-party notification producers — in-daemon code that emits events into the
existing notify fan-out (deck + Telegram), so NAVIG can tell you about *itself*
(its own errors, deploys, …) without an external website firing a Signal.

Each producer is just another caller of ``notify.dispatch`` — no new delivery
path. They land in the **NAVIG** category, mutable per-theme like everything else.
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine

# ── GC-safe fire-and-forget ──────────────────────────────────────────────────
# asyncio keeps only a WEAK reference to the result of create_task()/
# ensure_future(). A producer that schedules its push and drops the reference
# lets the task be garbage-collected before it runs — silently losing the very
# alert this area exists to deliver (a config incident, a self-error, a
# connectivity flap, a deploy). spawn() holds a strong reference until the task
# completes, then releases it. Call it from the loop thread.
_bg_tasks: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Schedule *coro* as a background task and keep it alive until it finishes."""
    task = asyncio.ensure_future(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task
