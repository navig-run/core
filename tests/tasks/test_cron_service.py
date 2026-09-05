"""
Tests for navig.scheduler.cron_service — JobStatus, CronConfig, CronJob, CronParser.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from navig.scheduler.cron_service import (
    CronConfig,
    CronJob,
    CronParser,
    CronService,
    JobStatus,
)

# ─── JobStatus ────────────────────────────────────────────────────────────────


def test_job_status_values():
    assert JobStatus.PENDING.value == "pending"
    assert JobStatus.RUNNING.value == "running"
    assert JobStatus.SUCCESS.value == "success"
    assert JobStatus.FAILED.value == "failed"
    assert JobStatus.DISABLED.value == "disabled"


@pytest.mark.parametrize("status", list(JobStatus))
def test_job_status_roundtrip(status):
    assert JobStatus(status.value) is status


# ─── CronConfig ───────────────────────────────────────────────────────────────


def test_cron_config_defaults():
    cfg = CronConfig()
    assert cfg.enabled is True
    assert cfg.max_concurrent_jobs == 5
    assert cfg.default_timeout_seconds == 300
    assert cfg.retry_failed is True
    assert cfg.max_retries == 3


def test_cron_config_from_dict_full():
    cfg = CronConfig.from_dict({
        "enabled": False,
        "max_concurrent": 10,
        "timeout": 600,
        "retry_failed": False,
        "max_retries": 5,
    })
    assert cfg.enabled is False
    assert cfg.max_concurrent_jobs == 10
    assert cfg.default_timeout_seconds == 600
    assert cfg.retry_failed is False
    assert cfg.max_retries == 5


def test_cron_config_from_dict_empty_uses_defaults():
    cfg = CronConfig.from_dict({})
    assert cfg.enabled is True
    assert cfg.max_concurrent_jobs == 5


# ─── CronJob ──────────────────────────────────────────────────────────────────


def _make_job(**overrides) -> CronJob:
    defaults = dict(
        id="job-1",
        name="Daily backup",
        schedule="daily",
        command="navig backup run --all",
    )
    defaults.update(overrides)
    return CronJob(**defaults)


def test_cron_job_defaults():
    j = _make_job()
    assert j.enabled is True
    assert j.timeout_seconds == 300
    assert j.retry_count == 0
    assert j.max_retries == 3
    assert j.last_run is None
    assert j.next_run is None
    assert j.last_status is None
    assert j.last_output is None


def test_cron_job_to_dict_basic():
    j = _make_job()
    d = j.to_dict()
    assert d["id"] == "job-1"
    assert d["name"] == "Daily backup"
    assert d["schedule"] == "daily"
    assert d["command"] == "navig backup run --all"
    assert d["enabled"] is True
    assert d["last_run"] is None
    assert d["next_run"] is None
    assert d["last_status"] is None


def test_cron_job_roundtrip():
    now = datetime(2024, 5, 1, 9, 0, 0)
    j = _make_job(
        last_run=now,
        next_run=now + timedelta(days=1),
        last_status=JobStatus.SUCCESS,
        last_output="completed successfully",
    )
    restored = CronJob.from_dict(j.to_dict())
    assert restored.id == j.id
    assert restored.name == j.name
    assert restored.last_run == j.last_run
    assert restored.next_run == j.next_run
    assert restored.last_status == JobStatus.SUCCESS
    assert restored.last_output == "completed successfully"


def test_cron_job_from_dict_no_dates():
    data = {
        "id": "j2",
        "name": "Test",
        "schedule": "hourly",
        "command": "navig run test",
    }
    j = CronJob.from_dict(data)
    assert j.last_run is None
    assert j.next_run is None
    assert j.last_status is None


@pytest.mark.parametrize("status", list(JobStatus))
def test_cron_job_all_statuses_roundtrip(status):
    j = _make_job(last_status=status)
    restored = CronJob.from_dict(j.to_dict())
    assert restored.last_status == status


def test_cron_job_to_dict_status_none():
    j = _make_job()
    d = j.to_dict()
    assert d["last_status"] is None


# ─── CronParser.parse ─────────────────────────────────────────────────────────


def test_parse_every_N_minutes():
    result = CronParser.parse("every 5 minutes")
    assert result == timedelta(minutes=5)


def test_parse_every_N_hours():
    result = CronParser.parse("every 2 hours")
    assert result == timedelta(hours=2)


def test_parse_every_N_days():
    result = CronParser.parse("every 3 days")
    assert result == timedelta(days=3)


def test_parse_hourly():
    assert CronParser.parse("hourly") == timedelta(hours=1)


def test_parse_daily():
    assert CronParser.parse("daily") == timedelta(days=1)


def test_parse_weekly():
    assert CronParser.parse("weekly") == timedelta(weeks=1)


def test_parse_cron_expression_returns_none():
    result = CronParser.parse("0 9 * * 1")
    assert result is None  # cron expressions return None from parse()


def test_parse_unknown_returns_none():
    assert CronParser.parse("at midnight on thursdays") is None


# ─── CronParser._is_cron_expression ──────────────────────────────────────────


def test_is_cron_expression_valid():
    assert CronParser._is_cron_expression("* * * * *") is True
    assert CronParser._is_cron_expression("0 9 * * 1") is True
    assert CronParser._is_cron_expression("*/5 * * * *") is True


def test_is_cron_expression_invalid():
    assert CronParser._is_cron_expression("daily") is False
    assert CronParser._is_cron_expression("every 5 minutes") is False


# ─── CronParser.validate — reject dead schedules at add-time ──────────────────


def test_validate_accepts_natural_language_and_in_range_cron():
    for good in ("every 30 minutes", "daily", "hourly", "0 9 * * 1-5",
                 "*/15 9-17/2 * * *", "0 0 1,15 * *", "* * * * *", "0 0 * * 7"):
        ok, reason = CronParser.validate(good)
        assert ok, (good, reason)
        assert reason is None


def test_validate_rejects_out_of_range_fields():
    # Each parses the char-set fine (is_valid used to say True) but never fires.
    for bad, needle in (
        ("61 * * * *", "minute"),
        ("0 25 * * *", "hour"),
        ("0 0 32 * *", "day-of-month"),
        ("0 0 * 13 *", "month"),
        ("0 0 * * 8", "day-of-week"),
        ("0 0 1-99 * *", "day-of-month"),
    ):
        ok, reason = CronParser.validate(bad)
        assert not ok, bad
        assert needle in reason and "out of range" in reason, (bad, reason)


def test_validate_dow_seven_is_sunday_not_out_of_range():
    ok, reason = CronParser.validate("0 0 * * 7")
    assert ok, reason


def test_validate_rejects_non_positive_step():
    ok, reason = CronParser.validate("*/0 * * * *")
    assert not ok
    assert "step" in reason


def test_validate_rejects_gibberish_and_too_few_fields():
    for bad in ("total garbage", "0 9 * *", "", "   "):
        ok, reason = CronParser.validate(bad)
        assert not ok, bad
        assert reason  # a non-empty human reason is always returned


def test_is_valid_is_the_bool_of_validate():
    assert CronParser.is_valid("0 9 * * 1-5") is True
    assert CronParser.is_valid("61 * * * *") is False   # newly rejected (was True)
    assert CronParser.is_valid("every 10 minutes") is True


# ─── CronParser.calculate_next ────────────────────────────────────────────────


def test_calculate_next_daily():
    base = datetime(2024, 1, 1, 9, 0)
    result = CronParser.calculate_next("daily", from_time=base)
    assert result == base + timedelta(days=1)


def test_calculate_next_every_30_minutes():
    base = datetime(2024, 1, 1, 10, 0)
    result = CronParser.calculate_next("every 30 minutes", from_time=base)
    assert result == base + timedelta(minutes=30)


def test_calculate_next_unknown_defaults_to_1_hour():
    base = datetime(2024, 1, 1, 12, 0)
    result = CronParser.calculate_next("unknown schedule", from_time=base)
    assert result == base + timedelta(hours=1)


def test_calculate_next_uses_now_when_from_time_none():
    before = datetime.now()
    result = CronParser.calculate_next("hourly")
    after = datetime.now()
    # Result should be between now+1h - 1s and now+1h + 1s
    assert before + timedelta(hours=1) - timedelta(seconds=1) <= result <= after + timedelta(hours=1) + timedelta(seconds=1)


# ─── Cron day-of-week correctness (Python weekday Mon=0 vs cron Sun=0) ─────────
# 2024-01-01 is a MONDAY (weekday()==0). These pin the fix for schedules that
# firing on the wrong day: `* * * * 1` must fire Monday, not Tuesday.


def test_cron_monday_fires_on_monday_not_tuesday():
    base = datetime(2023, 12, 31, 10, 0)  # Sunday
    nxt = CronParser.calculate_next("0 9 * * 1", from_time=base)
    assert nxt == datetime(2024, 1, 1, 9, 0)  # Monday 09:00
    assert nxt.weekday() == 0


def test_cron_sunday_zero_and_seven_both_mean_sunday():
    base = datetime(2024, 1, 1, 10, 0)  # Monday
    for expr in ("0 9 * * 0", "0 9 * * 7"):
        nxt = CronParser.calculate_next(expr, from_time=base)
        assert nxt == datetime(2024, 1, 7, 9, 0), expr  # the coming Sunday
        assert nxt.weekday() == 6, expr


def test_cron_weekdays_range_is_mon_to_fri():
    # cron 1-5 = Mon–Fri; Saturday & Sunday must NOT match.
    for d in range(1, 6):  # Jan 1(Mon)..Jan 5(Fri)
        assert CronParser._matches_weekday(datetime(2024, 1, d).weekday(), "1-5"), d
    assert not CronParser._matches_weekday(datetime(2024, 1, 6).weekday(), "1-5")  # Sat
    assert not CronParser._matches_weekday(datetime(2024, 1, 7).weekday(), "1-5")  # Sun


def test_cron_weekends_list_is_sat_and_sun():
    assert CronParser._matches_weekday(datetime(2024, 1, 6).weekday(), "0,6")   # Sat
    assert CronParser._matches_weekday(datetime(2024, 1, 7).weekday(), "0,6")   # Sun
    assert not CronParser._matches_weekday(datetime(2024, 1, 3).weekday(), "0,6")  # Wed


# ─── DOM/DOW OR semantics (standard Vixie cron) ───────────────────────────────


def test_dom_and_dow_both_restricted_is_or():
    # "0 0 1 * 1" = midnight on the 1st OR any Monday.
    monday_not_1st = datetime(2024, 1, 8, 0, 0)    # Monday, day 8
    first_not_monday = datetime(2024, 2, 1, 0, 0)  # Thursday, day 1
    assert CronParser._matches_cron(monday_not_1st, "0", "0", "1", "*", "1")
    assert CronParser._matches_cron(first_not_monday, "0", "0", "1", "*", "1")
    assert not CronParser._matches_cron(datetime(2024, 1, 10, 0, 0), "0", "0", "1", "*", "1")


def test_dom_only_restricted_matches_that_day():
    assert CronParser._matches_cron(datetime(2024, 1, 15, 0, 0), "0", "0", "15", "*", "*")
    assert not CronParser._matches_cron(datetime(2024, 1, 16, 0, 0), "0", "0", "15", "*", "*")


# ─── */step counted from the field minimum ────────────────────────────────────


def test_step_dom_starts_at_the_first():
    # DOM */2 (min 1) → 1,3,5… not 2,4,6.
    assert CronParser._matches_field(1, "*/2", 1, 31)
    assert CronParser._matches_field(3, "*/2", 1, 31)
    assert not CronParser._matches_field(2, "*/2", 1, 31)


def test_step_minute_starts_at_zero():
    for m in (0, 15, 30, 45):
        assert CronParser._matches_field(m, "*/15", 0, 59)
    assert not CronParser._matches_field(7, "*/15", 0, 59)


# ─── Composed field forms: range+step, list-of-ranges, n/step (no crash) ──────


def test_range_step_hour_is_business_hours():
    # "9-17/2" = 9,11,13,15,17 (a common "every 2 hours, 9am–5pm"). This used to
    # CRASH — int("17/2") in the old range branch — for a schedule _is_cron_field
    # happily accepts, so `navig … schedule add` 500'd / the loop errored.
    for h in (9, 11, 13, 15, 17):
        assert CronParser._matches_field(h, "9-17/2", 0, 23), h
    for h in (8, 10, 12, 18):
        assert not CronParser._matches_field(h, "9-17/2", 0, 23), h


def test_range_step_counts_from_the_range_start():
    # "0-30/10" = 0,10,20,30 — the step is anchored to the range start, not min.
    for m in (0, 10, 20, 30):
        assert CronParser._matches_field(m, "0-30/10", 0, 59), m
    assert not CronParser._matches_field(5, "0-30/10", 0, 59)
    assert not CronParser._matches_field(40, "0-30/10", 0, 59)


def test_list_containing_a_range():
    # "1,10-15" = the 1st plus the 10th–15th. Used to crash: int("10-15").
    assert CronParser._matches_field(1, "1,10-15", 1, 31)
    for d in (10, 12, 15):
        assert CronParser._matches_field(d, "1,10-15", 1, 31), d
    for d in (2, 9, 16):
        assert not CronParser._matches_field(d, "1,10-15", 1, 31), d


def test_n_slash_step_runs_from_n_to_field_max():
    # "5/15" (minute) = 5,20,35,50 — start at 5, step 15 up to the field max.
    for m in (5, 20, 35, 50):
        assert CronParser._matches_field(m, "5/15", 0, 59), m
    assert not CronParser._matches_field(0, "5/15", 0, 59)
    assert not CronParser._matches_field(6, "5/15", 0, 59)


def test_malformed_field_returns_false_never_raises():
    # Garbage that slips past the permissive _is_cron_field regex must degrade to
    # "no match", never raise — a bad field can't be allowed to crash next-run calc.
    for bad in ("1--2", "10/", "/5", "5/0", "1-", "-3", "a", "1/2/3", ""):
        assert CronParser._matches_field(3, bad, 0, 59) is False, bad


def test_range_step_weekday_is_mon_wed_fri():
    # DOW "1-5/2" = Mon,Wed,Fri. This used to silently NEVER match (int("5/2") was
    # swallowed by the except), so the schedule fell through to the +1h fallback —
    # a job that never fired on the days the user asked for. 2024-01-01 is a Monday.
    assert CronParser._matches_weekday(datetime(2024, 1, 1).weekday(), "1-5/2")  # Mon
    assert CronParser._matches_weekday(datetime(2024, 1, 3).weekday(), "1-5/2")  # Wed
    assert CronParser._matches_weekday(datetime(2024, 1, 5).weekday(), "1-5/2")  # Fri
    assert not CronParser._matches_weekday(datetime(2024, 1, 2).weekday(), "1-5/2")  # Tue
    assert not CronParser._matches_weekday(datetime(2024, 1, 4).weekday(), "1-5/2")  # Thu


def test_composed_schedule_resolves_end_to_end():
    # "0 9-17/2 * * *" from 08:30 → the next fire is 09:00, not the +1h fallback
    # the old crash-then-except path produced.
    nxt = CronParser.calculate_next("0 9-17/2 * * *", from_time=datetime(2024, 1, 1, 8, 30))
    assert nxt == datetime(2024, 1, 1, 9, 0)


# ─── Sparse schedules resolve (no wrong +1h fallback) ─────────────────────────


def test_monthly_on_31st_resolves_across_short_month():
    # From mid-Feb (29 days, 2024), next "0 0 31 * *" is Mar 31 — > the old
    # ~31-day scan cap, which used to silently return from_time + 1h.
    nxt = CronParser.calculate_next("0 0 31 * *", from_time=datetime(2024, 2, 15, 12, 0))
    assert nxt == datetime(2024, 3, 31, 0, 0)


def test_yearly_schedule_resolves():
    nxt = CronParser.calculate_next("0 0 1 1 *", from_time=datetime(2024, 3, 1, 12, 0))
    assert nxt == datetime(2025, 1, 1, 0, 0)


# ─── CronService: external-edit reconcile (two-writer footgun) ────────────────


def _svc(tmp_path):
    # gateway=None → skips live-service registration + legacy migration (test-safe).
    return CronService(gateway=None, storage_path=tmp_path)


def test_reload_picks_up_external_add(tmp_path):
    svc = _svc(tmp_path)
    svc.add_job(name="a", schedule="daily", command="navig a")
    path = tmp_path / "cron_jobs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    jb = dict(data["jobs"][0])  # a valid job dict shape
    jb["id"], jb["name"] = "job_ext", "b"
    data["jobs"].append(jb)
    data["counter"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")

    svc._jobs_file_mtime = 0.0  # simulate the file being newer than our last write
    svc._reload_if_changed()

    assert {j.name for j in svc.jobs.values()} == {"a", "b"}
    assert svc._job_counter == 99


def test_reload_honors_external_removal(tmp_path):
    svc = _svc(tmp_path)
    svc.add_job(name="a", schedule="daily", command="navig a")
    svc.add_job(name="b", schedule="daily", command="navig b")
    path = tmp_path / "cron_jobs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["jobs"] = [j for j in data["jobs"] if j["name"] == "a"]
    path.write_text(json.dumps(data), encoding="utf-8")

    svc._jobs_file_mtime = 0.0
    svc._reload_if_changed()

    assert {j.name for j in svc.jobs.values()} == {"a"}


def test_reload_ignores_own_write(tmp_path):
    svc = _svc(tmp_path)
    svc.add_job(name="a", schedule="daily", command="navig a")
    jobs_ref = svc.jobs  # our own save recorded the mtime → reload must be a no-op
    svc._reload_if_changed()
    assert svc.jobs is jobs_ref  # not replaced


def test_reload_recalcs_next_run_for_enabled(tmp_path):
    svc = _svc(tmp_path)
    svc.add_job(name="a", schedule="daily", command="navig a")
    path = tmp_path / "cron_jobs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["jobs"][0]["next_run"] = None  # external write left no next_run
    path.write_text(json.dumps(data), encoding="utf-8")

    svc._jobs_file_mtime = 0.0
    svc._reload_if_changed()

    assert next(iter(svc.jobs.values())).next_run is not None  # recalculated


# ─── config-wipe guard: a transient/corrupt read must not erase every schedule ─
#
# cron_jobs.json holds EVERY system-wide schedule. A transient OS lock (AV/backup
# agent, a read mid-os.replace) used to collapse the load to zero jobs, and the very
# next _save_jobs (start() saves right after load) persisted that empty set — wiping
# all schedules permanently. The store now refuses to overwrite a file it couldn't read.


def _raise_json_read_error(*_a, **_k):
    from navig.core.json_io import JsonReadError

    raise JsonReadError("simulated transient lock (sharing violation)")


def test_transient_read_failure_keeps_jobs_and_blocks_save(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    svc.add_job(name="a", schedule="daily", command="navig a")
    svc.add_job(name="b", schedule="daily", command="navig b")
    path = tmp_path / "cron_jobs.json"
    on_disk_before = path.read_bytes()

    # A lock that survives the retries: the mutating load must NOT collapse to {}.
    monkeypatch.setattr(
        "navig.scheduler.cron_service.load_json_for_update", _raise_json_read_error
    )
    svc._load_jobs()

    assert svc._jobs_readable is False
    assert {j.name for j in svc.jobs.values()} == {"a", "b"}  # in-memory jobs untouched

    svc._save_jobs()  # must REFUSE — writing our view could overwrite unread schedules
    assert path.read_bytes() == on_disk_before  # file left exactly as it was — no wipe


def test_boot_with_unreadable_store_does_not_wipe_it(tmp_path, monkeypatch):
    """The exact production trigger: daemon boot loads then saves. If the load can't read
    the store, the save must not overwrite it with an empty set."""
    seed = _svc(tmp_path)
    seed.add_job(name="keep", schedule="daily", command="navig keep")
    path = tmp_path / "cron_jobs.json"
    on_disk = path.read_bytes()

    # A fresh service boots while the file is locked (its __init__ calls _load_jobs).
    monkeypatch.setattr(
        "navig.scheduler.cron_service.load_json_for_update", _raise_json_read_error
    )
    booted = _svc(tmp_path)
    assert booted._jobs_readable is False
    booted._save_jobs()  # what start() does right after load

    assert path.read_bytes() == on_disk  # every schedule survived the locked boot


def test_corrupt_store_is_quarantined_and_starts_empty(tmp_path):
    path = tmp_path / "cron_jobs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json ,,,", encoding="utf-8")

    svc = _svc(tmp_path)  # __init__ → _load_jobs must not raise

    assert svc.jobs == {}  # corrupt file → start empty (bytes already lost)
    assert svc._jobs_readable is True  # a corrupt (not locked) file is safe to overwrite
    assert (tmp_path / "cron_jobs.json.corrupt").exists()  # original bytes preserved


def test_one_malformed_job_entry_does_not_drop_the_rest(tmp_path):
    svc = _svc(tmp_path)
    svc.add_job(name="good", schedule="daily", command="navig good")
    path = tmp_path / "cron_jobs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["jobs"].append({"id": "broken"})  # missing required fields → from_dict raises
    path.write_text(json.dumps(data), encoding="utf-8")

    svc._jobs_file_mtime = 0.0
    svc._reload_if_changed()

    assert {j.name for j in svc.jobs.values()} == {"good"}  # 'good' kept, 'broken' skipped


# ─── CronService: per-job in-flight guard (no double-execution) ───────────────


async def test_run_job_guarded_against_concurrent_double_execution(tmp_path, monkeypatch):
    """A scheduled fire and a manual run_job_now() for the SAME job must not both execute:
    that would run the command twice and race-write the job's last_run/status/next_run."""
    svc = _svc(tmp_path)
    job = _make_job(id="dup-1", command="echo hi")
    svc.jobs[job.id] = job

    calls = {"n": 0}
    release = asyncio.Event()

    async def _fake_exec(j):
        calls["n"] += 1
        await release.wait()  # hold the first run "in flight"
        return "ok"

    monkeypatch.setattr(svc, "_execute_job_command", _fake_exec)

    # Start a scheduled run; it enters _run_job and blocks inside _execute_job_command.
    scheduled = asyncio.create_task(svc._run_job(job, trigger="schedule"))
    for _ in range(6):
        await asyncio.sleep(0)  # let the task reach _fake_exec → job.id now in _running_jobs
    assert job.id in svc._running_jobs

    # A concurrent manual "run now" must be SKIPPED, not a second execution.
    await svc.run_job_now(job.id)
    assert calls["n"] == 1

    # Release the first run; the guard clears so a LATER run can proceed normally.
    release.set()
    await asyncio.wait_for(scheduled, timeout=2.0)
    assert job.id not in svc._running_jobs
    assert calls["n"] == 1


