"""The approval journals prune themselves, so `doctor` never counts the unanswerable.

The operator's `navig doctor` read "Waiting on you: 6 approval(s) unanswered (most
recent 20192m ago)" and "67 approved-too-late record(s)". A gateway that dies or
restarts mid-wait never reaches the `finally` that clears the record, and nothing
pruned either store — a warning that can never go green is one the operator
learns to skip.
"""

from __future__ import annotations

import time

import pytest

from navig.approval import journal, resume


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))


def _pending(rid: str, *, expires_in: float | None) -> None:
    journal.record_pending(
        rid,
        command="tool bash_exec",
        channel="mission",
        user_id="system",
        level="CONFIRM",
        expires_at=None if expires_in is None else time.time() + expires_in,
    )


# ── pending.json ─────────────────────────────────────────────────────────────


def test_an_expired_pending_record_is_pruned_on_read():
    _pending("weeks-old", expires_in=-14 * 86400)
    _pending("live", expires_in=600)

    assert set(journal.list_pending()) == {"live"}
    assert set(journal._load()) == {"live"}, "pruned from the file, not just the view"


def test_a_just_expired_record_is_kept_for_the_grace_window():
    """A tap that lands a moment after the deadline must still be recognised."""
    _pending("moments-ago", expires_in=-60)

    assert "moments-ago" in journal.list_pending()


def test_a_record_with_no_deadline_is_unknown_not_expired():
    _pending("no-deadline", expires_in=None)

    assert "no-deadline" in journal.list_pending()


def test_prune_reports_how_many_it_dropped():
    _pending("a", expires_in=-86400)
    _pending("b", expires_in=-86400)
    _pending("c", expires_in=600)

    assert journal.prune_expired() == 2
    assert journal.prune_expired() == 0


def test_a_malformed_expiry_is_kept():
    journal.record_pending("odd", command="x", channel="cli", user_id="u", level="CONFIRM")
    data = journal._load()
    data["odd"]["expires_at"] = "not-a-number"
    journal._save(data)

    assert "odd" in journal.list_pending()


# ── resumable.json ───────────────────────────────────────────────────────────


def _resumable(rid: str, *, age_s: float) -> None:
    resume.record(rid, session_key="s", channel="mission", user_id="system", tool_name="x")
    data = resume._load()
    data[rid]["asked_at"] = time.time() - age_s
    resume._save(data)


def test_a_day_old_resume_record_is_pruned_on_read():
    _resumable("month-old", age_s=34 * 86400)
    _resumable("hours-old", age_s=3 * 3600)

    assert set(resume.list_resumable()) == {"hours-old"}


def test_a_too_old_but_recent_record_is_kept_so_a_late_tap_is_named():
    """Between max_age (1 h) and the keep window (1 d) the record's job is to turn
    "unknown id" into "too old to resume" — it must survive."""
    _resumable("two-hours", age_s=2 * 3600)

    assert resume.is_too_old(resume.list_resumable()["two-hours"])
    assert "two-hours" in resume.list_resumable()


def test_prune_stale_honours_an_explicit_horizon():
    _resumable("x", age_s=3600)

    assert resume.prune_stale(keep_s=600) == 1
    assert resume.list_resumable() == {}


# ── doctor ───────────────────────────────────────────────────────────────────


def test_doctor_goes_quiet_when_every_record_is_stale():
    from navig.commands import doctor

    _pending("stale", expires_in=-10 * 86400)
    _resumable("stale-r", age_s=30 * 86400)

    assert doctor.check_pending_approvals() == []


def test_doctor_still_warns_on_a_live_pending_approval():
    from navig.commands import doctor

    _pending("live", expires_in=600)

    rows = doctor.check_pending_approvals()

    assert len(rows) == 1 and rows[0].label == "Waiting on you"
