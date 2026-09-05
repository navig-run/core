"""navig.core.aio_subprocess — a bounded subprocess wait must KILL the child on timeout."""

from __future__ import annotations

import asyncio

import pytest

from navig.core.aio_subprocess import communicate_or_kill, wait_or_kill

pytestmark = pytest.mark.unit


class _FakeProc:
    """Minimal asyncio-subprocess stand-in: communicate()/wait() hang until killed."""

    def __init__(self, *, hang: bool, result=(b"out", b"err")):
        self.hang = hang
        self.result = result
        self.killed = False

    async def communicate(self):
        while self.hang and not self.killed:
            await asyncio.sleep(0.01)
        return self.result

    def kill(self):
        self.killed = True

    def terminate(self):
        # asyncio.subprocess.Process has terminate() as well as kill(); a fake that omits it
        # would make any helper doing a graceful-first stop blow up with AttributeError only
        # in tests, or silently pass because nothing exercised that branch.
        self.killed = True

    async def wait(self):
        while self.hang and not self.killed:
            await asyncio.sleep(0.01)
        return 0


async def test_communicate_or_kill_kills_child_on_timeout():
    proc = _FakeProc(hang=True)
    with pytest.raises(asyncio.TimeoutError):
        await communicate_or_kill(proc, timeout=0.05)
    assert proc.killed is True  # the child was killed, not orphaned


async def test_communicate_or_kill_returns_on_success_without_killing():
    proc = _FakeProc(hang=False, result=(b"hi", b""))
    out, err = await communicate_or_kill(proc, timeout=5)
    assert (out, err) == (b"hi", b"")
    assert proc.killed is False


async def test_wait_or_kill_kills_child_on_timeout():
    proc = _FakeProc(hang=True)
    with pytest.raises(asyncio.TimeoutError):
        await wait_or_kill(proc, timeout=0.05)
    assert proc.killed is True


async def test_kill_and_reap_tolerates_an_already_dead_child():
    class _Dead:
        def kill(self):
            raise ProcessLookupError  # already exited

        async def wait(self):
            return 0

    # communicate_or_kill must not blow up if kill() finds the child already gone.
    proc = _FakeProc(hang=True)
    proc.kill = _Dead().kill  # type: ignore[method-assign]
    with pytest.raises(asyncio.TimeoutError):
        await communicate_or_kill(proc, timeout=0.05)


# ── tree-kill: grandchildren must not be orphaned ───────────────────────────


class _FakePidProc:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.killed = False

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return 0


async def test_windows_kill_uses_taskkill_tree(monkeypatch):
    """On Windows the tree is reached with ``taskkill /PID <pid> /T /F`` — a bare kill
    would leave grandchildren (a `navig backup` → pg_dump …) running."""
    import navig.core.aio_subprocess as aio

    monkeypatch.setattr(aio.sys, "platform", "win32")

    calls: list[tuple] = []

    class _FakeTaskkill:
        async def wait(self):
            return 0

    async def _fake_exec(*args, **kwargs):
        calls.append(args)
        return _FakeTaskkill()

    monkeypatch.setattr(aio.asyncio, "create_subprocess_exec", _fake_exec)

    proc = _FakePidProc(pid=4242)
    await aio.kill_process_tree(proc)

    assert calls, "taskkill was not invoked"
    argv = calls[0]
    assert argv[0] == "taskkill"
    assert "/T" in argv and "/F" in argv and "4242" in argv
    assert proc.killed is True  # the direct child is signalled + reaped too


async def test_kill_process_tree_tolerates_no_pid_and_dead_child():
    # A stand-in with no .pid must still be killed + reaped (no tree to walk).
    proc = _FakeProc(hang=True)
    assert not hasattr(proc, "pid")
    from navig.core.aio_subprocess import kill_process_tree

    await kill_process_tree(proc)
    assert proc.killed is True


