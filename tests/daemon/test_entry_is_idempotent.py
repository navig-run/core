"""Launching the daemon twice must not produce two daemons.

`navig.daemon.entry` is what the Windows scheduled task runs
(`runpy.run_module('navig.daemon.entry')`) and what any tray/startup script
reaches for. Only the CLI path (`navig service start`) used to ask "is one
already running?", so every other launcher started a daemon unconditionally.

Measured before the fix: with supervisor 8732 healthy and serving, two further
launches produced supervisors 8968 and 75516, and daemon/state.json showed the
NEWCOMER had taken over the pid file -- three supervisors and two gateways.

This is also the precondition for giving the autostart task a repeating trigger
(withdrawn in #1180): a watchdog that re-runs this entry every few minutes is
only safe once a duplicate launch is a no-op.
"""

from __future__ import annotations

import os

import pytest

import navig.daemon.entry as entry
import navig.daemon.supervisor as _supervisor

# Captured before the autouse stub below replaces it.
_REAL_VERIFY_DAEMON_PID = _supervisor.NavigDaemon.__dict__["_verify_daemon_pid"]


@pytest.fixture(autouse=True)
def _entry_chdirs_to_its_home(monkeypatch):
    """`entry.main()` chdir's to `config_dir()` by design -- it is the daemon's home, so
    every launch path (shell, scheduled task, tray) sees the same cwd. Driven in-process
    here, that chdir outlived each test: six tests left the worker standing in an
    isolated temp config dir, and `tests/ops/test_monitoring_unicode.py`, which loaded a
    module by a relative path, went 10-red in the full suite while passing alone.
    `monkeypatch.chdir` records the cwd now and restores it on teardown whatever the
    code under test does; the conftest cwd guard is what named this file.
    """
    monkeypatch.chdir(os.getcwd())


@pytest.fixture(autouse=True)
def _recorded_pid_is_a_daemon(monkeypatch):
    """The guard also asks the command line whether the recorded pid is a navig daemon.
    The stub pids here (1, 7, 4321) are not real daemons — answer for them, or a test
    that means "one is running" would read a stranger's cmdline and boot a real one."""
    import navig.daemon.supervisor as supervisor

    monkeypatch.setattr(
        supervisor.NavigDaemon, "_verify_daemon_pid", staticmethod(lambda pid: True)
    )


def _never_reached(*a, **k):  # pragma: no cover - the point is that it is not called
    raise AssertionError(
        "main() went on to build a daemon even though one was already running"
    )


def test_a_duplicate_launch_exits_without_starting_anything(monkeypatch) -> None:
    """The whole point: a second launch must not construct a NavigDaemon."""
    import navig.daemon.supervisor as supervisor

    monkeypatch.setattr(supervisor.NavigDaemon, "is_running", staticmethod(lambda: True))
    monkeypatch.setattr(supervisor.NavigDaemon, "read_pid", staticmethod(lambda: 4321))
    # If the guard fails, main() reaches config loading and daemon construction.
    monkeypatch.setattr(entry, "_load_config", _never_reached)

    entry.main()  # must simply return


def test_a_duplicate_launch_returns_rather_than_raising(monkeypatch) -> None:
    """Task Scheduler reads the exit code: a correct no-op must look SUCCESSFUL.

    Raising here would record LastTaskResult != 0 for the right outcome, which is
    exactly the kind of false alarm that trains an operator to ignore the row.
    """
    import navig.daemon.supervisor as supervisor

    monkeypatch.setattr(supervisor.NavigDaemon, "is_running", staticmethod(lambda: True))
    monkeypatch.setattr(supervisor.NavigDaemon, "read_pid", staticmethod(lambda: 1))
    monkeypatch.setattr(entry, "_load_config", _never_reached)

    assert entry.main() is None


