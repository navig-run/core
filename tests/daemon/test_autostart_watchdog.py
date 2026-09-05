"""The autostart task's real capabilities, and the honesty of what it reports.

An operator lost two days of daily check-ins: the daemon died, the Task Scheduler
entry had only a logon trigger (so it never fired again while they stayed logged
in) and its last run had FAILED -- while ``navig service status`` printed
``Task Scheduler: Active``.

The honest status half of that shipped. The *recovery* half did NOT, and these
tests pin why, so nobody re-adds it without doing the missing work first.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from navig.daemon import service_manager as sm


@pytest.fixture
def task_xml() -> str:
    return sm._schtasks_xml()


# ── The XML has to actually be installable ───────────────────────────────────


def test_the_task_xml_is_well_formed(task_xml: str) -> None:
    """Parses for real. This has caught the same mistake twice.

    A DOUBLE HYPHEN inside an XML comment is illegal, and the explanatory
    comments in this definition are long and keep attracting one. An unparseable
    definition means ``schtasks /create`` fails and the autostart task is simply
    never installed -- a silent loss of the whole feature.
    """
    ET.fromstring(task_xml)  # noqa: S314 - our own generated XML, not untrusted input


def test_no_comment_contains_a_double_hyphen(task_xml: str) -> None:
    """The specific illegal token, named, so the failure explains itself."""
    for chunk in task_xml.split("<!--")[1:]:
        body = chunk.split("-->", 1)[0]
        assert "--" not in body, (
            "an XML comment in the task definition contains '--', which is "
            "illegal and makes the whole definition unparseable: "
            f"...{body[:80]}..."
        )


# ── Recovery: deliberately absent, and this records why ──────────────────────


def test_the_task_HAS_a_repeating_trigger(task_xml: str) -> None:
    """The recovery half, added only once the bootstrap became idempotent.

    A detached daemon completes its Task Scheduler instance immediately, so
    RestartOnFailure never applies, and a LogonTrigger does not fire again while
    the user stays logged in. Without this, a dead daemon stayed dead and an
    operator lost days of daily check-ins with every status light green.

    This was withheld once, on measurement: with supervisor 8732 healthy, two
    further firings produced 8968 and 75516 and the NEWCOMER took over the pid
    file -- three supervisors and two gateways, growing every interval. What
    changed is `navig.daemon.entry`, which now returns cleanly when a daemon is
    already up (see test_the_repetition_is_only_safe_while_the_guard_exists).

    Verified against real Windows Task Scheduler, not just the string: the XML
    registers, and the trigger reads back as MSFT_TaskTimeTrigger with
    `Interval = PT5M`, empty `Duration` (indefinite) and a NextRunTime on the
    next 5-minute boundary. The old task had NO next run at all.
    """
    assert "<TimeTrigger>" in task_xml
    assert "<Repetition>" in task_xml
    assert "<Interval>PT5M</Interval>" in task_xml, (
        "PT5M is measured: a no-op duplicate launch costs ~0.6s, so 288 firings a "
        "day is ~3 min of CPU, bounding recovery latency at 5 minutes"
    )
    assert "<Duration>" not in task_xml, (
        "an absent Duration repeats INDEFINITELY; adding one silently stops the "
        "watchdog after that window and the task goes back to not recovering"
    )


def test_the_repetition_is_only_safe_while_the_guard_exists(task_xml: str) -> None:
    """THE INTERLOCK. Do not delete either half without the other.

    A repeating trigger is safe ONLY because `navig.daemon.entry.main()` asks
    `NavigDaemon.is_running()` and returns instead of building a second daemon.
    Remove that guard and this trigger multiplies supervisors every 5 minutes --
    the exact failure that made this trigger unshippable the first time.

    So the two are pinned together here: if the guard goes, this test fails and
    names the trigger that must go with it.
    """
    import inspect

    from navig.daemon import entry

    source = inspect.getsource(entry.main)
    assert "is_running()" in source, (
        "navig.daemon.entry.main() no longer checks whether a daemon is already "
        "running -- the repeating TimeTrigger in _schtasks_xml() MUST be removed "
        "in the same change, or every firing starts another supervisor"
    )
    assert "<Repetition>" in task_xml  # the half this interlock protects


def test_the_logon_trigger_is_still_there(task_xml: str) -> None:
    assert "<LogonTrigger>" in task_xml


def test_restart_on_failure_is_present_but_does_not_cover_a_detached_daemon(
    task_xml: str,
) -> None:
    """Kept, and known to be insufficient -- which is why status must be honest.

    The daemon detaches, so the launched process exits immediately and Task
    Scheduler marks the instance COMPLETE. RestartOnFailure only applies to an
    instance still running, so from then on there is nothing left to restart.
    """
    assert "<RestartOnFailure>" in task_xml


# ── The honesty half (this DID ship) ─────────────────────────────────────────


def _health(monkeypatch, *, rc: int = 0, last: int = 0,
            nxt: str = "2026-01-01T00:00:00", reps: int = 1,
            enabled: bool | None = True):
    import json as _json
    import subprocess as _sp

    data = {"last": last, "next": nxt, "reps": reps}
    if enabled is not None:  # None models an older reply with no `enabled` field
        data["enabled"] = enabled
    payload = _json.dumps(data)
    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(
        _sp, "run",
        lambda *a, **k: _sp.CompletedProcess(a[0] if a else [], rc, payload, ""),
    )
    monkeypatch.setattr(sm, "subprocess", _sp)
    return sm.task_scheduler_health()


def test_a_failed_last_run_is_reported_not_swallowed(monkeypatch) -> None:
    h = _health(monkeypatch, last=1)
    assert h["last_result"] == 1
    assert any("FAILED" in p for p in h["problems"])


def test_no_next_run_means_it_cannot_recover(monkeypatch) -> None:
    """The operator's real state: installed, and it will never fire again."""
    h = _health(monkeypatch, nxt="")
    assert h["next_run"] is None
    assert h["can_recover"] is False
    assert any("cannot restart" in p for p in h["problems"])


