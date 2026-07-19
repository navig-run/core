"""Regression: the TUI Scheduler + Task Queue badges read real state.

Both were silently dead — the same class as the mesh badge (#346):

- `resolve_scheduler` pointed CronService at `config_dir()`, but every other
  caller (deck schedule route, gateway, habit_store) stores cron jobs under
  `config_dir()/scheduler`. So it read a nonexistent `cron_jobs.json` and the badge
  always said "no jobs configured". It also read a phantom `CronJob.next_fire`
  attribute (the real field is `next_run`, a datetime), so the "next: …" hint never
  showed — and the `datetime - time.time()` math would have been a type error.

- `resolve_task_queue` called `q.pending_count()` — a method that does not exist
  (`size`/`total` are properties) — on a bare `TaskQueue()` that never loaded the
  daemon's persisted queue, so the badge was permanently "0 pending".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import navig.scheduler.cron_service as cron_mod
import navig.tasks.queue as tq_mod
from navig.tui.resolvers import resolve_scheduler, resolve_task_queue

# ── Scheduler ──────────────────────────────────────────────────────────────


@dataclass
class _Job:
    name: str
    next_run: datetime | None = None


class _FakeCron:
    """Stand-in for CronService — records the storage_path it was pointed at."""

    last_storage_path = None
    jobs: list = []

    def __init__(self, gateway=None, storage_path=None, config=None):
        _FakeCron.last_storage_path = storage_path

    def list_jobs(self):
        return list(_FakeCron.jobs)


def test_scheduler_reads_the_scheduler_subdir(monkeypatch):
    _FakeCron.jobs = [_Job("backup"), _Job("digest")]
    monkeypatch.setattr(cron_mod, "CronService", _FakeCron)

    badge = resolve_scheduler()

    # The path fix: must look under config_dir()/scheduler, not config_dir().
    assert str(_FakeCron.last_storage_path).replace("\\", "/").endswith("/scheduler")
    assert badge.status == "ok"
    assert "2 jobs" in badge.detail


def test_scheduler_no_jobs(monkeypatch):
    _FakeCron.jobs = []
    monkeypatch.setattr(cron_mod, "CronService", _FakeCron)

    badge = resolve_scheduler()

    assert badge.status == "missing"
    assert "no jobs" in badge.detail


def test_scheduler_shows_next_run(monkeypatch):
    _FakeCron.jobs = [_Job("digest", next_run=datetime.now() + timedelta(minutes=30))]
    monkeypatch.setattr(cron_mod, "CronService", _FakeCron)

    badge = resolve_scheduler()

    assert "next: digest" in badge.detail  # the next_run label (was dead via next_fire)


# ── Task Queue ─────────────────────────────────────────────────────────────


class _FakeQueue:
    """Stand-in for TaskQueue — records the persist_path and exposes size/total."""

    last_persist_path = None
    size_val = 0
    total_val = 0

    def __init__(self, persist_path=None):
        _FakeQueue.last_persist_path = persist_path

    @property
    def size(self):
        return _FakeQueue.size_val

    @property
    def total(self):
        return _FakeQueue.total_val


def test_task_queue_reads_persisted_file(monkeypatch):
    _FakeQueue.size_val, _FakeQueue.total_val = 3, 5
    monkeypatch.setattr(tq_mod, "TaskQueue", _FakeQueue)

    badge = resolve_task_queue()

    # The fix: must point at the daemon's task_queue.json, not a bare TaskQueue().
    assert str(_FakeQueue.last_persist_path).replace("\\", "/").endswith("/task_queue.json")
    assert "3 pending" in badge.detail
    assert "5 tracked" in badge.detail
    assert badge.status == "ok"


def test_task_queue_warns_when_deep(monkeypatch):
    _FakeQueue.size_val, _FakeQueue.total_val = 25, 25
    monkeypatch.setattr(tq_mod, "TaskQueue", _FakeQueue)

    badge = resolve_task_queue()

    assert badge.status == "warn"
    assert "25 pending" in badge.detail