# ─── retry budget resets per failure episode (not once per lifetime) ──────────
#
# retry_count was reset ONLY on success, so once a job exhausted max_retries in one
# failure episode it stayed at max_retries forever — the guard `retry_count <
# max_retries` was then permanently False and NO later transient failure was ever
# retried (auto-retry silently disabled until a run happened to succeed).


async def test_retry_budget_resets_after_exhaustion(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    svc.config.retry_failed = True  # the guard needs it (default, pinned for clarity)
    job = _make_job(id="retry-1", schedule="daily", command="boom", max_retries=2)
    svc.jobs[job.id] = job

    async def _boom(_j):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(svc, "_execute_job_command", _boom)

    def soon() -> datetime:
        return datetime.now() + timedelta(minutes=10)

    # Episode 1, attempt 1: fails → retry scheduled (~+5min, well under an hour).
    await svc._run_job_locked(job)
    assert job.retry_count == 1
    assert job.next_run < soon()  # a near-term retry, not the daily schedule

    # Episode 1, attempt 2: budget exhausted (retry_count == max_retries). The
    # counter must RESET so the next episode is fresh, and next_run falls back to the
    # normal (daily) schedule rather than another +5min retry.
    await svc._run_job_locked(job)
    assert job.retry_count == 0  # ← the fix (was stuck at 2 before)
    assert job.next_run > datetime.now() + timedelta(hours=1)

    # A LATER scheduled failure must get a FRESH retry, not be silently un-retried.
    await svc._run_job_locked(job)
    assert job.retry_count == 1  # fresh episode (before the fix: 3, guard False, no retry)
    assert job.next_run < soon()


async def test_retry_count_still_resets_on_success(tmp_path, monkeypatch):
    # Guard the existing behavior: a successful run clears any accumulated retry count.
    svc = _svc(tmp_path)
    job = _make_job(id="retry-2", schedule="daily", command="ok",
                    max_retries=2, retry_count=1)
    svc.jobs[job.id] = job

    async def _ok(_j):
        return "done"

    monkeypatch.setattr(svc, "_execute_job_command", _ok)
    await svc._run_job_locked(job)
    assert job.last_status is JobStatus.SUCCESS
    assert job.retry_count == 0


async def test_timed_out_navig_subprocess_is_killed(tmp_path, monkeypatch):
    """A `navig ` command cancelled by the timeout wrapper must have its subprocess
    KILLED, not left running orphaned past the reported failure (a live infra command
    would otherwise keep executing, and a retry could launch a second copy)."""
    svc = _svc(tmp_path)
    job = _make_job(command="navig backup run --all")

    class _FakeProc:
        def __init__(self):
            self.returncode = None
            self.killed = False
            self.waited = False

        async def communicate(self):
            await asyncio.sleep(30)  # hang until the timeout wrapper cancels us
            return (b"", b"")

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.waited = True
            return self.returncode

    proc = _FakeProc()

    async def _fake_create(*_a, **_k):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)

    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await asyncio.wait_for(svc._execute_job_command(job), timeout=0.05)

    assert proc.killed is True  # the orphan was terminated on cancellation
    assert proc.waited is True  # and reaped, not left as a zombie


