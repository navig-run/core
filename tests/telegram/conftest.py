"""Shared isolation for the Telegram suite.

`navig.telegram.tiktok_actions` keeps two pieces of module-level state — the
in-flight set that stops a double-tap starting a second download, and the clip
cache that stops two different actions re-fetching the same video. Both are
process-lifetime by design, which is right in the daemon and wrong in a test
process: a clip cached by one test satisfies the next test's fetch, so a case
that patched the download to FAIL passes anyway, having exercised nothing.

That is not hypothetical — it is exactly what happened when the cache landed:
five tests across three files went green or red for reasons that had nothing to
do with what they were asserting.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_tiktok_module_state():
    """Clear the in-flight set and the clip cache around every test."""
    try:
        from navig.telegram import tiktok_actions
    except Exception:  # noqa: BLE001 — navig-download absent: nothing to isolate
        yield
        return

    def _clear() -> None:
        tiktok_actions._IN_FLIGHT.clear()
        tiktok_actions._cache.clear()
        tiktok_actions._cache_locks.clear()

    _clear()
    yield
    _clear()
