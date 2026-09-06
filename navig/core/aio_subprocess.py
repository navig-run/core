"""Await an asyncio subprocess with a timeout — and KILL the child TREE on timeout.

`await asyncio.wait_for(proc.communicate(), timeout=…)` cancels only the wait coroutine; the
child process keeps running **orphaned**. Every bounded subprocess wait must kill the child on
timeout, so route them through here — it happens in exactly one place, and
``tests/quality/test_no_orphaned_subprocess_on_timeout.py`` fails the build if a new
``wait_for(proc.communicate()/wait())`` is added without a kill.

A bare ``proc.kill()`` signals only the DIRECT child, though. A `navig backup export` → pg_dump,
a `navig db dump` → mysqldump, a `navig host …` → ssh, or any command that spawns helpers leaves
its GRANDCHILDREN running orphaned past the reported timeout — and the retry could then launch a
second concurrent copy against live infra. :func:`kill_process_tree` kills the whole tree:
``taskkill /T /F`` on Windows (kernel walks the tree), psutil descendant enumeration on POSIX,
falling back to a bare kill of the direct child when neither is available.

Killing is only half of it — a caller that wants to be *polite* first has a trap waiting.
``terminate()`` → ``wait()`` → escalate cannot reach the grandchildren, because walking a tree
needs a LIVE root and the escalation runs after the parent was reaped. So the graceful path has
its own pair, which snapshots the descendants BEFORE signalling anything:
:func:`terminate_process_tree` (asyncio) and :func:`terminate_process_tree_sync`
(``subprocess.Popen``). Reach for those whenever the process might be a launcher — a
``create_subprocess_shell`` spawn, or on Windows any ``.cmd``/``.bat`` (``npx`` resolves to
``npx.CMD``), where the pid you hold is ``cmd.exe`` and the real work is its child.
"""

from __future__ import annotations

import asyncio
import sys
import time

# asyncio gives a subprocess pipe a StreamReader whose default line limit is 64 KiB. A single
# output line longer than that makes ``readline()`` RAISE (LimitOverrunError → ValueError)
# instead of returning the line — which kills the reading loop, not just that one line. Any
# spawn whose stream is consumed with ``readline()``/``readuntil()`` must therefore pass an
# explicit, generous ``limit=``: one ``--json`` payload, one long log line, or one big
# JSON-RPC response is enough to hit 64 KiB in normal use. This bricked MCP connections
# (#692/#695) before it was found. ``tests/quality/test_subprocess_stream_limit.py`` fails the
# build if a new line-reading spawn omits it.
STREAM_LIMIT = 8 * 1024 * 1024  # 8 MiB


async def kill_descendants(pid: int) -> None:
    """Kill *pid*'s descendant processes so a bare ``proc.kill()`` can't orphan grandchildren.

    Public because a caller with its own draining/waiting structure needs the descendant sweep
    WITHOUT the parent reap that :func:`kill_process_tree` performs — it can then keep its
    existing ``proc.kill()`` and its own wait, and simply stop orphaning the real command.
    Callable only while *pid* is still alive (a dead pid has no walkable tree).

    On Windows ``taskkill /T`` also kills *pid* itself (the caller's ``proc.kill()`` then
    no-ops); on POSIX only the descendants are killed here and the caller kills the parent.
    Best-effort throughout — a child that already exited, or a missing psutil, is fine.
    """
    if sys.platform == "win32":
        # taskkill /T walks the process tree in the kernel — the reliable way to reach
        # grandchildren on Windows (the daemon supervisor uses the same pattern).
        from navig.platform.process import spawn_kwargs  # noqa: PLC0415

        try:
            tk = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                # This runs on EVERY bounded subprocess that has to be reaped, so an
                # unsuppressed taskkill is a console window per timeout/cancel.
                **spawn_kwargs(),
            )
        except OSError:
            return  # taskkill unavailable — the caller's proc.kill() still gets the direct child
        try:
            await asyncio.wait_for(tk.wait(), timeout=5)
        except asyncio.TimeoutError:
            # taskkill itself hung (pathological) — don't leave IT orphaned.
            try:
                tk.kill()
            except (ProcessLookupError, OSError):
                pass
        return

    # POSIX: the child was not spawned in its own session, so os.killpg would hit the
    # daemon's own group. Enumerate descendants with psutil instead (best-effort).
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        return
    try:
        descendants = psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return  # already gone / not accessible
    for child in descendants:
        try:
            child.kill()
        except psutil.Error:
            pass


async def kill_process_tree(proc) -> None:
    """Best-effort SIGKILL of the child **and its descendants**, then reap.

    Kills grandchildren a bare ``proc.kill()`` would orphan, then signals and reaps the direct
    child object so it can't linger or zombie. Safe to call on an already-exited process.

    **Precondition: the ROOT must still be alive.** Reaching descendants means walking a tree
    from *pid* — ``taskkill /T`` needs a live pid and psutil cannot enumerate a dead one's
    children — so once the parent has been terminated **and reaped**, its grandchildren are
    unreachable and this becomes a no-op over them. A caller that wants a graceful attempt
    first must therefore NOT reap the parent before calling here; use
    :func:`terminate_process_tree`, which snapshots the descendants up front.
    """
    pid = getattr(proc, "pid", None)
    if pid is not None:
        await kill_descendants(pid)
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass  # already exited (or killed with the tree above)
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except (asyncio.TimeoutError, OSError):
        pass