async def test_completed_navig_subprocess_is_not_killed(tmp_path, monkeypatch):
    """A normally-completing subprocess must NOT be killed (returncode is set)."""
    svc = _svc(tmp_path)
    job = _make_job(command="navig backup run --all")

    class _DoneProc:
        def __init__(self):
            self.returncode = 0
            self.killed = False

        async def communicate(self):
            return (b"ok\n", b"")

        def kill(self):
            self.killed = True

        async def wait(self):
            return self.returncode

    proc = _DoneProc()

    async def _fake_create(*_a, **_k):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    out = await svc._execute_job_command(job)
    assert out.strip() == "ok"
    assert proc.killed is False  # happy path must not kill


class _FakeGatewayNoneTurn:
    """A gateway whose agent turn produces no text (run_agent_turn -> None)."""

    event_queue = None

    async def run_agent_turn(self, **_kwargs):
        return None


async def test_ai_job_with_empty_turn_is_success_not_spurious_failure(tmp_path):
    """An AI-prompt job whose turn returns None must record SUCCESS with empty output,
    not FAILED — the bare None used to crash `output[:5000]` (TypeError → job FAILED)."""
    svc = CronService(gateway=_FakeGatewayNoneTurn(), storage_path=tmp_path)
    job = _make_job(command="summarize my inbox")  # not a `navig ` command -> AI branch
    svc.jobs[job.id] = job

    await svc._run_job_locked(job)

    assert job.last_status is JobStatus.SUCCESS  # was FAILED (TypeError) before the fix
    assert job.last_output == ""  # empty output cleanly recorded, not an error string


