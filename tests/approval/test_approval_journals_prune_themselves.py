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


def test_a_record_inside_a_long_resume_window_is_not_pruned(monkeypatch):
    """resume_max_age_seconds above a day: a record that may still execute must survive."""
    monkeypatch.setattr(resume, "max_age_seconds", lambda: 2 * 86400.0)
    _resumable("thirty-hours", age_s=30 * 3600)

    assert "thirty-hours" in resume.list_resumable()
    assert not resume.is_too_old(resume.list_resumable()["thirty-hours"])


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


def test_doctor_reports_a_really_unreadable_journal(monkeypatch):
    """No monkeypatched list_pending: a real read failure must reach COULD NOT VERIFY.

    The default readers swallow every error into `{}`, so the doctor's warning branch
    was unreachable on an actual I/O or permission failure.
    """
    import navig.core.json_io as json_io
    from navig.commands import doctor

    _pending("live", expires_in=600)

    def locked(*_a, **_kw):
        raise PermissionError("locked by another process")

    monkeypatch.setattr(json_io, "read_text_retrying", locked)

    rows = doctor.check_pending_approvals()

    assert len(rows) == 1
    assert rows[0][1] is False
    assert "COULD NOT VERIFY" in rows[0][2]


def test_the_default_view_still_degrades_to_empty(monkeypatch):
    import navig.core.json_io as json_io

    _pending("live", expires_in=600)
    monkeypatch.setattr(
        json_io, "read_text_retrying", lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
    )

    assert journal.list_pending() == {}


# ── concurrent writers ───────────────────────────────────────────────────────


@pytest.mark.parametrize("store", ["resume", "journal"])
def test_a_prune_never_overwrites_a_record_written_meanwhile(monkeypatch, store):
    """A prune is load → filter → save. Unserialized, a record() that landed between its
    load and its save was erased by the stale snapshot — and that approval could never
    resume. The writer must wait for the prune's lock, then add to what it saved."""
    import threading

    if store == "resume":
        mod = resume
        _resumable("month-old", age_s=34 * 86400)

        def write():
            resume.record("fresh", session_key="s", channel="mission", user_id="u", tool_name="x")

        prune = resume.prune_stale
    else:
        mod = journal
        _pending("weeks-old", expires_in=-14 * 86400)

        def write():
            _pending("fresh", expires_in=600)

        prune = journal.prune_expired

    real_load = mod._load_for_update
    writer = threading.Thread(target=write)
    fired = []

    def racing_load():
        data = real_load()
        if not fired:  # only the prune's own load races, not the writer's
            fired.append(1)
            writer.start()
            writer.join(0.5)  # unserialized, the write completes right here
        return data

    monkeypatch.setattr(mod, "_load_for_update", racing_load)
    assert prune() == 1
    writer.join(15)

    assert not writer.is_alive()
    assert "fresh" in mod._load(), "the concurrent record was erased by the prune"
