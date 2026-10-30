"""`service start|restart` launch the daemon THROUGH the scheduled task on Windows.

Root cause of both daemon deaths on 2026-09-14 (20:01Z and 23:01Z), found in
the operator's own hourly process-sweep log:

    KILL  pythonw.exe  118488  ppid=62344 gone

A daemon Popen'd from the CLI has no living parent once the CLI exits — the
exact shape of an orphan — and the sweep kills orphaned dev-tool processes
(`pythonw` included) every hour at :01. The login-boot daemon, whose parent is
the Task Scheduler service, survived 147 sweeps; only CLI-restarted ones died.
So the CLI now starts the daemon through the task first, confirms it came up,
and falls back to the direct spawn only when it did not (no task, `/run`
refused, or IgnoreNew swallowed it).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands import service as svc
from navig.daemon import service_manager as sm

runner = CliRunner()


class _Daemon:
    """A daemon that appears after `appears_after` liveness polls."""

    def __init__(self, appears_after: int = 1) -> None:
        self.polls = 0
        self.appears_after = appears_after

    def is_running(self) -> bool:
        self.polls += 1
        return self.polls >= self.appears_after

    @staticmethod
    def read_pid() -> int:
        return 4242

    @staticmethod
    def stop_running_daemon() -> bool:
        return True


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_a, **_kw: None)
    spawned: list[bool] = []
    monkeypatch.setattr(svc, "_spawn_daemon_direct", lambda: spawned.append(True))
    return spawned


# ── the launcher ─────────────────────────────────────────────────────────────



class _WindowsOs:
    """`os` as service.py sees it on Windows, without touching the real module.

    Patching `svc.os.name` rewrote the GLOBAL `os.name`, so on Linux/macOS pathlib then
    tried to build `WindowsPath` objects and every command crashed before the code under
    test ran — these tests could only ever pass on Windows.
    """

    name = "nt"

    def __getattr__(self, attr):
        return getattr(os, attr)



def _pretend_windows(monkeypatch) -> None:
    monkeypatch.setattr(svc, "os", _WindowsOs())
    monkeypatch.setattr(svc.sys, "platform", "win32")  # the task path is gated on both
    monkeypatch.setattr(sm.sys, "platform", "win32")

def test_windows_launch_goes_through_the_task_and_never_spawns_directly(monkeypatch, fast):
    _pretend_windows(monkeypatch)
    monkeypatch.setattr(sm, "task_scheduler_run", lambda: (True, "started"))

    started, how = svc._launch_daemon(_Daemon(appears_after=2))

    assert (started, how) == (True, "scheduled task")
    assert fast == [], "a daemon that came up through the task must not be spawned a second time"


def test_a_run_the_scheduler_swallowed_falls_back_to_the_direct_spawn(monkeypatch, fast):
    """IgnoreNew: `schtasks /run` returns 0 and starts nothing. The CLI must notice
    (no daemon within the poll window) and spawn directly, as before."""
    _pretend_windows(monkeypatch)
    monkeypatch.setattr(sm, "task_scheduler_run", lambda: (True, "started"))
    monkeypatch.setattr(svc, "_wait_for_daemon", lambda d, **kw: bool(fast))  # only after a spawn

    started, how = svc._launch_daemon(_Daemon())

    assert fast == [True]
    assert (started, how) == (True, "direct spawn")


def test_no_task_installed_falls_back_to_the_direct_spawn(monkeypatch, fast):
    _pretend_windows(monkeypatch)
    monkeypatch.setattr(
        sm,
        "task_scheduler_run",
        lambda: (False, "ERROR: The system cannot find the file specified."),
    )

    started, how = svc._launch_daemon(_Daemon())

    assert fast == [True]
    assert (started, how) == (True, "direct spawn")


def test_posix_never_touches_task_scheduler(monkeypatch, fast):
    """Off Windows the scheduled task is never consulted.

    The fixture used to swap the whole `os` module for a `_PosixOs` stub, which is
    how the code chose its platform back then. It reads `sys.platform` now, so the
    stub had no effect and the running host's systemd unit answered first —
    `_wait_for_daemon` never ran (2026-10-30, outpost).
    """
    monkeypatch.setattr(svc.sys, "platform", "linux")
    monkeypatch.setattr(sm, "supervising_systemd_unit", lambda: None)

    def boom():
        raise AssertionError("task_scheduler_run must not be called off Windows")

    monkeypatch.setattr(sm, "task_scheduler_run", boom)

    started, how = svc._launch_daemon(_Daemon())

    assert fast == [True] and how == "direct spawn"


def test_a_stubbed_run_returning_none_is_not_a_success(monkeypatch, fast):
    """Older test doubles stub scheduler helpers with `lambda: None`."""
    _pretend_windows(monkeypatch)
    monkeypatch.setattr(sm, "task_scheduler_run", lambda: None)

    _, how = svc._launch_daemon(_Daemon())

    assert how == "direct spawn"


# ── ordering: the task is enabled BEFORE the launch ──────────────────────────


def test_restart_enables_the_task_before_launching(monkeypatch):
    """`restart` disables the task around the stop. `schtasks /run` refuses a
    disabled task, so the enable must come first or the task path is dead on
    exactly the flow it exists for."""
    order: list[str] = []
    _pretend_windows(monkeypatch)
    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", _Daemon())
    monkeypatch.setattr("time.sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr(sm, "task_scheduler_disable", lambda: order.append("disable"))
    monkeypatch.setattr(sm, "task_scheduler_enable", lambda: order.append("enable"))
    monkeypatch.setattr(sm, "clear_stop_flag", lambda: None)
    monkeypatch.setattr(sm, "clear_watchdog_deadline", lambda: None)
    monkeypatch.setattr(
        svc, "_launch_daemon", lambda d: (order.append("launch"), (True, "scheduled task"))[1]
    )
    monkeypatch.setattr(svc, "_is_elevated", lambda: True)

    result = runner.invoke(svc.service_app, ["restart"])

    assert result.exit_code == 0, result.output
    assert order.index("enable") < order.index("launch"), order
    assert "via scheduled task" in result.output


def test_start_enables_the_task_before_launching(monkeypatch):
    order: list[str] = []
    _pretend_windows(monkeypatch)
    daemon = _Daemon()
    daemon.polls = -10  # "not running" for the first checks that gate the start
    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", daemon)
    monkeypatch.setattr("time.sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr(sm, "stop_flag_is_set", lambda: False)
    monkeypatch.setattr(sm, "task_scheduler_enable", lambda: order.append("enable"))
    monkeypatch.setattr(
        svc, "_launch_daemon", lambda d: (order.append("launch"), (True, "scheduled task"))[1]
    )

    result = runner.invoke(svc.service_app, ["start"])

    assert result.exit_code == 0, result.output
    assert order.index("enable") < order.index("launch"), order


# ── the /run helper ──────────────────────────────────────────────────────────


def test_task_scheduler_run_refuses_under_pytest_by_default(monkeypatch):
    """The safety floor every task mutation has: a test must not start the
    operator's real daemon."""
    monkeypatch.delenv("NAVIG_ALLOW_TASK_MUTATION", raising=False)

    ok, msg = sm.task_scheduler_run()

    assert ok is False and "refusing" in msg


