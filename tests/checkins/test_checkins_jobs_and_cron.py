"""The legacy-job migration (fixture store only) and NAVIG_CHECKIN in the scheduler.

⚠ Never point any of this at the operator's real ``scheduler/cron_jobs.json``:
every store here is a tmp file shaped like the live one.
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

PARIS = ZoneInfo("Europe/Paris")
CHAT = 4242
SPACE = r"C:\Users\op\spaces\human-health-space"


def _live_shaped_store(path):
    """The two jobs as they exist on the operator's machine, plus an unrelated one."""
    data = {
        "counter": 12,
        "jobs": [
            {
                "id": "job_7",
                "name": "habit:weigh",
                "schedule": "10 8 * * *",
                "command": f'navig body checkin --send --space "{SPACE}"',
                "enabled": True,
                "timeout_seconds": 60,
                "retry_count": 1,
                "max_retries": 3,
                "last_run": "2026-10-08T08:10:02",
                "next_run": "2026-10-09T08:10:00",
                "last_status": "success",
                "last_output": "Weigh-in sent",
                "created_at": "2026-09-01T10:00:00",
            },
            {
                "id": "job_9",
                "name": "habit:bodyreview",
                "schedule": "45 18 * * 0",
                "command": f'navig body checkin --weekly --send --space "{SPACE}"',
                "enabled": True,
                "timeout_seconds": 60,
                "retry_count": 0,
                "max_retries": 3,
                "last_run": None,
                "next_run": "2026-10-11T18:45:00",
                "last_status": None,
                "last_output": None,
                "created_at": "2026-09-01T10:00:00",
            },
            {
                "id": "job_3",
                "name": "habit:ship",
                "schedule": "0 21 * * *",
                "command": "NAVIG_HABIT_REMINDER:4242:U2hpcA==",
                "enabled": True,
                "timeout_seconds": 30,
                "retry_count": 0,
                "max_retries": 3,
                "created_at": "2026-09-01T10:00:00",
            },
        ],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def test_migration_dry_run_plans_and_touches_nothing(ci_env, tmp_path):
    from navig.checkins import jobs

    store = tmp_path / "cron_jobs.json"
    _live_shaped_store(store)
    before = store.read_bytes()
    plan = jobs.migrate_legacy_jobs(store, dry_run=True, default_chat=CHAT, persist_space=False)
    assert store.read_bytes() == before
    assert plan["backup"] is None and plan["dry_run"] is True
    assert [c["to"]["name"] for c in plan["changes"]] == ["checkin:weigh", "checkin:weigh:weekly"]
    assert [c["to"]["command"] for c in plan["changes"]] == [
        f"NAVIG_CHECKIN:weigh:{CHAT}",
        f"NAVIG_CHECKIN:weigh:{CHAT}:weekly",
    ]
    assert {c["to"]["timeout_seconds"] for c in plan["changes"]} == {180}
    assert plan["spaces"] == [SPACE]
    assert not list(tmp_path.glob("cron_jobs.json.bak-*"))


def test_migration_writes_a_backup_first_and_is_idempotent(ci_env, tmp_path):
    from navig.checkins import jobs

    store = tmp_path / "cron_jobs.json"
    original = _live_shaped_store(store)
    raw = store.read_bytes()
    out = jobs.migrate_legacy_jobs(
        store, default_chat=CHAT, persist_space=False, now=datetime(2026, 10, 8, 12, 0, 0)
    )
    backup = tmp_path / "cron_jobs.json.bak-20261008T120000"
    assert out["backup"] == str(backup) and backup.read_bytes() == raw
    data = json.loads(store.read_text(encoding="utf-8"))
    by_id = {j["id"]: j for j in data["jobs"]}
    assert by_id["job_7"]["name"] == "checkin:weigh"
    assert by_id["job_7"]["schedule"] == "10 8 * * *"  # same schedule
    assert by_id["job_7"]["enabled"] is True  # same on/off
    assert by_id["job_7"]["timeout_seconds"] == 180
    assert by_id["job_7"]["last_run"] == "2026-10-08T08:10:02"  # history kept
    assert by_id["job_9"]["command"].endswith(":weekly")
    assert by_id["job_3"] == original["jobs"][2], "unrelated jobs untouched"
    assert data["counter"] == 12

    again = jobs.migrate_legacy_jobs(
        store, default_chat=CHAT, persist_space=False, now=datetime(2026, 10, 8, 12, 5, 0)
    )
    assert again["changes"] == [] and again["backup"] is None
    assert len(list(tmp_path.glob("cron_jobs.json.bak-*"))) == 1


def test_a_leftover_legacy_job_beside_the_new_one_is_removed_not_doubled(ci_env, tmp_path):
    from navig.checkins import jobs

    store = tmp_path / "cron_jobs.json"
    data = _live_shaped_store(store)
    data["jobs"].append(
        {
            "id": "job_20",
            "name": "checkin:weigh",
            "schedule": "10 8 * * *",
            "command": f"NAVIG_CHECKIN:weigh:{CHAT}",
            "enabled": True,
            "timeout_seconds": 180,
        }
    )
    store.write_text(json.dumps(data), encoding="utf-8")
    out = jobs.migrate_legacy_jobs(store, default_chat=CHAT, persist_space=False)
    actions = {c["id"]: c["action"] for c in out["changes"]}
    assert actions["job_7"] == "remove-duplicate"
    names = [j["name"] for j in json.loads(store.read_text(encoding="utf-8"))["jobs"]]
    assert names.count("checkin:weigh") == 1


def test_a_job_with_no_chat_is_skipped_and_reported(ci_env, tmp_path):
    from navig.checkins import jobs

    store = tmp_path / "cron_jobs.json"
    _live_shaped_store(store)
    out = jobs.migrate_legacy_jobs(store, dry_run=True, default_chat=None, persist_space=False)
    assert out["changes"] == [] and len(out["skipped"]) == 2


def test_migration_carries_the_space_into_config(ci_env, tmp_path):
    from navig.checkins import engine, jobs
    from navig.config import get_config_manager

    store = tmp_path / "cron_jobs.json"
    _live_shaped_store(store)
    jobs.migrate_legacy_jobs(store, default_chat=CHAT)
    assert get_config_manager().get("checkins.weigh.space") == SPACE
    assert str(engine.weigh_path(CHAT)).startswith(SPACE)


def test_job_specs_for_pills_include_one_nudge_per_slot(ci_env, add_pills):
    from navig.checkins import jobs

    item = add_pills(times="08:00,23:30", missed_after=120)
    specs = {s["name"]: s for s in jobs.job_specs(item, CHAT)}
    assert specs["checkin:metformin:0800"]["schedule"] == "0 8 * * *"
    assert specs["checkin:metformin:2330:nudge"]["schedule"] == "30 1 * * *"
    assert (
        specs["checkin:metformin:0800:nudge"]["command"]
        == f"NAVIG_CHECKIN:metformin:{CHAT}:0800:nudge"
    )


# ── the scheduler ─────────────────────────────────────────────────────────────


def _service(tmp_path):
    from navig.scheduler.cron_service import CronService

    return CronService(None, tmp_path / "sched")


@pytest.mark.asyncio
async def test_cron_runs_navig_checkin_in_process(ci_env, tmp_path, tg, add_pills, monkeypatch):
    from navig.checkins import engine
    from navig.scheduler.cron_service import CronJob

    add_pills()
    monkeypatch.setattr(engine, "http_sender", lambda: tg)
    monkeypatch.setattr(engine, "_now", lambda tz=None: datetime(2026, 10, 8, 8, 0, tzinfo=PARIS))
    svc = _service(tmp_path)
    job = CronJob(
        id="j1",
        name="checkin:metformin:0800",
        schedule="0 8 * * *",
        command=f"NAVIG_CHECKIN:metformin:{CHAT}:0800",
        timeout_seconds=180,
    )
    out = await svc._execute_job_command(job)
    assert "sent" in out and len(tg.sent()) == 1


@pytest.mark.asyncio
async def test_cron_retry_after_a_timeout_does_not_ask_twice(
    ci_env, tmp_path, tg, add_pills, monkeypatch
):
    """The job ran past its timeout AFTER Telegram accepted the card: the retry skips."""
    from navig.checkins import engine
    from navig.scheduler.cron_service import CronJob, JobStatus

    add_pills()
    monkeypatch.setattr(engine, "_now", lambda tz=None: datetime(2026, 10, 8, 8, 0, tzinfo=PARIS))

    async def slow_send(method, payload):
        result = await tg(method, payload)
        import asyncio

        await asyncio.sleep(5)  # Telegram took it; the run is killed at the ceiling
        return result

    monkeypatch.setattr(engine, "http_sender", lambda: slow_send)
    svc = _service(tmp_path)
    job = CronJob(
        id="j1",
        name="checkin:metformin:0800",
        schedule="0 8 * * *",
        command=f"NAVIG_CHECKIN:metformin:{CHAT}:0800",
        timeout_seconds=1,
    )
    svc.jobs[job.id] = job
    await svc._run_job(job)
    assert job.last_status == JobStatus.FAILED
    monkeypatch.setattr(engine, "http_sender", lambda: tg)
    out = await svc._execute_job_command(job)  # the 5-minute retry
    assert out.startswith("skipped") and "already asked" in out
    assert len(tg.sent()) == 1


@pytest.mark.asyncio
async def test_cron_honours_the_checkins_extension(ci_env, tmp_path, tg, add_pills, monkeypatch):
    from navig.checkins import engine
    from navig.gateway.channels import telegram_extensions as te
    from navig.scheduler.cron_service import CronJob

    add_pills()
    monkeypatch.setattr(engine, "http_sender", lambda: tg)
    monkeypatch.setattr(te, "is_enabled", lambda ext: ext != "checkins")
    job = CronJob(
        id="j1",
        name="checkin:metformin:0800",
        schedule="0 8 * * *",
        command=f"NAVIG_CHECKIN:metformin:{CHAT}:0800",
    )
    out = await _service(tmp_path)._execute_job_command(job)
    assert "Checkins extension is off" in out
    assert tg.calls == []


@pytest.mark.asyncio
async def test_a_paused_or_removed_checkin_job_does_nothing(
    ci_env, tmp_path, tg, add_pills, monkeypatch
):
    from navig.checkins import defs as d
    from navig.checkins import engine

    add_pills()
    d.set_enabled("metformin", False)
    monkeypatch.setattr(engine, "http_sender", lambda: tg)
    svc = _service(tmp_path)
    from navig.scheduler.cron_service import CronJob

    job = CronJob(
        id="j1", name="x", schedule="0 8 * * *", command=f"NAVIG_CHECKIN:metformin:{CHAT}:0800"
    )
    assert "paused" in await svc._execute_job_command(job)
    job.command = f"NAVIG_CHECKIN:gone:{CHAT}:0800"
    assert "no check-in" in await svc._execute_job_command(job)
    assert tg.calls == []


def test_the_checkins_extension_owns_ci_and_the_command():
    from navig.gateway.channels import telegram_extensions as te

    assert te.extension_for_callback("ci:metformin:t:20261008:0800") == "checkins"
    assert te.extension_for_command("checkins") == "checkins"
    assert te.extension_for_command("weigh") == "health"
