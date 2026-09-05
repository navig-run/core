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

import navig.daemon.entry as entry


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