def test_the_guard_runs_before_any_heavy_work(monkeypatch) -> None:
    """Cheap check first — a duplicate launch must not load .env or config.

    NavigDaemon is stubbed even though the guard should stop us reaching it: if
    the guard ever breaks, main() would otherwise construct a REAL supervisor and
    block forever inside daemon.run(), and a hanging test reports nothing at all.
    A broken guard must fail this test in milliseconds, not wedge the suite.
    """
    import navig.daemon.supervisor as supervisor

    calls: list[str] = []
    monkeypatch.setattr(supervisor.NavigDaemon, "is_running", staticmethod(lambda: True))
    monkeypatch.setattr(supervisor.NavigDaemon, "read_pid", staticmethod(lambda: 7))
    monkeypatch.setattr(entry, "_load_config", lambda: calls.append("config") or {})

    class _NeverRuns:
        # Must carry the guard's own methods: main() resolves NavigDaemon at CALL
        # time, so a stub without is_running makes the guard raise AttributeError
        # and (correctly) fail OPEN — which would look like a broken guard.
        @staticmethod
        def is_running():
            return True

        @staticmethod
        def read_pid():
            return 7

        @staticmethod
        def _verify_daemon_pid(pid):
            return True

        def __init__(self, *a, **k):
            pass

        def add_telegram_bot(self, *a, **k):
            pass

        def add_gateway(self, *a, **k):
            pass

        def add_scheduler(self, *a, **k):
            pass

        def run(self):
            calls.append("run")  # recorded, never blocks

    monkeypatch.setattr(supervisor, "NavigDaemon", _NeverRuns)

    entry.main()
    assert calls == [], f"a duplicate launch did work it should have skipped: {calls}"


def test_a_normal_start_is_NOT_blocked(monkeypatch) -> None:
    """The guard must not become a reason a real daemon never starts."""
    import navig.daemon.supervisor as supervisor

    started: list[str] = []
    monkeypatch.setattr(supervisor.NavigDaemon, "is_running", staticmethod(lambda: False))
    monkeypatch.setattr(entry, "_load_config", lambda: {"telegram_bot": False})

    class _FakeDaemon:
        def __init__(self, *a, **k):
            pass

        def run(self):
            started.append("ran")

    monkeypatch.setattr(supervisor, "NavigDaemon", _FakeDaemon)
    entry.main()
    assert started == ["ran"], "no daemon was running, so main() had to start one"


def test_an_unreadable_pid_file_falls_through_to_starting(monkeypatch) -> None:
    """Guard failure must never be the reason the daemon does not come up.

    A refusal is the expensive direction: it costs the operator their bot. An
    extra process is recoverable; a daemon that silently declines to start is the
    two-day outage this whole line of work came from.
    """
    import navig.daemon.supervisor as supervisor

    started: list[str] = []

    def boom():
        raise OSError("pid file unreadable")

    monkeypatch.setattr(supervisor.NavigDaemon, "is_running", staticmethod(boom))
    monkeypatch.setattr(entry, "_load_config", lambda: {"telegram_bot": False})

    class _FakeDaemon:
        def __init__(self, *a, **k):
            pass

        def run(self):
            started.append("ran")

    monkeypatch.setattr(supervisor, "NavigDaemon", _FakeDaemon)
    entry.main()
    assert started == ["ran"], "an unreadable pid file must not prevent a start"


def test_the_stop_intent_flag_still_wins(monkeypatch) -> None:
    """The new guard must not have displaced the deliberate-stop suppression."""
    import navig.daemon.service_manager as svc

    monkeypatch.setattr(svc, "stop_flag_is_set", lambda: True)
    monkeypatch.setattr(entry, "_load_config", _never_reached)

    entry.main()


def test_a_pid_file_naming_a_stranger_does_not_suppress_the_start(monkeypatch) -> None:
    """`is_running` trusts the pid file's number and create time; a file overwritten with
    an OLDER unrelated live process still passes it. The guard must also ask whether that
    process is a navig daemon, or every watchdog launch exits as a "duplicate" of it."""
    import navig.daemon.supervisor as supervisor

    started: list[str] = []

    class _StrangerInThePidFile:
        @staticmethod
        def is_running():
            return True

        @staticmethod
        def read_pid():
            return 4321

        @staticmethod
        def _read_cmdline(pid):
            return "notepad.exe"

        # The REAL verification — its cmdline lookup resolves through this class.
        _verify_daemon_pid = _REAL_VERIFY_DAEMON_PID

        def __init__(self, *a, **k):
            pass

        def run(self):
            started.append("ran")

    monkeypatch.setattr(supervisor, "NavigDaemon", _StrangerInThePidFile)
    monkeypatch.setattr(entry, "_load_config", lambda: {"telegram_bot": False})

    entry.main()

    assert started == ["ran"], "a pid file naming a non-daemon process suppressed the start"
