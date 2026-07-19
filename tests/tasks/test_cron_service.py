"""
Tests for navig.scheduler.cron_service — JobStatus, CronConfig, CronJob, CronParser.
"""

from __future__ import annotations

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
