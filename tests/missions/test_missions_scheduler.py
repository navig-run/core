"""MissionScheduler must retry an orphaned mission whose resume transiently failed.

The scheduler is the restart-recovery safety net: it re-drives QUEUED missions the
executor isn't handling. It marked a mission `_dispatched` on the ATTEMPT, so a submit
that transiently failed (a store flush / SSE emit blip) left the mission QUEUED yet
never retried this process lifetime — the exact orphan it exists to recover. It now
marks dispatched only on a SUCCESSFUL submit.
"""

from __future__ import annotations

from types import SimpleNamespace

from navig.missions.scheduler import MissionScheduler


class _FakeStore:
    def __init__(self, missions):
        self._missions = missions

    def list_missions(self, status=None, limit=100):
        return list(self._missions)


class _FakeExecutor:
    def __init__(self, missions):
        self.active: set[str] = set()
        self.store = _FakeStore(missions)
        self.submit_calls: list[str] = []
        self.fail_ids: set[str] = set()  # ids whose FIRST submit raises, then succeeds

    async def submit(self, m):
        mid = m.mission_id
        self.submit_calls.append(mid)
        if mid in self.fail_ids:
            self.fail_ids.discard(mid)
            raise RuntimeError("transient submit failure")
        self.active.add(mid)  # a real submit adds to active synchronously
        return m


def _mission(mid: str):
    return SimpleNamespace(mission_id=mid)


async def test_failed_resume_is_retried_next_sweep():
    m = _mission("mission-1")
    ex = _FakeExecutor([m])
    ex.fail_ids = {m.mission_id}
    sched = MissionScheduler(gateway=SimpleNamespace(), executor=ex)

    await sched._sweep()  # 1st sweep: submit RAISES
    assert ex.submit_calls == [m.mission_id]
    assert m.mission_id not in sched._dispatched  # NOT stranded (the fix)

    await sched._sweep()  # 2nd sweep: retried, submit succeeds
    assert ex.submit_calls == [m.mission_id, m.mission_id]
    assert m.mission_id in sched._dispatched


async def test_successful_resume_is_not_redispatched():
    m = _mission("mission-2")
    ex = _FakeExecutor([m])
    sched = MissionScheduler(gateway=SimpleNamespace(), executor=ex)

    await sched._sweep()  # submit succeeds
    assert ex.submit_calls == [m.mission_id]
    assert m.mission_id in sched._dispatched

    await sched._sweep()  # in active + dispatched → must NOT re-submit
    assert ex.submit_calls == [m.mission_id]


async def test_already_active_mission_is_skipped():
    m = _mission("mission-3")
    ex = _FakeExecutor([m])
    ex.active.add(m.mission_id)  # executor already handling it
    sched = MissionScheduler(gateway=SimpleNamespace(), executor=ex)

    await sched._sweep()
    assert ex.submit_calls == []  # never re-submitted while in-flight
