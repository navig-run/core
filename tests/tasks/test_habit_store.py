"""
Tests for navig.scheduler.habit_store — the live-store access layer that fixes
the dead ``~/.navig/daemon/cron_jobs.json`` split (habits were written to a
store the scheduler never executes).

Resolution order under test: in-process live service → gateway HTTP →
detached CronService over the live store (+ one-time legacy migration).
"""

from __future__ import annotations

import json

import pytest

from navig.scheduler import cron_service as cs
from navig.scheduler import habit_store


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch):
    """Point both stores at tmp_path and force the offline (no-HTTP) path."""
    live_dir = tmp_path / "scheduler"
    legacy = tmp_path / "daemon" / "cron_jobs.json"
    monkeypatch.setattr(habit_store, "live_store_dir", lambda: live_dir)
    monkeypatch.setattr(habit_store, "legacy_store_path", lambda: legacy)
    monkeypatch.setattr(habit_store, "_gateway_json", lambda *a, **k: None)
    # No live service unless a test registers one.
    monkeypatch.setattr(cs, "_LIVE_SERVICE", None)
    yield


def _write_legacy(jobs: list[dict], counter: int = 0) -> None:
    p = habit_store.legacy_store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"counter": counter, "jobs": jobs}), encoding="utf-8")


def _read_live() -> dict:
    return json.loads(habit_store.live_store_path().read_text(encoding="utf-8"))


def _job(name: str, jid: str = "job_1", schedule: str = "0 7 * * 1-5") -> dict:
    return {
        "id": jid,
        "name": name,
        "schedule": schedule,
        "command": f"NAVIG_HABIT_REMINDER:1:{name}",
        "enabled": True,
        "timeout_seconds": 30,
    }


# ─── Legacy migration ─────────────────────────────────────────────────────────


def test_migrate_moves_stranded_jobs_and_renames_legacy():
    _write_legacy([_job("habit:workout"), _job("habit:water", "job_2")], counter=2)

    migrated = habit_store.migrate_legacy_store()

    assert migrated == 2
    live = _read_live()
    assert {j["name"] for j in live["jobs"]} == {"habit:workout", "habit:water"}
    # Fresh sequential ids from the live counter.
    assert {j["id"] for j in live["jobs"]} == {"job_1", "job_2"}
    assert not habit_store.legacy_store_path().exists()
    migrated_marker = habit_store.legacy_store_path().with_suffix(".json.migrated")
    assert migrated_marker.exists()


def test_migrate_skips_name_collisions_and_is_idempotent():
    live_dir = habit_store.live_store_dir()
    live_dir.mkdir(parents=True, exist_ok=True)
    habit_store.live_store_path().write_text(
        json.dumps({"counter": 5, "jobs": [_job("habit:workout", "job_5")]}),
        encoding="utf-8",
    )
    _write_legacy([_job("habit:workout"), _job("habit:sleep", "job_2")])

    assert habit_store.migrate_legacy_store() == 1
    live = _read_live()
    names = sorted(j["name"] for j in live["jobs"])
    assert names == ["habit:sleep", "habit:workout"]
    # Existing live job untouched; migrated job got a fresh id above the counter.
    assert any(j["id"] == "job_5" and j["name"] == "habit:workout" for j in live["jobs"])
    assert any(j["id"] == "job_6" and j["name"] == "habit:sleep" for j in live["jobs"])

    # Second run: legacy file is gone — nothing to do.
    assert habit_store.migrate_legacy_store() == 0


def test_migrate_without_legacy_store_is_noop():
    assert habit_store.migrate_legacy_store() == 0
    assert not habit_store.live_store_path().exists()


# ─── Detached fallback (daemon off) ───────────────────────────────────────────


def test_create_job_offline_writes_live_store():
    job = habit_store.create_job(
        "habit:workout", "0 7 * * 1-5", "NAVIG_HABIT_REMINDER:1:x", timeout_seconds=30
    )

    assert job["name"] == "habit:workout"
    assert job["id"]
    live = _read_live()
    assert [j["name"] for j in live["jobs"]] == ["habit:workout"]
    # A CronService over the same dir sees it (the daemon-restart contract).
    svc = cs.CronService(None, habit_store.live_store_dir())
    assert [j.name for j in svc.jobs.values()] == ["habit:workout"]


