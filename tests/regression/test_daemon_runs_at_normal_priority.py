"""The daemon must not run at Task Scheduler's default BELOW_NORMAL priority.

A task whose XML omits <Priority> runs at 7: BELOW_NORMAL CPU and LOW I/O, and
every daemon child inherits it. On a busy machine that starved the brain —
measured: the deck's CPU/RAM/Disk snapshot took 18–42 s inside the daemon and
0.3 s in a normal-priority process, while trivial routes answered in milliseconds.
"""

from __future__ import annotations

import re
import sys
import types

import pytest

from navig.daemon import service_manager as sm
from navig.platform import process as proc_mod


def _priority(xml_text: str) -> list[str]:
    return re.findall(r"<Priority>(\d+)</Priority>", xml_text)


@pytest.mark.parametrize("build", [sm._schtasks_xml, sm._schtasks_restart_xml])
def test_task_xml_requests_normal_priority(build):
    # 4–6 is Task Scheduler's normal band; 7 (the silent default) is below normal.
    assert _priority(build()) == ["5"]


class _FakeProc:
    def __init__(self, nice: int, io: int) -> None:
        self._nice, self._io = nice, io

    def nice(self, value: int | None = None) -> int:
        if value is not None:
            self._nice = value
        return self._nice

    def ionice(self, value: int | None = None) -> int:
        if value is not None:
            self._io = value
        return self._io


def _fake_psutil(proc: _FakeProc) -> types.ModuleType:
    m = types.ModuleType("psutil")
    m.NORMAL_PRIORITY_CLASS = 32
    m.BELOW_NORMAL_PRIORITY_CLASS = 16384
    m.IDLE_PRIORITY_CLASS = 64
    m.HIGH_PRIORITY_CLASS = 128
    m.IOPRIO_NORMAL = 2
    m.Process = lambda: proc
    return m


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(proc_mod.sys, "platform", "win32")


def test_a_lowered_process_is_raised_to_normal(monkeypatch, on_windows):
    p = _FakeProc(nice=16384, io=1)  # exactly what the live daemon reported
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil(p))
    assert proc_mod.restore_normal_priority() is True
    assert p.nice() == 32
    assert p.ionice() == 2


def test_never_lowers_a_higher_priority(monkeypatch, on_windows):
    p = _FakeProc(nice=128, io=2)  # HIGH, set deliberately by someone
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil(p))
    assert proc_mod.restore_normal_priority() is False
    assert p.nice() == 128


def test_a_psutil_failure_never_raises(monkeypatch, on_windows):
    m = types.ModuleType("psutil")

    def boom():
        raise OSError("access denied")

    m.Process = boom
    monkeypatch.setitem(sys.modules, "psutil", m)
    assert proc_mod.restore_normal_priority() is False


def test_noop_off_windows(monkeypatch):
    monkeypatch.setattr(proc_mod.sys, "platform", "linux")
    assert proc_mod.restore_normal_priority() is False