def test_a_task_with_a_next_run_and_repetition_reports_recoverable(monkeypatch) -> None:
    h = _health(monkeypatch)
    assert h["installed"] is True
    assert h["can_recover"] is True
    assert h["problems"] == []


def test_a_DISABLED_task_is_never_healthy(monkeypatch) -> None:
    """The bug this test exists for, seen on the operator's machine.

    `navig service status` printed

        Task Scheduler: Healthy (watchdog re-checks, next 2026-09-04T18:20:00)
          Detail: installed but DISABLED - it will not start the daemon at logon.

    A green headline with the contradiction demoted to the line underneath. The health
    probe asked for last / next / reps and never for the ENABLED state.

    Note the fixture: a full next_run AND a repetition, exactly as Windows reports them
    for a switched-off task. That is the trap -- Windows computes the schedule whether
    or not it will ever act on it, so "has a next run" is not evidence of anything.
    """
    h = _health(monkeypatch, enabled=False, nxt="2026-01-01T00:00:00", reps=1)

    assert h["enabled"] is False
    assert h["can_recover"] is False, (
        "a disabled task fires nothing -- its next_run and repetition describe "
        "something that cannot happen"
    )
    assert h["problems"], "a disabled task reported no problem at all"
    assert "DISABLED" in h["problems"][0], (
        "the disabled state must come FIRST: it outranks every other finding, "
        f"got {h['problems']}"
    )
    assert "navig service start" in h["problems"][0], "say how to fix it"


def test_a_disabled_task_does_not_also_claim_a_missing_trigger(monkeypatch) -> None:
    """One cause, one problem. Piling on a second, wrong reason buries the real one."""
    h = _health(monkeypatch, enabled=False, reps=0)
    assert not any("no repeating trigger" in p for p in h["problems"]), (
        "the trigger is present and irrelevant -- the task is off"
    )


def test_a_reply_without_the_enabled_field_assumes_enabled(monkeypatch) -> None:
    """Back-compat: an older/partial reply must not invent a failure.

    Every other field still means something, so defaulting to "on" keeps the row
    honest about what it DID observe rather than fabricating a fault.
    """
    h = _health(monkeypatch, enabled=None)
    assert h["enabled"] is True
    assert h["can_recover"] is True
    assert h["problems"] == []


def test_an_unreadable_task_is_unknown_never_healthy(monkeypatch) -> None:
    """Could-not-look must be a warning, not a green light (the doctor rule)."""
    import subprocess as _sp

    monkeypatch.setattr(sm.sys, "platform", "win32")

    def boom(*a, **k):
        raise OSError("powershell missing")

    monkeypatch.setattr(_sp, "run", boom)
    monkeypatch.setattr(sm, "subprocess", _sp)
    h = sm.task_scheduler_health()
    assert h["installed"] is None
    assert h["problems"]
