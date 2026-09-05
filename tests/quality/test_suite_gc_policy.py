"""The per-test GC drain must stay cheap — it was 46% of the suite's wall clock.

`conftest._drain_orphan_subprocesses()` runs from an autouse fixture BEFORE AND AFTER every
test. With 27,456 tests that is ~55,000 collections of a heap holding the whole collected
suite, and it used to ask for a FULL gen-2 sweep every time.

Measured on tests/config (219 tests), idle, back-to-back:

    full gc.collect()   61.7s
    gc.collect(0)       33.0s
    no gc at all        31.5s     <- so gen-0 recovers ~95% of what is available
    amortised (this)    35.1s

The collection is not decoration: it runs `__del__` on unreferenced Popen handles so
`subprocess._cleanup()` can reap them, and an unraisable warning does not leak into the next
test. A young handle lives in gen-0/1, but automatic GC during a long test can promote it,
and gen-2 is only examined by a full sweep — so the policy is "cheap every time, full
occasionally", which bounds how long anything can linger while amortising the cost away.
"""

from __future__ import annotations

import gc

import pytest

from tests import conftest as ct


@pytest.fixture
def generations(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the generation asked for by each drain, without really collecting."""
    seen: list[int] = []

    def _record(generation: int = 2) -> int:
        seen.append(generation)
        return 0

    monkeypatch.setattr(ct.gc, "collect", _record)
    monkeypatch.setattr(ct, "_drain_count", 0)
    return seen


def test_the_common_case_is_a_cheap_collection(generations):
    """THE REGRESSION: an unconditional full sweep here cost 46% of the run."""
    for _ in range(10):
        ct._drain_orphan_subprocesses()

    assert generations, "the drain stopped collecting entirely"
    assert all(g < 2 for g in generations), (
        f"a full gen-2 sweep ran on a normal drain: {generations}"
    )


def test_a_full_sweep_still_happens_periodically(generations):
    """Anti-vacuity: 'never collect gen-2' would let a promoted handle linger forever."""
    for _ in range(ct._FULL_GC_EVERY * 2):
        ct._drain_orphan_subprocesses()

    full = [i for i, g in enumerate(generations, start=1) if g == 2]
    assert len(full) == 2, f"expected 2 full sweeps in {ct._FULL_GC_EVERY * 2} drains, got {full}"
    assert full == [ct._FULL_GC_EVERY, ct._FULL_GC_EVERY * 2], f"full sweeps at odd points: {full}"


def test_the_interval_is_bounded(generations):
    """A huge interval would be the same as never doing a full sweep."""
    assert 1 < ct._FULL_GC_EVERY <= 200, (
        f"_FULL_GC_EVERY={ct._FULL_GC_EVERY} is outside a sane amortisation window"
    )


def test_the_drain_still_reaps_subprocess_handles(generations):
    """The purpose survives: `subprocess._cleanup()` is still called every drain."""
    calls = {"n": 0}

    def _cleanup() -> None:
        calls["n"] += 1

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ct.subprocess, "_cleanup", _cleanup)
        ct._drain_orphan_subprocesses()

    assert calls["n"] == 1, "the drain stopped reaping subprocess handles"


def test_a_real_collection_actually_runs():
    """Not just the recorder: unpatched, the drain must really collect garbage.

    Without this the suite above would pass against a drain that collects nothing.
    """

    class Cycle:
        def __init__(self) -> None:
            self.self_ref = self

    before = gc.collect()  # settle
    del before
    obj = Cycle()
    tracked = id(obj)
    del obj  # unreachable, but in a reference cycle -> only the collector frees it

    ct._drain_orphan_subprocesses()

    assert not any(id(o) == tracked for o in gc.get_objects()), (
        "the drain did not actually collect an unreachable cycle"
    )