# ─── Time-pinned natural language ("daily at 9am") fires AT that time ──────────
# Regression: "daily at 9am" matched the loose `daily` interval pattern and fired at
# from_time-of-day (creation time), silently ignoring "9am". It is now converted to a cron.


def test_daily_at_time_fires_at_that_hour_not_creation_time():
    # Job created at 15:00 with "daily at 9am" must next fire at 09:00 the NEXT day —
    # NOT 15:00 tomorrow (the old `from_time + 1 day` bug).
    created = datetime(2026, 7, 24, 15, 0, 0)
    nxt = CronParser.calculate_next("daily at 9am", created)
    assert nxt == datetime(2026, 7, 25, 9, 0, 0)


def test_to_cron_daily_time_forms():
    assert CronParser._to_cron("daily at 9am") == "0 9 * * *"
    assert CronParser._to_cron("every day at 9:30am") == "30 9 * * *"
    assert CronParser._to_cron("daily at 9pm") == "0 21 * * *"
    assert CronParser._to_cron("daily at noon") == "0 12 * * *"
    assert CronParser._to_cron("daily at midnight") == "0 0 * * *"
    assert CronParser._to_cron("daily at 21:00") == "0 21 * * *"
    assert CronParser._to_cron("at 8am") == "0 8 * * *"  # bare time → daily


