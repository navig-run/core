"""MissionExecutor must not leave a mission stuck RUNNING when the run-setup crashes.

`_execute`'s outer `except` used to only LOG. If a store flush / SSE emit raised in the
window after the mission went RUNNING but before the runner recorded a terminal state,
the mission hung RUNNING forever with no ExecutionReceipt (the Deck shows it live
indefinitely; the audit trail is missing). It now finalizes a still-non-terminal
mission as failed + records its receipt.
"""

from __future__ import annotations

from types import SimpleNamespace

from navig.contracts.mission import Mission, MissionStatus
from navig.contracts.store import RuntimeStore
from navig.missions.executor import MissionExecutor


async def test_crash_in_running_setup_finalizes_the_mission(tmp_path):
    store = RuntimeStore(store_dir=tmp_path)
    ex = MissionExecutor(gateway=SimpleNamespace(), store=store)

    mission = Mission(title="t", capability="llm", metadata={"autonomy": "draft"})
    store.create_mission(mission)

    # Crash the store flush so the failure lands in the running-setup window (after
    # mission.start() flips RUNNING, before the runner records a terminal state).
    def _boom():
        raise RuntimeError("disk full")

    store.flush = _boom  # type: ignore[method-assign]

    result = await ex._execute(mission)

    # Without the fix: still RUNNING, no receipt. With it: failed + a receipt, released.
    assert result.is_terminal
    assert result.status == MissionStatus.FAILED
    assert "executor crashed" in (result.error or "")
    assert len(store.list_receipts()) == 1
    assert mission.mission_id not in ex.active


async def test_normal_draft_run_is_unaffected(tmp_path, monkeypatch):
    """The fix only touches the crash path — a normal run still completes on its own."""
    store = RuntimeStore(store_dir=tmp_path)
    ex = MissionExecutor(gateway=SimpleNamespace(), store=store)
    mission = Mission(title="t", capability="llm", metadata={"autonomy": "draft"})
    store.create_mission(mission)

    async def _fake_draft(m):
        return "drafted"

    monkeypatch.setattr(ex, "_run_draft", _fake_draft)

    result = await ex._execute(mission)
    assert result.status == MissionStatus.SUCCEEDED
    assert len(store.list_receipts()) == 1