async def test_kill_process_tree_kills_a_real_grandchild():
    """End-to-end proof: a parent that spawns a grandchild must have the WHOLE tree
    killed — the exact leak (`navig …` → pg_dump/ssh) a bare proc.kill() left orphaned."""
    import sys

    psutil = pytest.importorskip("psutil")
    from navig.core.aio_subprocess import kill_process_tree

    # Parent python spawns a long-sleeping grandchild, prints its pid, then sleeps.
    parent_code = (
        "import subprocess, sys, time; "
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(45)']); "
        "print(g.pid, flush=True); "
        "time.sleep(45)"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent_code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    gpid = None
    try:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
        gpid = int(line.decode().strip())
        assert psutil.pid_exists(gpid), "grandchild should be alive before the kill"

        await kill_process_tree(proc)

        # The kill is asynchronous; poll for the grandchild to actually disappear.
        for _ in range(50):
            if not _pid_alive(psutil, gpid):
                break
            await asyncio.sleep(0.1)
        assert not _pid_alive(psutil, gpid), "grandchild survived the tree kill (orphaned!)"
    finally:
        # Belt-and-suspenders cleanup so a failed assertion can't leak processes.
        for pid in (gpid, proc.pid):
            if pid is not None:
                try:
                    psutil.Process(pid).kill()
                except psutil.Error:
                    pass


def _pid_alive(psutil, pid: int) -> bool:
    """True only if the pid is a live, non-zombie process (a killed proc may briefly
    linger as a zombie before the OS reaps it)."""
    try:
        return psutil.Process(pid).status() not in (
            getattr(psutil, "STATUS_ZOMBIE", "zombie"),
            getattr(psutil, "STATUS_DEAD", "dead"),
        )
    except psutil.Error:
        return False


async def test_terminate_process_tree_kills_a_real_grandchild():
    """Graceful-first must NOT cost the grandchildren.

    The naive sequence — terminate() → await wait() → escalate to kill_process_tree() — cannot
    work: reaching descendants means walking a tree from the parent pid, and by escalation time
    the parent has been reaped, so the tree is unwalkable and the grandchild lives on. It bit
    `navig task kill`, whose tracked pid is a *shell*: on Windows terminate() is
    TerminateProcess, which kills cmd.exe instantly and orphans the real command — the graceful
    wait then returns promptly and the caller reports a successful kill.
    """
    import sys

    psutil = pytest.importorskip("psutil")
    from navig.core.aio_subprocess import terminate_process_tree

    parent_code = (
        "import subprocess, sys, time; "
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(45)']); "
        "print(g.pid, flush=True); "
        "time.sleep(45)"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent_code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    gpid = None
    try:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
        gpid = int(line.decode().strip())
        assert psutil.pid_exists(gpid), "grandchild should be alive before the kill"

        await terminate_process_tree(proc, grace=2.0)

        # No polling here on purpose: the helper is contracted to confirm the descendants are
        # actually gone before returning, so a caller can trust "terminated" the moment it does.
        assert not _pid_alive(psutil, gpid), "grandchild survived terminate_process_tree"
    finally:
        for pid in (gpid, proc.pid):
            if pid is not None:
                try:
                    psutil.Process(pid).kill()
                except psutil.Error:
                    pass


async def test_terminate_process_tree_tolerates_an_already_dead_child():
    """Teardown paths call this on processes that may have exited on their own."""
    from navig.core.aio_subprocess import terminate_process_tree

    proc = _FakeProc(hang=False)
    await terminate_process_tree(proc, grace=0.1)  # must not raise


def test_terminate_process_tree_sync_kills_a_real_grandchild():
    """The blocking variant, used by the `navig mcp stop` path (subprocess.Popen)."""
    import subprocess
    import sys
    import time

    psutil = pytest.importorskip("psutil")
    from navig.core.aio_subprocess import terminate_process_tree_sync

    parent_code = (
        "import subprocess, sys, time; "
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(45)']); "
        "print(g.pid, flush=True); "
        "time.sleep(45)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    gpid = None
    try:
        deadline = time.monotonic() + 15
        line = ""
        while time.monotonic() < deadline and not line:
            line = proc.stdout.readline().strip()
        gpid = int(line)
        assert psutil.pid_exists(gpid), "grandchild should be alive before the kill"

        terminate_process_tree_sync(proc, grace=2.0)

        assert not _pid_alive(psutil, gpid), "grandchild survived terminate_process_tree_sync"
    finally:
        for pid in (gpid, proc.pid):
            if pid is not None:
                try:
                    psutil.Process(pid).kill()
                except psutil.Error:
                    pass


def test_a_dot_cmd_launcher_is_a_shell_whose_child_must_also_die():
    """The real MCP trigger, reproduced exactly.

    `navig mcp` configures npm-type servers as `npx <package>`, and `shutil.which("npx")` on
    Windows returns **npx.CMD**. CreateProcess runs a .cmd through cmd.exe — so the pid we hold
    is a shell and the actual server (node) is its child. A plain terminate() reaps the shell,
    reports success, and leaves the server running with its pipes open.
    """
    import os

    if os.name != "nt":
        pytest.skip("the .cmd -> cmd.exe launcher shape is Windows-only")

    import pathlib
    import subprocess
    import sys
    import tempfile
    import time

    psutil = pytest.importorskip("psutil")
    from navig.core.aio_subprocess import terminate_process_tree_sync

    tmp = pathlib.Path(tempfile.mkdtemp())
    launcher = tmp / "fake_npx.cmd"
    launcher.write_text(
        f'@echo off\r\n"{sys.executable}" -c "import time; time.sleep(45)"\r\n', encoding="utf-8"
    )

    proc = subprocess.Popen(
        [str(launcher)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    kids = []
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                kids = psutil.Process(proc.pid).children(recursive=True)
            except psutil.Error:
                kids = []
            if kids:
                break
            time.sleep(0.05)

        # Anti-vacuity: if the .cmd did not produce a child process there is nothing to orphan
        # and the assertion below would pass for free.
        assert kids, "a .cmd launcher must run under cmd.exe and spawn the real command"

        terminate_process_tree_sync(proc, grace=2.0)

        survivors = [(k.pid, k.name()) for k in kids if _pid_alive(psutil, k.pid)]
        assert not survivors, f"the launched command outlived its .cmd shell: {survivors}"
    finally:
        for k in kids:
            try:
                k.kill()
            except psutil.Error:
                pass


def test_terminate_process_tree_sync_tolerates_an_already_dead_child():
    """`navig mcp stop` may race a server that just exited on its own."""
    import subprocess

    from navig.core.aio_subprocess import terminate_process_tree_sync

    class _DeadPopen:
        pid = None

        def terminate(self):
            raise ProcessLookupError

        def kill(self):
            raise ProcessLookupError

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout or 0)

    terminate_process_tree_sync(_DeadPopen(), grace=0.1)  # must not raise