def test_to_cron_weekday_forms_both_orderings():
    assert CronParser._to_cron("monday at 6pm") == "0 18 * * 1"
    assert CronParser._to_cron("every friday at noon") == "0 12 * * 5"
    assert CronParser._to_cron("sundays at 10:30pm") == "30 22 * * 0"  # plural + minutes
    # time-FIRST ordering ("at <time> on/every <day>") resolves the same weekday
    assert CronParser._to_cron("at midnight on thursdays") == "0 0 * * 4"
    assert CronParser._to_cron("at 9pm every friday") == "0 21 * * 5"


def test_to_cron_weekdays_form():
    assert CronParser._to_cron("weekdays at 8am") == "0 8 * * 1-5"
    assert CronParser._to_cron("at noon on weekdays") == "0 12 * * 1-5"


def test_to_cron_returns_none_for_intervals_crons_and_ambiguous():
    # bare intervals + cron expressions are untouched, and a day-less "weekly at <t>" is
    # ambiguous → left as None (falls back to the weekly interval).
    for s in ("daily", "hourly", "every 30 minutes", "0 9 * * 1-5", "*/5 * * * *",
              "weekly at 9am"):
        assert CronParser._to_cron(s) is None, s


def test_bare_intervals_still_parse_as_intervals():
    # The fix must not change bare interval handling.
    assert CronParser.parse("daily") == timedelta(days=1)
    assert CronParser.parse("every 30 minutes") == timedelta(minutes=30)
    assert CronParser.parse("daily at 9am") is None  # time-pinned → handled as cron, not interval


