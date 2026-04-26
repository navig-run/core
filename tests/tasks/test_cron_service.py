"""
Tests for navig.scheduler.cron_service — JobStatus, CronConfig, CronJob, CronParser.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from navig.scheduler.cron_service import (
    CronConfig,
    CronJob,
    CronParser,
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
