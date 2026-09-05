"""A pending approval survives a restart, and a late answer is told it was too late.

`ApprovalManager` kept its pending requests in a dict and their waiters in
`asyncio.Future`s, and the turn's `finally` popped both the moment it gave up. Three silent
consequences, all closed here:

1. **A restart forgot.** Nothing on disk recorded what the operator had been asked.
2. **A timeout told nobody.** The expiry path logged at INFO and printed a narrator block —
   to a *terminal*. Under the daemon there is none, so an operator pinged on Telegram
   learned nothing.
3. **A late answer vanished.** `respond()` looked the id up in `_pending`, already popped,
   logged "Approval request not found" and returned False. A human tapped *Approve* and
   nothing anywhere said their decision had arrived too late to matter.

⚠ This is deliberately NOT durable turn-resume. The turn's own state lives in a coroutine
stack frame and cannot be persisted without converting the agent loop into a checkpointed
state machine — a separate design change. What is fixed here is *knowing what was lost*,
which is both useful on its own and the thing any future resume would be built on.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point config_dir at a temp dir so the journal writes nowhere real."""
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
def recorded(monkeypatch):
    """Capture incidents instead of writing them."""
    from navig.approval import manager as mgr

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        mgr._incidents, "record", lambda event, **data: events.append((event, data))
    )
    return events


def _manager(timeout_s: float = 0.05):
    from navig.approval.manager import ApprovalManager
    from navig.approval.policies import ApprovalPolicy

    policy = ApprovalPolicy()
    policy.enabled = True
    policy.timeout_seconds = timeout_s
    policy.default_action = "deny"
    return ApprovalManager(policy=policy)


# ── The journal ────────────────────────────────────────────────────────────────────


def test_a_pending_approval_is_written_down():
    from navig.approval import journal

    journal.record_pending(
        "req-1", command="rm -rf /", channel="telegram", user_id="42", level="DANGEROUS"
    )

    pending = journal.list_pending()
    assert "req-1" in pending, "nothing recorded — a restart would forget the request"
    assert pending["req-1"]["command"] == "rm -rf /"
    assert pending["req-1"]["channel"] == "telegram"


def test_the_record_survives_a_fresh_read():
    """The point of the journal: another process (after a restart) can enumerate it."""
    from navig.approval import journal

    journal.record_pending(
        "req-2", command="deploy", channel="cli", user_id="me", level="MODERATE"
    )

    # A fresh read is what a restarted daemon does — no in-memory state involved.
    assert "req-2" in journal.list_pending()

    assert journal.clear_pending("req-2") is not None
    assert "req-2" not in journal.list_pending()


def test_the_journal_never_raises(monkeypatch):
    """Bookkeeping must not be able to break an approval."""
    from navig.approval import journal

    monkeypatch.setattr(journal, "_store_path", lambda: (_ for _ in ()).throw(OSError("nope")))

    journal.record_pending("x", command="c", channel="cli", user_id="u", level="SAFE")
    assert journal.clear_pending("x") is None
    assert journal.list_pending() == {}


# ── Timeout and late answer ────────────────────────────────────────────────────────


async def test_a_timeout_is_reported_outside_the_terminal(recorded):
    from navig.core import incidents

    mgr = _manager(timeout_s=0.05)

    approved = await mgr.request_approval(
        "sudo rm -rf /", channel="telegram", user_id="42"
    )

    assert approved is False  # default_action=deny
    events = [e for e, _ in recorded]
    assert incidents.APPROVAL_EXPIRED in events, (
        f"a timed-out approval recorded nothing an operator can see: {events}"
    )


async def test_answering_after_the_timeout_says_so(recorded):
    """The human tapped Approve. Before, that produced 'request not found' and nothing else."""
    from navig.core import incidents

    mgr = _manager(timeout_s=0.05)

    request_ids: list[str] = []
    mgr.on_request(lambda req: request_ids.append(req.id))

    await mgr.request_approval("deploy prod", channel="telegram", user_id="42")
    assert request_ids, "no request id was published to the callback"

    applied = await mgr.respond(request_ids[0], approved=True)

    assert applied is False, "a late answer must not report success — it was not applied"
    events = [e for e, _ in recorded]
    assert incidents.APPROVAL_ANSWERED_TOO_LATE in events, (
        f"a late answer was discarded with no record: {events}"
    )


async def test_an_unknown_id_is_not_reported_as_a_late_answer(recorded):
    """Only ids we actually gave up on count — otherwise the signal means nothing."""
    from navig.core import incidents

    mgr = _manager()

    assert await mgr.respond("never-seen-this", approved=True) is False

    assert incidents.APPROVAL_ANSWERED_TOO_LATE not in [e for e, _ in recorded]


async def test_the_pending_record_is_cleared_when_the_turn_ends(recorded):
    """A restart must not resurrect a request nobody is waiting on."""
    from navig.approval import journal

    mgr = _manager(timeout_s=0.05)
    await mgr.request_approval("something", channel="cli", user_id="me")

    assert journal.list_pending() == {}, (
        "an expired request is still on disk — a restart would show it as outstanding"
    )


async def test_an_approval_that_is_answered_in_time_still_works(recorded):
    """The happy path must be untouched by any of this."""
    mgr = _manager(timeout_s=5.0)

    ids: list[str] = []
    mgr.on_request(lambda req: ids.append(req.id))

    task = asyncio.create_task(
        mgr.request_approval("git push", channel="cli", user_id="me")
    )
    for _ in range(100):
        await asyncio.sleep(0.01)
        if ids:
            break

    assert ids, "the request never published an id"
    assert await mgr.respond(ids[0], approved=True) is True
    assert await task is True

    from navig.approval import journal

    assert journal.list_pending() == {}