def test_task_scheduler_run_reports_what_schtasks_said(monkeypatch):
    monkeypatch.setenv("NAVIG_ALLOW_TASK_MUTATION", "1")
    monkeypatch.setattr(sm.sys, "platform", "win32")
    seen: dict = {}

    class R:
        def __init__(self, rc, err=b""):
            self.returncode, self.stderr, self.stdout = rc, err, b""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return R(1, b"ERROR: The task cannot be started because it is disabled.")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)

    ok, msg = sm.task_scheduler_run()

    assert ok is False and "disabled" in msg
    assert seen["cmd"][:2] == ["schtasks", "/run"] and sm.TASK_NAME in seen["cmd"]

    monkeypatch.setattr(sm.subprocess, "run", lambda cmd, **kw: R(0))
    assert sm.task_scheduler_run()[0] is True


def test_task_scheduler_run_is_windows_only(monkeypatch):
    monkeypatch.setenv("NAVIG_ALLOW_TASK_MUTATION", "1")
    monkeypatch.setattr(sm.sys, "platform", "linux")

    ok, msg = sm.task_scheduler_run()

    assert ok is False and "Windows" in msg



# ── the task runs the config home it was installed for ───────────────────────


def test_a_task_installed_for_another_config_home_is_not_run(monkeypatch, tmp_path):
    """`schtasks /run` would start the install-time brain; `_launch_daemon` then times out
    on THIS home's pid file and direct-spawns a second one. The run is refused instead."""
    monkeypatch.setenv("NAVIG_ALLOW_TASK_MUTATION", "1")
    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(sm, "_navig_home", lambda: tmp_path / "portable")
    monkeypatch.setattr(sm, "task_scheduler_config_dir", lambda: tmp_path / "installed")

    def boom(*_a, **_kw):
        raise AssertionError("a task baked for another config home must not be run")

    monkeypatch.setattr(sm.subprocess, "run", boom)

    ok, detail = sm.task_scheduler_run()

    assert ok is False and "installed" in detail


def test_a_task_installed_for_this_config_home_is_run(monkeypatch, tmp_path):
    import subprocess as _sp

    monkeypatch.setenv("NAVIG_ALLOW_TASK_MUTATION", "1")
    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(sm, "_navig_home", lambda: tmp_path / "home")
    monkeypatch.setattr(sm, "task_scheduler_config_dir", lambda: tmp_path / "home")
    calls = []
    monkeypatch.setattr(
        sm.subprocess, "run", lambda args, **kw: (calls.append(args), _sp.CompletedProcess(args, 0, b"", b""))[1]
    )

    assert sm.task_scheduler_run()[0] is True
    assert calls and calls[0][:2] == ["schtasks", "/run"]


def test_the_task_config_dir_is_read_back_from_the_installed_definition(monkeypatch, tmp_path):
    """Round trip: the home `_schtasks_xml` bakes is the home the reader returns."""
    import subprocess as _sp

    home = tmp_path / "custom-home"
    monkeypatch.setattr(sm, "_navig_home", lambda: home)
    xml = sm._schtasks_xml()
    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(
        sm.subprocess, "run", lambda *a, **kw: _sp.CompletedProcess(a, 0, xml, "")
    )

    assert sm.task_scheduler_config_dir() == Path(str(home).replace("\\", "/"))