def test_validate_accepts_time_pinned_schedules():
    for good in ("daily at 9am", "monday at 6pm", "weekdays at 8am", "at midnight on thursdays"):
        ok, reason = CronParser.validate(good)
        assert ok, (good, reason)
        assert reason is None


# ─── Sparse & unsatisfiable cron next-run resolution ──────────────────────────
# Regression: _next_cron_time scanned only a fixed ~1-year (366-day) window minute by
# minute, then fell back to `from_time + 1h` on no match. That silently turned BOTH a
# valid leap-only schedule (Feb 29, up to ~4 years out) AND a truly impossible one
# (Feb 30) into an HOURLY-firing job — and paid a ~527k-iteration event-loop stall each
# recompute. Now: the horizon spans a leap cycle, the loop skips whole non-matching days
# (no cliff), and an impossible schedule is parked far in the future, never rescheduled +1h.


def test_leap_only_schedule_resolves_to_next_leap_day():
    # "0 0 29 2 *" from a NON-leap year must land on the next real Feb 29 (2028),
    # not fall through to the +1h hourly-firing bug.
    nxt = CronParser._next_cron_time("0 0 29 2 *", datetime(2026, 7, 24, 15, 0))
    assert nxt == datetime(2028, 2, 29, 0, 0)


def test_impossible_day_month_is_parked_not_hourly():
    from_time = datetime(2026, 7, 24, 15, 0)
    nxt = CronParser._next_cron_time("0 0 30 2 *", from_time)  # Feb 30 never occurs
    # Parked far in the future — crucially NOT `from_time + 1h` (the old bug that made an
    # impossible schedule fire every hour forever).
    assert nxt != from_time + timedelta(hours=1)
    assert nxt > from_time + timedelta(days=365 * 5)


def test_impossible_time_field_is_parked_up_front():
    # A minute/hour field that matches no valid value (slipped past validation) must be
    # caught cheaply, not walked minute-by-minute for years.
    from_time = datetime(2026, 7, 24, 15, 0)
    nxt = CronParser._next_cron_time("0 99 * * *", from_time)  # hour 99 is impossible
    assert nxt != from_time + timedelta(hours=1)
    assert nxt > from_time + timedelta(days=365 * 5)


def test_impossible_schedule_returns_quickly_no_scan_cliff():
    # The day-skip must keep an unsatisfiable schedule cheap — a regression to the
    # minute-by-minute scan would be ~2M iterations (seconds of event-loop stall).
    import time as _time

    start = _time.perf_counter()
    CronParser._next_cron_time("0 0 30 2 *", datetime(2026, 7, 24, 15, 0))
    elapsed = _time.perf_counter() - start
    assert elapsed < 0.5, f"unsatisfiable scan took {elapsed:.2f}s — day-skip regressed"


def test_monthly_on_31st_skips_short_months():
    # From mid-February, "0 0 31 * *" must skip to the next month that HAS a 31st
    # (March), never firing on a non-existent Feb 31.
    nxt = CronParser._next_cron_time("0 0 31 * *", datetime(2026, 2, 15, 15, 0))
    assert nxt == datetime(2026, 3, 31, 0, 0)


def test_common_schedules_unaffected_by_dayskip_rewrite():
    # Guard the hot paths after the rewrite.
    base = datetime(2026, 7, 24, 15, 0)  # a Friday
    assert CronParser._next_cron_time("0 9 * * *", base) == datetime(2026, 7, 25, 9, 0)
    assert CronParser._next_cron_time("0 18 * * 1", base) == datetime(2026, 7, 27, 18, 0)  # Mon
    # a time later today still fires today, not tomorrow
    assert CronParser._next_cron_time("30 23 * * *", base) == datetime(2026, 7, 24, 23, 30)


def test_next_cron_time_short_expression_falls_back():
    # A malformed (<5 field) expression keeps the documented +1h fallback.
    from_time = datetime(2026, 7, 24, 15, 0)
    assert CronParser._next_cron_time("0 9 *", from_time) == from_time + timedelta(hours=1)


# ─── validate() rejects unsatisfiable schedules at add-time ───────────────────
# Every field can be individually in-range yet the COMBINATION never occurs (Feb 30,
# April 31). Such a schedule used to pass validate() and get stored, then _next_cron_time
# would silently park it decades out — a job that looks created but never fires. validate
# now rejects it up front so both add surfaces (deck schedule.py, gateway cron.py) surface
# a reason. _scan_next_cron is the shared satisfiability oracle (None == never occurs).


def test_validate_rejects_impossible_day_month_combos():
    # day-of-week is '*', so this is a pure AND: the DOM must exist in the month.
    for bad in ("0 0 30 2 *", "0 0 31 4 *", "0 0 31 2 *", "0 0 31 6 *"):
        ok, reason = CronParser.validate(bad)
        assert ok is False, bad
        assert reason and "never occurs" in reason, (bad, reason)


def test_validate_accepts_valid_leap_only_and_sparse_schedules():
    # Feb 29 is satisfiable (fires on leap years) and must NOT be rejected; nor may a
    # 31st-of-the-month schedule that simply skips the short months. And per the Vixie
    # OR rule, '30 2 1-5' fires on every weekday in February (the DOW branch matches)
    # even though Feb 30 never occurs — so it must be ACCEPTED, not rejected.
    for good in ("0 0 29 2 *", "0 0 31 * *", "0 9 * * 1-5", "0 0 1 1 *", "0 0 30 2 1-5"):
        ok, reason = CronParser.validate(good)
        assert ok is True, (good, reason)
        assert reason is None


