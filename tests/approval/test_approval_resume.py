"""A late approval now completes the action — exactly once, only if approved, only if fresh.

#1062 made a late answer visible. This makes it actionable: the operator taps Approve after
the turn ended and the action they authorised actually runs, with the result delivered back.

It is **re-entry, not resume**: the original turn's state lives in `run_agentic` (1312 lines,
35 awaits, 130 locals) inside a coroutine stack frame that cannot be serialized. A new turn
is started instead, seeded with the conversation the session store already owns.

Because this executes a privileged action *outside* the turn that asked for it, the safety
properties are the point of this file — each is tested for the failure, not just the success:

* **exactly once** — the record is TAKEN before anything runs, so a double-tap, a duplicate
  channel callback or a retry cannot run the tool twice;
* **approved only** — a denial has no path to execution;
* **fresh only** — an approval answered long after the fact is reported, not executed.
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


def _record(request_id: str = "r1", *, tool: str = "tool deploy_prod", asked_at=None):
    from navig.approval import resume

    resume.record(
        request_id,
        session_key="telegram:chat42",
        channel="telegram",
        user_id="42",
        tool_name=tool,
    )
    if asked_at is not None:  # age it
        data = resume._load()
        data[request_id]["asked_at"] = asked_at
        resume._save(data)


class _Spy:
    """Records what ran and what was delivered."""

    def __init__(self, reply: str = "done") -> None:
        self.runs: list[dict] = []
        self.delivered: list[tuple[str, str]] = []
        self._reply = reply

    async def run_turn(self, entry):
        self.runs.append(entry)
        return self._reply

    async def deliver(self, title, body):
        self.delivered.append((title, body))


async def test_a_late_approval_runs_the_action_and_reports_back():
    from navig.approval import resume

    _record()
    spy = _Spy(reply="Deployed. 3 services updated.")

    ran = await resume.resume_after_approval("r1", run_turn=spy.run_turn, deliver=spy.deliver)

    assert ran is True
    assert len(spy.runs) == 1
    assert spy.runs[0]["session_key"] == "telegram:chat42"
    assert spy.delivered and "3 services updated" in spy.delivered[0][1]


async def test_it_runs_exactly_once_even_if_answered_twice():
    """The double-tap case. This is the property that prevents duplicate side effects."""
    from navig.approval import resume

    _record()
    spy = _Spy()

    first = await resume.resume_after_approval("r1", run_turn=spy.run_turn, deliver=spy.deliver)
    second = await resume.resume_after_approval("r1", run_turn=spy.run_turn, deliver=spy.deliver)

    assert first is True
    assert second is False, "a second answer re-ran the action — duplicate side effects"
    assert len(spy.runs) == 1, f"the tool ran {len(spy.runs)} times, must be exactly 1"


async def test_an_unknown_id_runs_nothing():
    from navig.approval import resume

    spy = _Spy()
    assert await resume.resume_after_approval("never", run_turn=spy.run_turn, deliver=spy.deliver) is False
    assert spy.runs == []


async def test_a_stale_approval_is_reported_but_not_executed():
    """Tapping Approve on a week-old notification must not deploy to production."""
    from navig.approval import resume

    _record(asked_at=time.time() - (resume.max_age_seconds() + 60))
    spy = _Spy()

    ran = await resume.resume_after_approval("r1", run_turn=spy.run_turn, deliver=spy.deliver)

    assert ran is False
    assert spy.runs == [], "a stale approval executed the action"
    assert spy.delivered, "a stale approval ran nothing AND said nothing"
    assert "too late" in spy.delivered[0][0].lower()


async def test_the_stale_record_is_still_consumed():
    """Otherwise it sits on disk forever, re-reported on every subsequent tap."""
    from navig.approval import resume

    _record(asked_at=time.time() - (resume.max_age_seconds() + 60))
    spy = _Spy()

    await resume.resume_after_approval("r1", run_turn=spy.run_turn, deliver=spy.deliver)

    assert resume.take("r1") is None


async def test_the_max_age_is_configurable(monkeypatch):
    import navig.config as config_mod
    from navig.approval import resume

    class _CM:
        def get(self, key, default=None):
            return 10.0 if key == "approval.resume_max_age_seconds" else default

    monkeypatch.setattr(config_mod, "get_config_manager", lambda *a, **k: _CM())
    assert resume.max_age_seconds() == 10.0


async def test_a_failing_turn_is_reported_not_swallowed():
    from navig.approval import resume

    _record()
    spy = _Spy()

    async def boom(entry):
        raise RuntimeError("the tool exploded")

    ran = await resume.resume_after_approval("r1", run_turn=boom, deliver=spy.deliver)

    assert ran is False
    assert spy.delivered, "a failed resume told the operator nothing"
    assert "could not finish" in spy.delivered[0][0].lower()


# ── The manager side ───────────────────────────────────────────────────────────────


def _manager(timeout_s: float = 0.05):
    from navig.approval.manager import ApprovalManager
    from navig.approval.policies import ApprovalPolicy

    policy = ApprovalPolicy()
    policy.enabled = True
    policy.timeout_seconds = timeout_s
    policy.default_action = "deny"
    return ApprovalManager(policy=policy)


async def test_a_timeout_leaves_a_resumable_record():
    """The whole point: after the turn gives up, the answer can still land."""
    from navig.approval import resume

    mgr = _manager()
    ids: list[str] = []
    mgr.on_request(lambda req: ids.append(req.id))

    await mgr.request_approval("tool deploy", channel="telegram", user_id="42")

    assert ids
    assert resume.take(ids[0]) is not None, (
        "an expired approval left nothing to resume — a late Approve can do nothing"
    )


async def test_a_decision_made_IN_TIME_leaves_no_resumable_record():
    """A turn that already acted must not be re-enterable — that would double-execute."""
    import asyncio

    from navig.approval import resume

    mgr = _manager(timeout_s=5.0)
    ids: list[str] = []
    mgr.on_request(lambda req: ids.append(req.id))

    task = asyncio.create_task(
        mgr.request_approval("tool deploy", channel="telegram", user_id="42")
    )
    for _ in range(100):
        await asyncio.sleep(0.01)
        if ids:
            break
    assert ids
    await mgr.respond(ids[0], approved=True)
    await task

    assert resume.take(ids[0]) is None, (
        "an approval answered in time is still resumable — a stray late tap would run the "
        "tool a second time"
    )


async def test_a_late_DENIAL_never_resumes():
    from navig.approval import resume

    mgr = _manager()
    ids: list[str] = []
    mgr.on_request(lambda req: ids.append(req.id))

    await mgr.request_approval("tool deploy", channel="telegram", user_id="42")
    assert ids

    await mgr.respond(ids[0], approved=False)

    assert resume.take(ids[0]) is None, "a denial left the action resumable"
