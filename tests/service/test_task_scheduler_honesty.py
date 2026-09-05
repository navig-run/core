"""`navig service status` must not print "Active" for a DISABLED autostart task.

Measured on the operator's machine, 2026-09-04, while their daily check-ins had
stopped arriving::

    navig service status
      Task Scheduler: Active

    Get-ScheduledTask 'NAVIG Daemon'
      State          : Disabled
      LastTaskResult : 1

The daemon had NO autostart -- it would not come back after a reboot -- and the
status command said it was fine.

The cause was ``running = "running" in stdout.lower()`` over ``schtasks /query
/v``. That dump contains the field LABEL ``Repeat: Stop If Still Running:`` for
every task ever queried, so the expression was unconditionally True. The check
could report Inactive only when the query itself failed.

The unit test that "covered" it fed ``stdout="Status: Running"`` -- a string real
``schtasks`` never emits -- so the fake agreed with the bug. That is why the
central test here replays REAL captured output instead.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from navig.daemon import service_manager as sm

_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def _task_xml(*, settings_enabled: str, trigger_enabled: str = "true") -> str:
    """A task definition shaped like the one schtasks /xml actually returns."""
    return (
        f'<?xml version="1.0" encoding="UTF-16"?>\n'
        f'<Task version="1.2" xmlns="{_NS}">\n'
        f"  <Triggers><LogonTrigger>"
        f"<Enabled>{trigger_enabled}</Enabled>"
        f"</LogonTrigger></Triggers>\n"
        f"  <Settings><Enabled>{settings_enabled}</Enabled></Settings>\n"
        f"</Task>\n"
    )


def _run_returning(stdout: str, returncode: int = 0):
    return lambda *_a, **_k: SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


# --- the real captured output, verbatim ------------------------------------
# Trimmed from `schtasks /query /tn 'NAVIG Daemon' /fo LIST /v` on the operator's
# machine while the task was Disabled. The "running" that fooled the old check is
# a column LABEL; note the actual state fields both say Disabled.
_REAL_VERBOSE_DUMP_OF_A_DISABLED_TASK = """\
Folder: (root)
HostName:                             SUBDOSE-PC
TaskName:                             NAVIG Daemon
Status:                               Disabled
Scheduled Task State:                 Disabled
Repeat: Stop If Still Running:        N/A
"""


def test_a_disabled_task_is_reported_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm.subprocess, "run", _run_returning(_task_xml(settings_enabled="false")))
    enabled, detail = sm.task_scheduler_status()
    assert enabled is False, "a disabled task renders as 'Active' -- the original bug"
    assert "DISABLED" in detail
    assert "navig service start" in detail, "a warning must say how to fix it"


def test_an_enabled_task_is_reported_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """The floor: the fix must not make a healthy install look broken."""
    monkeypatch.setattr(sm.subprocess, "run", _run_returning(_task_xml(settings_enabled="true")))
    enabled, _ = sm.task_scheduler_status()
    assert enabled is True


def test_the_verbose_dump_that_defeated_the_old_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay the exact output that made the old substring check always True.

    Pinned as a discriminator: `"running" in dump.lower()` is True for this text,
    so any future reader that goes back to scanning the human-readable dump fails
    here instead of in six months on the operator's machine.
    """
    assert "running" in _REAL_VERBOSE_DUMP_OF_A_DISABLED_TASK.lower(), (
        "the fixture no longer reproduces the bug's trigger"
    )
    monkeypatch.setattr(
        sm.subprocess, "run", _run_returning(_REAL_VERBOSE_DUMP_OF_A_DISABLED_TASK)
    )
    enabled, detail = sm.task_scheduler_status()
    assert enabled is False, "the field label 'Stop If Still Running' read as a live task"
    assert "could not be read" in detail, "not-XML must be reported as unverified, not healthy"


def test_a_triggers_enabled_flag_is_not_the_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each <Trigger> carries its own <Enabled>; only Settings/Enabled is the task's."""
    monkeypatch.setattr(
        sm.subprocess,
        "run",
        _run_returning(_task_xml(settings_enabled="false", trigger_enabled="true")),
    )
    assert sm.task_scheduler_status()[0] is False


def test_an_omitted_Enabled_element_means_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows OMITS <Enabled> when the task is enabled -- true is the schema default.

    Caught only by running against the operator's live task in both states: while
    disabled it carried `<Enabled>false</Enabled>`, and the moment it was re-enabled
    the element vanished from <Settings> entirely. A reader that requires the element
    reports every HEALTHY install as "state could not be read" -- swapping one
    dishonest answer for another. This block is the real one, trimmed.
    """
    real_enabled_settings = (
        f'<Task xmlns="{_NS}"><Settings>'
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
        "<Hidden>true</Hidden>"
        "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<RestartOnFailure><Count>999</Count><Interval>PT1M</Interval></RestartOnFailure>"
        "<StartWhenAvailable>true</StartWhenAvailable>"
        "</Settings></Task>"
    )
    assert "<Enabled>" not in real_enabled_settings, "fixture no longer reproduces the case"
    monkeypatch.setattr(sm.subprocess, "run", _run_returning(real_enabled_settings))
    enabled, detail = sm.task_scheduler_status()
    assert enabled is True, "a healthy enabled task must not read as unverifiable"
    assert "enabled" in detail.lower()


def test_unreadable_state_is_not_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Could-not-verify must render Inactive, never Active.

    House rule: a green light over an unknown is worse than a red one, because it
    tells the operator not to look. "No <Settings> element at all" is the genuinely
    unreadable case -- distinct from "<Settings> without <Enabled>", which is the
    normal shape of a healthy task (above).
    """
    monkeypatch.setattr(sm.subprocess, "run", _run_returning("<Task><Triggers/></Task>"))
    enabled, detail = sm.task_scheduler_status()
    assert enabled is False
    assert "could not be read" in detail


def test_a_missing_task_says_how_to_install_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm.subprocess, "run", _run_returning("ERROR: cannot find the file", 1))
    enabled, detail = sm.task_scheduler_status()
    assert enabled is False
    assert "navig service install" in detail or "ERROR" in detail


def test_a_probe_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """schtasks missing entirely must degrade, not crash `navig service status`."""

    def boom(*_a, **_k):
        raise FileNotFoundError("schtasks")

    monkeypatch.setattr(sm.subprocess, "run", boom)
    enabled, detail = sm.task_scheduler_status()
    assert enabled is False
    assert "schtasks" in detail
