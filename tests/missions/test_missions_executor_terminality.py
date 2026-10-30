"""The executor must not crash when a mission finished while it was waiting.

`advance_mission` (the deck/CLI route) drives the same state machine as the executor,
so a mission can reach a terminal state out of band — an operator answering a stuck
approval by marking it succeeded, or a second actor completing it. The approval and
verification branches then called `mission.cancel()` unconditionally, and the contract
raises `Invalid transition 'succeeded' → 'cancelled'`. On 2026-10-28 that crashed the
executor four times on outpost for three brain readiness missions whose prompts had
expired (07:12:09 / 07:13:13 / 07:13:38), so the deny never finished cleanly.

A terminal state is final — the receipt and the event already exist. These tests pin
both halves: the race is a no-op, and the ordinary deny still cancels and records.
"""

from __future__ import annotations

from types import SimpleNamespace

from navig.contracts.mission import Mission, MissionStatus
from navig.contracts.store import RuntimeStore
from navig.missions.executor import MissionExecutor


def _executor(tmp_path):
    return MissionExecutor(gateway=SimpleNamespace(), store=RuntimeStore(store_dir=tmp_path))


async def _deny(_mission):
    return False


async def test_a_mission_finished_during_the_approval_wait_is_not_cancelled(tmp_path):
    """The outpost crash: succeeded out of band, then the 900 s timeout denied."""
    ex = _executor(tmp_path)
    mission = Mission(title="t", capability="readiness.probe", metadata={"autonomy": "approval"})
    ex.store.create_mission(mission)
    # Somebody else completed it while the prompt was pending.
    mission.start()
    mission.succeed("reported")
    ex.store.record_receipt_from_mission(mission)
    ex.store.flush()
    receipts_before = len(ex.store.list_receipts())

    ex._request_approval = _deny  # type: ignore[method-assign]
    result = await ex._execute(mission)

    assert result.status == MissionStatus.SUCCEEDED, "a terminal state must stay final"
    assert result.result == "reported"
    assert len(ex.store.list_receipts()) == receipts_before, "no second receipt for a finished mission"
    assert mission.mission_id not in ex.active


async def test_the_ordinary_deny_still_cancels_and_records_a_receipt(tmp_path):
    """The fix must not weaken the fail-closed path it guards."""
    ex = _executor(tmp_path)
    mission = Mission(title="t", capability="readiness.probe", metadata={"autonomy": "approval"})
    ex.store.create_mission(mission)

    ex._request_approval = _deny  # type: ignore[method-assign]
    result = await ex._execute(mission)

    assert result.status == MissionStatus.CANCELLED
    assert "approval denied" in (result.error or "")
    assert len(ex.store.list_receipts()) == 1
    assert mission.mission_id not in ex.active