def test_create_job_replace_true_replaces_same_name():
    habit_store.create_job("habit:workout", "0 7 * * 1-5", "old")
    habit_store.create_job("habit:workout", "0 9 * * 1-5", "new", replace=True)

    jobs = habit_store.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["schedule"] == "0 9 * * 1-5"
    assert jobs[0]["command"] == "new"


def test_delete_jobs_by_name_and_by_id():
    habit_store.create_job("habit:workout", "0 7 * * 1-5", "x")
    kept = habit_store.create_job("habit:water", "0 */2 * * *", "y")

    assert habit_store.delete_jobs("habit:workout") == 1
    assert habit_store.delete_jobs("habit:workout") == 0
    assert habit_store.delete_jobs(kept["id"]) == 1
    assert habit_store.list_jobs() == []


def test_offline_create_adopts_legacy_jobs_first():
    """The detached fallback migrates before writing — no stranded jobs left."""
    _write_legacy([_job("habit:sleep")])

    habit_store.create_job("habit:workout", "0 7 * * 1-5", "x")

    names = sorted(j["name"] for j in habit_store.list_jobs())
    assert names == ["habit:sleep", "habit:workout"]
    assert not habit_store.legacy_store_path().exists()


def test_list_habit_jobs_filters_prefix():
    habit_store.create_job("habit:workout", "0 7 * * 1-5", "x")
    habit_store.create_job("backup nightly", "0 3 * * *", "navig backup run")

    habits = habit_store.list_habit_jobs()
    assert [j["name"] for j in habits] == ["habit:workout"]


# ─── Live-service preference (in-gateway process) ─────────────────────────────


def test_live_service_used_when_registered(monkeypatch):
    svc = cs.CronService(None, habit_store.live_store_dir())
    monkeypatch.setattr(cs, "_LIVE_SERVICE", svc)

    # HTTP must never be attempted when the live service exists.
    def _boom(*a, **k):  # pragma: no cover — the assertion is that it's unused
        raise AssertionError("HTTP path must not be used with a live service")

    monkeypatch.setattr(habit_store, "_gateway_json", _boom)

    job = habit_store.create_job("habit:workout", "0 7 * * 1-5", "x")
    assert job["name"] == "habit:workout"
    assert [j["name"] for j in habit_store.list_jobs()] == ["habit:workout"]
    assert habit_store.delete_jobs("habit:workout") == 1


def test_gateway_attached_service_registers_and_migrates(monkeypatch):
    """A gateway-attached CronService adopts legacy jobs at startup."""

    class _FakeGateway:
        pass

    _write_legacy([_job("habit:water")])
    svc = cs.CronService(_FakeGateway(), habit_store.live_store_dir())

    assert cs.get_live_service() is svc
    assert [j.name for j in svc.jobs.values()] == ["habit:water"]
    assert not habit_store.legacy_store_path().exists()


# ─── HTTP-first path (CLI while the daemon runs) ──────────────────────────────


def test_http_path_used_when_gateway_reachable(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_gateway_json(method, path, body=None):
        calls.append((method, path))
        if method == "GET" and path.endswith("/crons"):
            return {"jobs": [dict(_job("habit:workout"), id="job_9")]}
        if method == "POST" and path.endswith("/crons"):
            return {"job": dict(body, id="job_10")}
        if method == "DELETE":
            return {"deleted": True}
        return None

    monkeypatch.setattr(habit_store, "_gateway_json", fake_gateway_json)

    jobs = habit_store.list_jobs()
    assert jobs[0]["id"] == "job_9"

    created = habit_store.create_job("habit:sleep", "0 22 * * *", "z")
    assert created["id"] == "job_10"

    assert habit_store.delete_jobs("habit:workout") == 1
    # Nothing was written locally — the daemon owns the store.
    assert not habit_store.live_store_path().exists()
    assert any(m == "DELETE" for m, _ in calls)
