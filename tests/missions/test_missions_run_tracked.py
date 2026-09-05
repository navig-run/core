"""``MissionExecutor.run_tracked`` — the board's tracked-run entry point.

Two gaps this covers, both sibling-asymmetries with ``_execute``:

1. **Stranding.** ``_execute`` finalizes a still-non-terminal mission when the run-setup
   crashes (#685); ``run_tracked`` did not. Its ``ok()``/``summary()`` callbacks are
   CALLER-supplied — the board passes ``lambda r: bool(r) and r.get("agent_status") != "failed"``,
   which raises ``AttributeError`` on any truthy non-dict result — so a raise there left the
   mission RUNNING forever with no ExecutionReceipt.
2. **Timeout never enforced.** ``run_tracked`` writes ``timeout_secs`` onto the Mission but ran
   the callable unbounded, so a hung runner held a slot of the shared ``_sem`` (default 3)
   forever — and ``_execute`` shares that semaphore, so a few hung cards wedged all mission
   execution.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from navig.contracts.mission import MissionStatus
from navig.contracts.store import RuntimeStore
from navig.missions.executor import MissionExecutor


def _executor(tmp_path) -> tuple[MissionExecutor, RuntimeStore]:
    store = RuntimeStore(store_dir=tmp_path)
    return MissionExecutor(gateway=SimpleNamespace(), store=store), store


async def test_raising_ok_callback_finalizes_instead_of_stranding(tmp_path):
    """The board's real predicate shape: a truthy NON-dict result makes `r.get` raise."""
    ex, store = _executor(tmp_path)

    with pytest.raises(AttributeError):
        await ex.run_tracked(
            title="card",
            capability="board_card",
            fn=lambda: "a truthy string, not a dict",
            ok=lambda r: bool(r) and r.get("agent_status") != "failed",  # raises
        )

    (mission,) = store.list_missions()
    # Pre-fix: still RUNNING, zero receipts, still in `active`.
    assert mission.is_terminal
    assert mission.status == MissionStatus.FAILED
    assert "tracked run crashed" in (mission.error or "")
    assert len(store.list_receipts()) == 1
    assert mission.mission_id not in ex.active


async def test_raising_summary_callback_finalizes(tmp_path):
    """``summary()`` runs after the success decision — a raise there must not strand it."""
    ex, store = _executor(tmp_path)

    def _bad_summary(_r):
        raise ValueError("bad summary")

    with pytest.raises(ValueError):
        await ex.run_tracked(
            title="card", capability="board_card", fn=lambda: {"ok": True}, summary=_bad_summary
        )

    (mission,) = store.list_missions()
    assert mission.status == MissionStatus.FAILED
    assert len(store.list_receipts()) == 1


async def test_timeout_is_enforced_and_releases_the_semaphore(tmp_path):
    """A hung runner must time out, record a TIMED_OUT receipt, and free the slot."""
    ex, store = _executor(tmp_path)
    ex.default_timeout = 0.05

    release = threading.Event()

    def _hang():
        # Short backstop: the worker thread can never outlive the test even if the
        # release below is skipped (a leaked non-daemon thread hangs loop shutdown).
        release.wait(5)
        return {"late": True}

    try:
        with pytest.raises(asyncio.TimeoutError):
            await ex.run_tracked(title="hung card", capability="board_card", fn=_hang)
    finally:
        release.set()  # let the worker finish INSIDE the loop, before it closes

    (mission,) = store.list_missions()
    assert mission.status == MissionStatus.TIMED_OUT
    assert len(store.list_receipts()) == 1
    assert mission.mission_id not in ex.active
    # The slot was returned — a later run still proceeds (pre-fix it was held forever).
    assert ex._sem._value >= 1


async def test_successful_run_is_unaffected(tmp_path):
    """The happy path keeps its contract: returns the result, succeeds, records a receipt."""
    ex, store = _executor(tmp_path)

    result = await ex.run_tracked(
        title="card",
        capability="board_card",
        fn=lambda c: {"agent_status": "done", "n": c},
        args=(7,),
        ok=lambda r: r.get("agent_status") != "failed",
        summary=lambda r: f"n={r['n']}",
    )

    assert result == {"agent_status": "done", "n": 7}
    (mission,) = store.list_missions()
    assert mission.status == MissionStatus.SUCCEEDED
    assert mission.result == "n=7"
    assert len(store.list_receipts()) == 1


async def test_runner_failure_still_reports_failed(tmp_path):
    """A runner that raises still fails the mission (unchanged) and re-raises."""
    ex, store = _executor(tmp_path)

    def _boom():
        raise RuntimeError("runner blew up")

    with pytest.raises(RuntimeError):
        await ex.run_tracked(title="card", capability="board_card", fn=_boom)

    (mission,) = store.list_missions()
    assert mission.status == MissionStatus.FAILED
    assert "runner blew up" in (mission.error or "")
    assert len(store.list_receipts()) == 1


async def test_ok_false_marks_failed_without_raising(tmp_path):
    """`ok()` returning False is a reported failure, not an exception."""
    ex, store = _executor(tmp_path)

    result = await ex.run_tracked(
        title="card", capability="board_card", fn=lambda: {"agent_status": "failed"},
        ok=lambda r: r.get("agent_status") != "failed",
    )

    assert result == {"agent_status": "failed"}
    (mission,) = store.list_missions()
    assert mission.status == MissionStatus.FAILED
    assert "run reported failure" in (mission.error or "")
