"""`navig service start` must actually re-enable a DISABLED autostart task.

Measured on the operator's machine, 2026-09-04, while their Telegram Deck showed
"can't reach your brain"::

    navig service status
      Task Scheduler: Installed but NOT healthy
        Detail: installed but DISABLED - it will not start the daemon at logon.
                Re-enable it with: navig service start

    navig service start
      Daemon already running (pid=49592)

    Get-ScheduledTask 'NAVIG Daemon'
      State : Disabled          <-- unchanged

The daemon had been started by another route, so `service start` took its
"already running" early return -- above the `task_scheduler_enable()` call that
the not-running path performs. The task therefore stayed disabled, the machine
had no autostart at the next logon, and the operator who followed the printed
advice got a success-looking message that repaired nothing.

The repair capability was present and wired; it was simply unreachable in the
one state whose own diagnosis tells you to run this command.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from navig.commands.service import service_app

runner = CliRunner()


class _RunningDaemon:
    @staticmethod
    def is_running() -> bool:
        return True

    @staticmethod
    def read_pid() -> int:
        return 49592


@pytest.fixture
def running_on_windows(monkeypatch: pytest.MonkeyPatch):
    """A running daemon on a Windows-shaped host, with the enable call recorded."""
    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", _RunningDaemon)
    # Patch the platform the code READS (`sys.platform`), not `os.name`: the latter
    # also changes what `pathlib` builds, so a Windows-shaped test on Linux died in
    # `WindowsPath` before reaching the repair path (2026-10-30, outpost).
    monkeypatch.setattr("sys.platform", "win32")
    calls: list[str] = []
    monkeypatch.setattr(
        "navig.daemon.service_manager.task_scheduler_enable",
        lambda: (calls.append("enable"), (True, "Task 'NAVIG Daemon' enabled"))[1],
    )
    return calls


def _set_state(monkeypatch: pytest.MonkeyPatch, state: tuple) -> None:
    monkeypatch.setattr(
        "navig.daemon.service_manager.task_scheduler_enabled_state", lambda: state
    )


def test_start_reenables_a_disabled_task_even_when_already_running(
    monkeypatch: pytest.MonkeyPatch, running_on_windows: list[str]
) -> None:
    """The regression: this is the exact state `service status` tells you to fix."""
    _set_state(monkeypatch, (False, True, "installed but DISABLED - ..."))

    result = runner.invoke(service_app, ["start"])

    assert result.exit_code == 0, result.output
    assert running_on_windows == ["enable"], (
        "service start left the DISABLED autostart task alone -- the machine has "
        f"no autostart at the next logon. output={result.output!r}"
    )
    assert "utostart" in result.output, result.output


def test_start_does_not_touch_an_absent_task(
    monkeypatch: pytest.MonkeyPatch, running_on_windows: list[str]
) -> None:
    """"Not installed" is `service status`'s business, not a start-path nag."""
    _set_state(monkeypatch, (None, False, "not installed - install it with: navig service install"))

    result = runner.invoke(service_app, ["start"])

    assert result.exit_code == 0, result.output
    assert running_on_windows == [], "tried to enable a task that does not exist"
    assert "utostart" not in result.output, result.output


def test_start_does_not_touch_an_unreadable_task(
    monkeypatch: pytest.MonkeyPatch, running_on_windows: list[str]
) -> None:
    """Could-not-verify must not be treated as "disabled" and blindly rewritten."""
    _set_state(monkeypatch, (None, True, "installed, but its enabled-state could not be read"))

    result = runner.invoke(service_app, ["start"])

    assert result.exit_code == 0, result.output
    assert running_on_windows == [], "rewrote a task whose state could not be read"


def test_start_is_quiet_when_autostart_is_already_healthy(
    monkeypatch: pytest.MonkeyPatch, running_on_windows: list[str]
) -> None:
    _set_state(monkeypatch, (True, True, "enabled - starts the daemon at logon"))

    result = runner.invoke(service_app, ["start"])

    assert result.exit_code == 0, result.output
    assert running_on_windows == []
    assert "utostart" not in result.output, result.output


def test_a_failed_repair_is_reported_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, running_on_windows: list[str]
) -> None:
    """If the repair fails the operator must hear about it -- silence here would
    recreate the original trap one level down."""
    _set_state(monkeypatch, (False, True, "installed but DISABLED - ..."))
    monkeypatch.setattr(
        "navig.daemon.service_manager.task_scheduler_enable",
        lambda: (False, "ERROR: Access is denied."),
    )

    result = runner.invoke(service_app, ["start"])

    assert result.exit_code == 0, result.output
    assert "Access is denied" in result.output, result.output
    assert "navig service install" in result.output, result.output


# ── a leftover stop intent would make the repair a lie ───────────────────────
# `service stop` leaves stop_requested set (and may arm a watchdog). A daemon started by
# another route afterwards is "already running" here, but every scheduled boot then exits
# at entry.py's stop-intent guard — enabling the task alone repaired nothing.


def _stop_flag(monkeypatch: pytest.MonkeyPatch, *, tty: bool) -> list[str]:
    cleared: list[str] = []
    flag = {"set": True}
    monkeypatch.setattr(
        "navig.daemon.service_manager.stop_flag_is_set", lambda: flag["set"]
    )
    monkeypatch.setattr(
        "navig.daemon.service_manager.clear_stop_flag",
        lambda: (cleared.append("stop_flag"), flag.update(set=False)),
    )
    monkeypatch.setattr(
        "navig.daemon.service_manager.clear_watchdog_deadline",
        lambda: cleared.append("watchdog"),
    )
    monkeypatch.setattr("navig.commands.service._stdin_is_tty", lambda: tty)
    return cleared


def test_an_interactive_start_lifts_the_stop_guards_before_repairing(
    monkeypatch: pytest.MonkeyPatch, running_on_windows: list[str]
) -> None:
    _set_state(monkeypatch, (False, True, "installed but DISABLED - ..."))
    cleared = _stop_flag(monkeypatch, tty=True)

    result = runner.invoke(service_app, ["start"])

    assert result.exit_code == 0, result.output
    assert sorted(cleared) == ["stop_flag", "watchdog"], result.output
    assert running_on_windows == ["enable"], result.output


def test_a_noninteractive_start_honours_the_stop_and_repairs_nothing(
    monkeypatch: pytest.MonkeyPatch, running_on_windows: list[str]
) -> None:
    _set_state(monkeypatch, (False, True, "installed but DISABLED - ..."))
    cleared = _stop_flag(monkeypatch, tty=False)

    result = runner.invoke(service_app, ["start"])

    assert result.exit_code == 0, result.output
    assert cleared == [], "a tray app or script must not override a deliberate stop"
    assert running_on_windows == [], "reported a repair every scheduled boot would undo"
    assert "stopped by user intent" in result.output, result.output