def _snapshot_descendants(pid: int | None) -> list:
    """Return live ``psutil.Process`` handles for *pid*'s descendants, best-effort.

    Taken BEFORE the parent is signalled, because after it exits the tree can no longer be
    walked. A handle stays usable for ``is_running()``/``kill()`` after its parent dies.
    """
    if pid is None:
        return []
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        return []
    try:
        return psutil.Process(pid).children(recursive=True)
    except Exception:  # noqa: BLE001 - psutil.Error, and a racing exit can raise others
        return []


async def terminate_process_tree(proc, grace: float = 5.0) -> None:
    """Graceful-then-forceful kill of *proc* **and everything it spawned**.

    The naive sequence — ``terminate()`` → ``await wait()`` → escalate to a tree kill — cannot
    work, because the escalation runs only after the parent has been reaped, and by then the
    tree is unwalkable (see :func:`kill_process_tree`). This snapshots the descendants FIRST,
    gives the direct child *grace* seconds to exit on its own, then force-kills whatever is
    still standing.

    This matters most for a **shell** spawn. ``create_subprocess_shell`` makes the pid
    ``cmd.exe`` on Windows, where ``terminate()`` is ``TerminateProcess``: it kills the shell
    instantly and ORPHANS the real command. The graceful wait then returns promptly and the
    caller reports success — while the process it meant to kill keeps running forever, holding
    its inherited stdout handle (and any file that handle points at) open.
    """
    descendants = _snapshot_descendants(getattr(proc, "pid", None))
    try:
        proc.terminate()
    except (ProcessLookupError, OSError):
        pass  # already exited
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
    except (asyncio.TimeoutError, OSError):
        pass
    for child in descendants:
        try:
            if child.is_running():
                child.kill()
        except Exception:  # noqa: BLE001 - best-effort; a vanished child is the good case
            pass
    # The direct child too, in case terminate() did not take within the grace period.
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
    except (asyncio.TimeoutError, OSError):
        pass
    # Confirm the descendants are actually gone before returning. Killing is asynchronous
    # (``TerminateProcess`` on Windows), so returning the instant the signal is sent would let
    # a caller report "terminated" while the process is still up — the exact dishonesty this
    # helper exists to remove. Bounded: a survivor is reported by the caller's own checks, not
    # waited on forever.
    if descendants:
        deadline = asyncio.get_running_loop().time() + grace
        while asyncio.get_running_loop().time() < deadline:
            try:
                if not any(c.is_running() for c in descendants):
                    break
            except Exception:  # noqa: BLE001 - a vanished handle is the good case
                break
            await asyncio.sleep(0.02)


def terminate_process_tree_sync(proc, grace: float = 5.0) -> None:
    """Blocking :func:`terminate_process_tree`, for a ``subprocess.Popen``.

    Same contract and same reasoning — the descendants are snapshotted before the parent is
    signalled, because once it exits the tree can no longer be walked. Lives here rather than
    in a module of its own so there is exactly ONE description of how a process tree is taken
    down; several callers had already hand-rolled their own.

    The Windows case that makes this mandatory: ``Popen(["npx"])`` resolves through ``PATHEXT``
    to ``npx.CMD``, and ``CreateProcess`` runs a ``.cmd`` via ``cmd.exe`` — so the pid you hold
    is a shell and the real server (``node``) is its child. ``terminate()`` reaps the shell and
    leaves the server running, holding its stdio pipes.
    """
    import subprocess  # noqa: PLC0415 - stdlib, but only needed on this path

    descendants = _snapshot_descendants(getattr(proc, "pid", None))
    try:
        proc.terminate()
    except (ProcessLookupError, OSError):
        pass  # already exited
    try:
        proc.wait(timeout=grace)
    except (subprocess.TimeoutExpired, OSError):
        pass
    for child in descendants:
        try:
            if child.is_running():
                child.kill()
        except Exception:  # noqa: BLE001 - best-effort; a vanished child is the good case
            pass
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=grace)
    except (subprocess.TimeoutExpired, OSError):
        pass
    if descendants:
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            try:
                if not any(c.is_running() for c in descendants):
                    break
            except Exception:  # noqa: BLE001 - a vanished handle is the good case
                break
            time.sleep(0.02)


async def communicate_or_kill(proc, timeout: float):
    """``await proc.communicate()`` bounded by *timeout*, killing the child TREE on timeout
    before re-raising ``asyncio.TimeoutError`` — so a caller's existing timeout handling still
    runs, but neither the subprocess nor its grandchildren are left orphaned. Returns
    ``(stdout, stderr)``."""
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await kill_process_tree(proc)
        raise


async def wait_or_kill(proc, timeout: float) -> int:
    """``await proc.wait()`` bounded by *timeout*, killing the child TREE on timeout before
    re-raising ``asyncio.TimeoutError``. Returns the exit code."""
    try:
        return await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        await kill_process_tree(proc)
        raise