def test_is_valid_false_for_impossible_schedule():
    assert CronParser.is_valid("0 0 30 2 *") is False
    assert CronParser.is_valid("0 0 29 2 *") is True  # leap-only is valid


def test_field_range_error_precedes_satisfiability_reason():
    # An out-of-range field gives the SPECIFIC range error, not the generic
    # "never occurs" — field validation runs first.
    ok, reason = CronParser.validate("0 25 * * *")
    assert ok is False
    assert "out of range" in reason and "never occurs" not in reason


def test_scan_next_cron_is_the_satisfiability_oracle():
    now = datetime(2026, 7, 24, 15, 0)
    assert CronParser._scan_next_cron("0 0 30 2 *", now) is None  # impossible
    assert CronParser._scan_next_cron("0 99 * * *", now) is None  # impossible time field
    assert CronParser._scan_next_cron("0 0 29 2 *", now) == datetime(2028, 2, 29, 0, 0)  # leap
    assert CronParser._scan_next_cron("0 9 * * *", now) == datetime(2026, 7, 25, 9, 0)


# ─── scheduler loop dispatches fire-and-forget (a slow job can't stall the tick) ──
# The loop used to `await asyncio.gather(*tasks)` every tick, so one job running up to
# its timeout (default 300s) blocked the whole 10s cadence — newly-due reminders and
# external-edit reloads waited minutes behind one unrelated job. _dispatch_due_jobs now
# fires each due job as a tracked background task and returns at once.


def _due(job, *, seconds_ago=1):
    job.next_run = datetime.now() - timedelta(seconds=seconds_ago)
    return job


async def test_dispatch_fires_all_due_jobs_without_waiting_for_a_slow_one(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    slow = _due(_make_job(id="slow", command="slow"))
    fast = _due(_make_job(id="fast", command="fast"))
    svc.jobs = {slow.id: slow, fast.id: fast}

    hang = asyncio.Event()
    done = {"fast": False}

    async def _exec(j):
        if j.id == "slow":
            await hang.wait()  # held for the whole assert window
            return "slow-ok"
        done["fast"] = True
        return "fast-ok"

    monkeypatch.setattr(svc, "_execute_job_command", _exec)

    tasks = svc._dispatch_due_jobs(datetime.now())
    assert len(tasks) == 2  # both spawned in a single tick
    for _ in range(10):
        await asyncio.sleep(0)  # let both start; the fast one runs to completion

    # The fast job finished even though the slow one is still hanging — dispatch did
    # NOT serialize on or await the slow job (the old gather would have blocked here).
    assert done["fast"] is True
    assert fast.last_status is JobStatus.SUCCESS
    assert "slow" in svc._running_jobs  # still running, not awaited

    hang.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=2.0)


async def test_dispatch_skips_a_job_already_running(tmp_path):
    svc = _svc(tmp_path)
    job = _due(_make_job(id="busy", command="x"))
    svc.jobs = {job.id: job}
    svc._running_jobs.add(job.id)  # pretend it is mid-execution
    # It is "due" but already in flight → no throwaway task is spawned this tick.
    assert svc._dispatch_due_jobs(datetime.now()) == []


async def test_dispatch_ignores_disabled_future_and_unscheduled_jobs(tmp_path):
    svc = _svc(tmp_path)
    disabled = _due(_make_job(id="off", command="x", enabled=False))
    future = _make_job(id="later", command="x")
    future.next_run = datetime.now() + timedelta(hours=1)
    unscheduled = _make_job(id="nonext", command="x")
    unscheduled.next_run = None
    svc.jobs = {j.id: j for j in (disabled, future, unscheduled)}
    assert svc._dispatch_due_jobs(datetime.now()) == []


async def test_inflight_task_is_referenced_then_released(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    job = _due(_make_job(id="tracked", command="x"))
    svc.jobs = {job.id: job}

    async def _exec(_j):
        return "ok"

    monkeypatch.setattr(svc, "_execute_job_command", _exec)

    tasks = svc._dispatch_due_jobs(datetime.now())
    assert tasks[0] in svc._inflight_tasks  # strong ref held so the loop can't GC it
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=2.0)
    for _ in range(3):
        await asyncio.sleep(0)  # let the done-callback run
    assert svc._inflight_tasks == set()  # released on completion


