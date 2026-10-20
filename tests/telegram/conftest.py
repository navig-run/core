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


#: Far past anything the in-memory fakes can take, and still finite — a hang
#: should fail the suite, not sit forever.
_SUITE_TIMEOUT_S = 600.0


@pytest.fixture(autouse=True)
def _outcomes_do_not_depend_on_machine_load(monkeypatch):
    """Stop a busy machine from answering as though TikTok had been slow.

    `offer_card` and `_do_full_text` bound their metadata read with
    `asyncio.wait_for(..., _INFO_TIMEOUT)` — 12s, sized for a live fetch of
    tiktok.com. Here the fetch is an in-memory fake, so the timeout ought to be
    unreachable. It is not: the same test measures 1.2s alone and 3.6s in-file
    (0.64s of it a lazy import inside `engine.info`), and the gate runs ~20 xdist
    workers on a machine that is also compiling. Twice, the identical commit went
    red and then green on re-run — the signature of a load-sensitive assertion
    rather than a defect.

    Worth naming because **neither failure looks like a timeout**. A timed-out
    `offer_card` yields `meta=None`, so `_card_overflows` is False and the card
    silently drops its 📄 Full text button; the test then reads
    `assert "tx" in {...}` with nothing about time in it. Verified by forcing
    `_INFO_TIMEOUT = 0.001`, which reproduces both observed failures character
    for character.

    This is not "raise the timeout until it passes": the bound exists to cap a
    NETWORK call, there is no network here, and the value is what the suite was
    accidentally measuring instead of what it meant to assert. A test that means
    to exercise the timeout still sets its own (`test_tiktok_card_dm` uses 0.05)
    — that monkeypatch runs inside the test body, after this fixture, so it wins.
    """
    try:
        from navig.telegram import tiktok_actions
    except Exception:  # noqa: BLE001 — navig-download absent: nothing to neutralise
        return
    for name in ("_INFO_TIMEOUT", "_COVER_TIMEOUT"):
        if isinstance(getattr(tiktok_actions, name, None), (int, float)):
            monkeypatch.setattr(tiktok_actions, name, _SUITE_TIMEOUT_S)


@pytest.fixture(autouse=True)
def _isolate_deletion_state_files(tmp_path, monkeypatch):
    """Per-test files for the two small deletion registries.

    `deletions` notes the business messages NAVIG deletes itself (so its own
    trigger words stay out of the digest) and the messages a Show posted (so they
    can be cleared). Both live in the cache dir, which is shared across a test
    session — so a test that ran the real role delete with connection `bc1` noted
    `bc1:1`, and a LATER test's genuine deletion of message 1 was skipped as
    "ours". Three business-flow tests failed that way, and only in a full run.
    """
    try:
        from navig.telegram import deletions
    except Exception:  # noqa: BLE001
        return
    monkeypatch.setattr(deletions, "_own_deletions_path", lambda: tmp_path / "own_deletions.json")
    monkeypatch.setattr(deletions, "_reveals_path", lambda: tmp_path / "deletion_reveals.json")
