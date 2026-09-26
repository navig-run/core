"""Who launched the daemon — the process SHAPE an orphan sweep kills on.

On 2026-09-14 the operator's hourly cleanup killed the daemon twice with the line
`KILL pythonw.exe 118488 ppid=62344 gone`: a daemon spawned detached from a CLI
that had exited has no living parent, and to a sweeper that is a leaked helper.
`NavigDaemon.parent_of()` names the shape so `doctor` and `service status` can
say "orphan-shaped — restart through the task" BEFORE the next :01, instead of
recording a death after it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from navig.daemon import supervisor as sup


def test_our_own_parent_is_a_live_ordinary_process():
    info = sup.NavigDaemon.parent_of(os.getpid())

    assert info["alive"] is True
    assert info["shape"] in ("process", "service"), info
    assert info["ppid"] and info["name"]


def test_a_process_whose_parent_exited_is_orphan_shaped():
    """Spawn a child that spawns a grandchild and exits: the grandchild's parent
    is gone — exactly the shape the sweep killed."""
    # The grandchild must NOT inherit our pipes, or run() below waits for IT.
    code = "\n".join(
        [
            "import subprocess, sys",
            "flags = getattr(subprocess, 'DETACHED_PROCESS', 0)",
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],",
            "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,",
            "    creationflags=flags, start_new_session=(flags == 0))",
            "print(p.pid, flush=True)",
        ]
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    grandchild = int(out.stdout.strip())
    try:
        # the middle process has exited by the time run() returned
        deadline = time.time() + 5
        info = sup.NavigDaemon.parent_of(grandchild)
        while info["shape"] not in ("orphan", "service") and time.time() < deadline:
            time.sleep(0.2)
            info = sup.NavigDaemon.parent_of(grandchild)
        if sys.platform == "win32":
            assert info["shape"] == "orphan", info
            assert info["alive"] is False
        else:
            # POSIX re-parents an orphan to init / a subreaper; that is a living
            # parent for life, and the sweep class does not exist there.
            assert info["shape"] in ("orphan", "service"), info
    finally:
        try:
            import psutil

            psutil.Process(grandchild).kill()
        except Exception:  # noqa: BLE001
            pass


class _FakeParent:
    def __init__(self, pid: int, name: str) -> None:
        self.pid = pid
        self._name = name

    def name(self) -> str:
        return self._name


class _FakeProc:
    def __init__(self, parent) -> None:
        self._parent = parent

    def ppid(self) -> int:
        return self._parent.pid if self._parent else 4321

    def parent(self):
        return self._parent


@pytest.mark.parametrize(
    "name, shape",
    [
        ("svchost.exe", "service"),
        ("nssm.exe", "service"),
        ("systemd", "service"),
        ("pwsh.exe", "process"),
        ("python.exe", "process"),
    ],
)
def test_the_service_host_is_recognised(monkeypatch, name, shape):
    import psutil

    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProc(_FakeParent(3356, name)))

    assert sup.NavigDaemon.parent_of(1)["shape"] == shape


def test_pid_one_is_a_service_parent_whatever_its_name(monkeypatch):
    import psutil

    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProc(_FakeParent(1, "whatever")))

    assert sup.NavigDaemon.parent_of(1)["shape"] == "service"


def test_an_unreadable_process_is_unknown_not_orphan(monkeypatch):
    """AccessDenied on an elevated daemon must not be reported as a missing parent."""
    import psutil

    def boom(pid):
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(psutil, "Process", boom)

    info = sup.NavigDaemon.parent_of(1)

    assert info["shape"] == "unknown" and info["alive"] is None


def test_a_parent_whose_name_cannot_be_read_is_still_alive(monkeypatch):
    import psutil

    class _Nameless(_FakeParent):
        def name(self):
            raise psutil.AccessDenied(self.pid)

    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProc(_Nameless(77, "")))

    info = sup.NavigDaemon.parent_of(1)

    assert info["alive"] is True and info["shape"] == "process" and info["name"] is None
