"""`kill_processes` must turn a refused kill into a report entry, never a crash.

Regression 2026-10-15: `navig kill claude-idle` killed one session, hit
AccessDenied on the next (VS Code-owned), recorded it as failed — and then
psutil.wait_procs() tried to OpenProcess the SAME refused pid and raised
psutil.AccessDenied uncaught, taking the whole CLI down with a crash dump
while half the work was already done.
"""

from __future__ import annotations

import psutil

from navig.commands import processes as P


class FakeProc:
    """A psutil.Process stand-in whose kill() can be made to fail."""

    def __init__(self, pid: int, kill_error: Exception | None = None):
        self.pid = pid
        self._kill_error = kill_error
        self.killed = False

    def kill(self):
        if self._kill_error:
            raise self._kill_error
        self.killed = True

    def terminate(self):
        self.kill()

    def name(self):
        return f"fake-{self.pid}"

    def is_running(self):
        return not self.killed


def _info(pid: int) -> dict:
    return {"pid": pid, "name": f"fake-{pid}", "cmdline": [], "username": "", "created_at": ""}


def test_refused_kill_is_reported_and_never_reaches_wait(monkeypatch):
    denied = FakeProc(11, kill_error=psutil.AccessDenied(pid=11))
    ok = FakeProc(12)
    monkeypatch.setattr(P.psutil, "Process", lambda pid: denied if pid == 11 else ok)
    waited: list = []

    def fake_wait(procs, timeout=None):
        waited.extend(procs)
        return list(procs), []

    monkeypatch.setattr(P.psutil, "wait_procs", fake_wait)

    report = P.kill_processes([_info(11), _info(12)], force=True, exclude_self=False)

    assert len(waited) == 1, "the refused pid must never be waited on (that is what crashed)"
    assert waited[0] is ok
    assert [f["pid"] for f in report["failed"]] == [11]
    assert "access denied" in report["failed"][0]["reason"]
    assert [k["pid"] for k in report["killed"]] == [12]


def test_wait_failure_degrades_to_report(monkeypatch):
    ok = FakeProc(20)
    monkeypatch.setattr(P.psutil, "Process", lambda pid: ok)

    def exploding_wait(procs, timeout=None):
        raise psutil.AccessDenied(pid=20)

    monkeypatch.setattr(P.psutil, "wait_procs", exploding_wait)
    report = P.kill_processes([_info(20)], force=True, exclude_self=False)
    assert any("wait failed" in f["reason"] for f in report["failed"]), "a wait crash must degrade, not escape"


def test_already_exited_process_is_recorded_as_gone(monkeypatch):
    class Gone(FakeProc):
        def kill(self):
            raise psutil.NoSuchProcess(pid=self.pid)

    monkeypatch.setattr(P.psutil, "Process", lambda pid: Gone(30))
    monkeypatch.setattr(P.psutil, "wait_procs", lambda procs, timeout=None: ([], list(procs)))
    report = P.kill_processes([_info(30)], force=True, exclude_self=False)
    assert [k["pid"] for k in report["killed"]] == [30]
    assert report["failed"] == []


def test_safe_name_survives_a_denied_process():
    class Hostile:
        def name(self):
            raise psutil.AccessDenied(pid=1)

    assert P._safe_name(Hostile()) == ""  # type: ignore[arg-type]