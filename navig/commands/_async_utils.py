"""Shared async-to-sync bridge for Typer CLI commands."""
from __future__ import annotations

import asyncio


def run_sync(coro):
    """Run an async coroutine from a synchronous Typer context.

    Handles the edge case where a running event loop already exists (e.g.
    during pytest with anyio) by offloading execution to a ThreadPoolExecutor.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)
