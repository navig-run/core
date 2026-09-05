"""The durable approval record must be READ, not just written.

#1062 persisted pending approvals and #1064 made a late Approve complete the action. Both
stored state on disk — and both resolution paths then consulted only **in-memory** maps:

    request = self._pending.get(request_id)          # in-memory
    late    = self._recently_resolved.get(...)       # ALSO in-memory

So after a daemon restart every id looked unknown, `respond()` logged "Approval request not
found", and the operator's tap did nothing — while `resumable.json` on disk held everything
needed to act. **The durability did not survive the restart it exists for.** A record written
and never read is the same shape as the bug it was built to fix.

The same omission had a second face: `journal.list_pending()` had zero production readers, so
the daemon knew something was outstanding and no surface could show it. `navig doctor` now
reads it.

A restart is simulated by constructing a **fresh `ApprovalManager`** — its `_pending` and
`_recently_resolved` start empty, which is exactly the post-restart state, while the on-disk
record persists.
"""

from __future__ import annotations

import time

import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    # ⚠ setenv ONLY — do NOT `monkeypatch.setattr(paths, "config_dir", …)`. `config_dir()`
    # re-reads `NAVIG_CONFIG_DIR` on every call, so the env var is sufficient; patching the
    # global resolver additionally poisons whatever has already CACHED a config dir from it,
    # and monkeypatch restores the attribute but not the cache. That reddened two unrelated
    # rows in tests/commands/test_doctor_vault.py — reproduced on clean main — and it is the
    # second time this exact pair has been broken this way.
    yield


@pytest.fixture
def spawned(monkeypatch):
    """Capture what would be spawned, so the resume is observable without running a turn."""
    from navig.approval import manager as mgr

    started: list[str] = []

    def _fake_spawn(coro, **kw):
        started.append(getattr(coro, "cr_code", None) and coro.cr_code.co_name or "coro")
        coro.close()          # we are not running it; don't leak a pending coroutine
        return None

    import navig.core.background as bg

    monkeypatch.setattr(bg, "spawn", _fake_spawn)
    monkeypatch.setattr(mgr, "_incidents", mgr._incidents)
    return started


def _manager(timeout_s: float = 0.05):
    from navig.approval.manager import ApprovalManager
    from navig.approval.policies import ApprovalPolicy

    policy = ApprovalPolicy()
    policy.enabled = True
    policy.timeout_seconds = timeout_s
    policy.default_action = "deny"
    return ApprovalManager(policy=policy)


async def _ask(mgr) -> str:
    ids: list[str] = []
    mgr.on_request(lambda req: ids.append(req.id))
    await mgr.request_approval("tool deploy_prod", channel="telegram", user_id="42")
    assert ids, "the request published no id"
    return ids[0]


async def test_an_approval_after_a_restart_is_recognised_and_resumed(spawned):
    """The case the durability was built for, and the one it did not handle."""
    asked_on = _manager()
    request_id = await _ask(asked_on)

    after_restart = _manager()          # fresh maps == a restarted daemon
    assert after_restart._pending == {}
    assert after_restart._recently_resolved.get(request_id) is None

    applied = await after_restart.respond(request_id, approved=True)

    assert applied is False, "a post-restart answer is not applied inline — it re-enters"
    assert spawned, (
        "a post-restart Approve started nothing — the durable record was never consulted, "
        "so the operator's tap did nothing"
    )


async def test_a_denial_after_a_restart_consumes_the_record(spawned):
    from navig.approval import resume

    request_id = await _ask(_manager())

    await _manager().respond(request_id, approved=False)

    assert spawned == [], "a DENIAL started a resume"
    assert resume.peek(request_id) is None, "a denied record was left resumable"


async def test_a_genuinely_unknown_id_still_does_nothing(spawned):
    """The signal is only worth anything if it does not fire for strays."""
    assert await _manager().respond("no-such-request", approved=True) is False
    assert spawned == []


async def test_peek_does_not_consume_the_single_use_token():
    """`respond` peeks; only `resume_after_approval` may take. Otherwise the exactly-once
    guarantee is spent by merely *looking*, and the action never runs."""
    from navig.approval import resume

    request_id = await _ask(_manager())

    assert resume.peek(request_id) is not None
    assert resume.peek(request_id) is not None, "peek consumed the record"
    assert resume.take(request_id) is not None, "take found nothing after peeking"


# ── The operator-facing read ───────────────────────────────────────────────────────


def test_doctor_is_silent_when_nothing_is_waiting():
    from navig.commands.doctor import check_pending_approvals

    assert check_pending_approvals() == []


def test_doctor_reports_an_unanswered_approval():
    from navig.approval import journal
    from navig.commands.doctor import check_pending_approvals

    journal.record_pending(
        "r1", command="tool deploy_prod", channel="telegram", user_id="42",
        level="DANGEROUS",
    )

    rows = check_pending_approvals()

    assert rows, (
        "doctor showed nothing while an approval was outstanding — the durable record "
        "still has no reader"
    )
    label, ok, detail = rows[0]
    assert ok is False, "an unanswered approval must not render as a green tick"
    assert "1 approval" in detail


def test_doctor_does_not_report_an_unreadable_journal_as_nothing_waiting(monkeypatch):
    """A failed READ must not render as "nothing is waiting".

    `check_pending_approvals` returned [] on any exception, and [] means doctor prints no
    row at all — indistinguishable from a healthy install with an empty queue. That is the
    reassuring answer to an unanswerable question, and the same shape this whole function
    exists to fix: a durable record that no surface displays. The sibling
    `_webhook_tenant_rows` already reports this case as `COULD NOT VERIFY (...)`.
    """
    from navig.approval import journal
    from navig.commands.doctor import check_pending_approvals

    def boom():
        raise OSError("journal unreadable (disk full / permissions)")

    monkeypatch.setattr(journal, "list_pending", boom)

    rows = check_pending_approvals()

    assert rows, "an unreadable approvals journal rendered as 'nothing waiting'"
    label, ok, detail = rows[0]
    assert ok is False, "could-not-verify must not render as a green tick"
    assert "COULD NOT VERIFY" in detail
    assert "unreadable" in detail, f"the reason must reach the operator: {detail}"


def test_doctor_flags_records_too_old_to_resume():
    from navig.approval import resume
    from navig.commands.doctor import check_pending_approvals

    resume.record(
        "r2", session_key="telegram:42", channel="telegram", user_id="42",
        tool_name="tool deploy_prod",
    )
    data = resume._load()
    data["r2"]["asked_at"] = time.time() - (resume.max_age_seconds() + 60)
    resume._save(data)

    rows = check_pending_approvals()

    assert any("Too old" in label for label, _ok, _d in rows), (
        f"a stale resumable record was not surfaced: {rows}"
    )


def test_doctor_never_raises_on_a_broken_store(monkeypatch):
    """A health check must not be the thing that breaks.

    This asserted `== []`, which over-specified the stated intent: it pinned not just
    "does not raise" but also "reports nothing", and reporting nothing is what made an
    unreadable journal look like an empty queue. The intent is kept here; what the row
    must SAY is asserted by
    test_doctor_does_not_report_an_unreadable_journal_as_nothing_waiting.
    """
    from navig.approval import journal
    from navig.commands.doctor import check_pending_approvals

    monkeypatch.setattr(
        journal, "list_pending", lambda: (_ for _ in ()).throw(OSError("nope"))
    )
    rows = check_pending_approvals()  # must not raise
    assert isinstance(rows, list)
