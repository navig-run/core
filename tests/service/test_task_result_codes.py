"""A RUNNING autostart task is not a FAILED one.

Task Scheduler reports SCHED_S_* informational codes through the same
`LastTaskResult` field as a real exit code, so `!= 0` does not mean "failed".

`267009` (0x41301, SCHED_S_TASK_RUNNING) is the one that bites, and it is not
transient: Task Scheduler holds a task in Running for as long as the process it
launched is alive, and the daemon is long-lived. So once the watchdog has actually
STARTED the daemon -- the entire reason the repeating trigger exists -- the result
stays 267009 for the rest of that daemon's life.

Measured on the operator's machine 2026-09-04. The watchdog recovered a dead daemon
at 21:05:01 (PID 42376, parent svchost.exe, i.e. Task Scheduler), and from then on::

    navig service status
      Task Scheduler: Installed but NOT healthy
        ! last run FAILED (result 267009)

The recovery had just worked perfectly. A row that reports failure on success
trains the operator to ignore it -- the same harm as a green light over an unknown,
pointed the other way. It also only became reachable when the repeating trigger was
restored, so it arrived with the feature.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from navig.daemon import service_manager as sm


def _health(monkeypatch: pytest.MonkeyPatch, *, last: int, nxt: str = "2026-01-01T00:00:00"):
    payload = json.dumps({"last": last, "next": nxt, "reps": 1})
    monkeypatch.setattr(sm.sys, "platform", "win32")

    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(a[0] if a else [], 0, payload, "")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)
    return sm.task_scheduler_health()


def test_a_running_task_is_not_reported_as_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE regression: 267009 is SCHED_S_TASK_RUNNING."""
    out = _health(monkeypatch, last=267009)
    assert not any("FAILED" in p for p in out["problems"]), (
        "a task that is currently RUNNING was reported as a failed run -- which is "
        f"the steady state after the watchdog starts the daemon. problems={out['problems']}"
    )


@pytest.mark.parametrize("code", [0, 267008, 267011])
def test_the_other_non_failure_codes_are_quiet(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """S_OK, SCHED_S_TASK_READY, SCHED_S_TASK_HAS_NOT_RUN."""
    out = _health(monkeypatch, last=code)
    assert not any("FAILED" in p for p in out["problems"]), out["problems"]


def test_a_refused_trigger_is_not_a_failed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """0x800710E0 = the trigger was refused because an instance was already running.

    That is MultipleInstancesPolicy=IgnoreNew doing its job: a watchdog firing must
    not start a second daemon. And because Task Scheduler holds an instance Running
    for as long as the process it launched lives, this is the STEADY state on a
    healthy machine -- the instance that started the daemon stays Running and every
    later firing is refused. Measured 2026-09-05: a healthy install with the uplink
    online reported "last run FAILED (result 2147946720)" on every check.
    """
    out = _health(monkeypatch, last=2147946720)
    assert not any("FAILED" in p for p in out["problems"]), (
        "a trigger refused because the daemon was already running was reported as a "
        f"failed run -- the permanent steady state. problems={out['problems']}"
    )


@pytest.mark.parametrize("code", [1, 2, 267014, 2147942401])
def test_a_genuinely_failed_run_is_still_reported(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """The row must keep its teeth: a real non-zero exit, and SCHED_S_TASK_TERMINATED
    (0x41306) which means someone stopped it, are still worth surfacing."""
    out = _health(monkeypatch, last=code)
    assert any("FAILED" in p for p in out["problems"]), (
        f"result {code} should still be reported as a failed run: {out['problems']}"
    )