async def test_stop_cancels_inflight_jobs(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    job = _due(_make_job(id="hang", command="x"))
    svc.jobs = {job.id: job}

    hang = asyncio.Event()  # never set — the job only ends via stop()'s cancel

    async def _exec(_j):
        await hang.wait()
        return "ok"

    monkeypatch.setattr(svc, "_execute_job_command", _exec)
    svc._running = True

    tasks = svc._dispatch_due_jobs(datetime.now())
    for _ in range(10):
        await asyncio.sleep(0)  # let it start and reach hang.wait()
    assert "hang" in svc._running_jobs

    await svc.stop()  # must reap the still-running background job, not hang forever

    assert tasks[0].cancelled()
    assert svc._inflight_tasks == set()
    assert "hang" not in svc._running_jobs  # _run_job's finally cleared the guard


# ─── reload-during-run: a running job's object is preserved, not detached ─────────
# Since the loop fires jobs fire-and-track (#611), an external `navig schedule` edit can
# trigger _reload_if_changed WHILE a job is mid-run. Replacing the running job's object
# with the disk snapshot would detach the in-flight task — its completion (status +
# next_run advance) would write to a dropped object, and the snapshot's already-past
# next_run would re-fire the job (a duplicate execution). _load_jobs now preserves the
# live object of any job in _running_jobs (that the reload still contains).


async def test_reload_preserves_running_job_object_and_updates_others(tmp_path):
    svc = _svc(tmp_path)
    running = _due(_make_job(id="run-1", command="x"))
    idle = _make_job(id="idle", command="y")
    svc.jobs = {running.id: running, idle.id: idle}
    svc._save_jobs()  # snapshot both to disk
    svc._running_jobs.add(running.id)  # run-1 is executing
    svc._jobs_file_mtime = None  # force _reload_if_changed to reload

    svc._reload_if_changed()

    assert svc.jobs["run-1"] is running  # LIVE object preserved (in-flight task stays attached)
    assert svc.jobs["idle"] is not idle  # a non-running job is reloaded fresh from disk
    assert "idle" in svc.jobs


async def test_reload_does_not_resurrect_an_externally_deleted_running_job(tmp_path):
    svc = _svc(tmp_path)
    running = _due(_make_job(id="run-1", command="x"))
    svc.jobs = {running.id: running}
    svc._save_jobs()
    svc._running_jobs.add(running.id)

    # External edit deletes the job while it runs.
    svc._get_jobs_path().write_text(json.dumps({"counter": 0, "jobs": []}), encoding="utf-8")
    svc._jobs_file_mtime = None

    svc._reload_if_changed()

    assert "run-1" not in svc.jobs  # a removed job is not resurrected despite _running_jobs


async def test_reload_during_run_does_not_refire_the_running_job(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    job = _due(_make_job(id="A", schedule="daily", command="x"))
    svc.jobs = {job.id: job}
    svc._save_jobs()

    runs = {"n": 0}
    release = asyncio.Event()

    async def _exec(_j):
        runs["n"] += 1
        await release.wait()
        return "ok"

    monkeypatch.setattr(svc, "_execute_job_command", _exec)

    # Fire A (fire-and-track); it starts and blocks mid-run.
    svc._dispatch_due_jobs(datetime.now())
    for _ in range(6):
        await asyncio.sleep(0)
    assert runs["n"] == 1 and "A" in svc._running_jobs

    # An external edit lands mid-run → reload. A's live object must be preserved.
    svc._save_jobs()  # stand-in for the external writer touching the file
    svc._jobs_file_mtime = None
    svc._reload_if_changed()
    assert svc.jobs["A"] is job  # preserved, not detached

    # A finishes → next_run advances to the future on the LIVE (persisted) object.
    release.set()
    await asyncio.wait_for(asyncio.gather(*svc._inflight_tasks), timeout=2.0)
    for _ in range(3):
        await asyncio.sleep(0)
    assert "A" not in svc._running_jobs
    assert svc.jobs["A"].next_run > datetime.now()  # advanced, not a stale past value

    # A must NOT re-fire — the detach/re-fire bug this guards against.
    svc._dispatch_due_jobs(datetime.now())
    for _ in range(6):
        await asyncio.sleep(0)
    assert runs["n"] == 1  # still one execution — no duplicate


# ─── restart mid-run is at-most-once (claim next_run before executing) ─────────────
# next_run used to advance only AFTER _run_job_locked completed, so a daemon killed
# mid-run left the on-disk next_run in the past → the job re-fired on reboot (a duplicate
# backup / deploy / reminder). _run_job_locked now claims + persists the next slot BEFORE
# running the command, and start() reconciles any job left RUNNING on disk.


async def test_next_run_claimed_and_persisted_before_execution(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    job = _due(_make_job(id="A", schedule="daily", command="x"))
    svc.jobs = {job.id: job}
    svc._save_jobs()  # persist the pre-run (past) next_run

    seen = {}

    async def _exec(_j):
        # When the command runs, the on-disk next_run must ALREADY be advanced (the claim) —
        # a crash at this instant would therefore NOT re-fire the job on restart.
        disk = json.loads(svc._get_jobs_path().read_text(encoding="utf-8"))
        seen["disk_next_run"] = disk["jobs"][0]["next_run"]
        return "ok"

    monkeypatch.setattr(svc, "_execute_job_command", _exec)
    await svc._run_job_locked(job)

    claimed = datetime.fromisoformat(seen["disk_next_run"])
    assert claimed > datetime.now()  # claim was persisted to disk before the command ran


async def test_restart_marks_a_running_job_as_interrupted(tmp_path):
    svc = _svc(tmp_path)
    job = _make_job(id="A", schedule="daily", command="x")
    job.last_status = JobStatus.RUNNING  # persisted mid-run (the claim already advanced next_run)
    job.next_run = datetime.now() + timedelta(days=1)
    svc.jobs = {job.id: job}
    svc._save_jobs()

    # Simulate a reboot: a fresh service loads the file, then start() reconciles.
    svc2 = _svc(tmp_path)
    assert svc2.jobs["A"].last_status is JobStatus.RUNNING  # loaded as RUNNING
    await svc2.start()
    try:
        assert svc2.jobs["A"].last_status is JobStatus.FAILED
        assert svc2.jobs["A"].last_output == "interrupted by restart"
        assert svc2.jobs["A"].next_run > datetime.now()  # claim preserved → not re-fired
    finally:
        await svc2.stop()


async def test_successful_run_still_advances_next_run_from_completion(tmp_path, monkeypatch):
    # The claim is only the crash-safety value; a normal run's next_run is still the
    # authoritative end-of-run recompute (cadence unchanged).
    svc = _svc(tmp_path)
    job = _due(_make_job(id="A", schedule="daily", command="x"))
    svc.jobs = {job.id: job}

    async def _ok(_j):
        return "ok"

    monkeypatch.setattr(svc, "_execute_job_command", _ok)
    await svc._run_job_locked(job)
    assert job.last_status is JobStatus.SUCCESS
    assert job.next_run > datetime.now()


async def test_claim_does_not_break_retry_scheduling(tmp_path, monkeypatch):
    # A failed job's ~5-min retry override must still win over the claim (which set the
    # normal daily next_run before the run).
    svc = _svc(tmp_path)
    svc.config.retry_failed = True
    job = _due(_make_job(id="A", schedule="daily", command="boom", max_retries=2))
    svc.jobs = {job.id: job}

    async def _boom(_j):
        raise RuntimeError("fail")

    monkeypatch.setattr(svc, "_execute_job_command", _boom)
    await svc._run_job_locked(job)
    assert job.last_status is JobStatus.FAILED
    assert job.retry_count == 1
    assert job.next_run < datetime.now() + timedelta(hours=1)  # ~5-min retry, not tomorrow
