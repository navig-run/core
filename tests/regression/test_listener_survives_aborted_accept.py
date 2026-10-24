"""A client that aborts before it is accepted must not take the whole server down (Windows).

On a real ProactorEventLoop: the listener's first overlapped accept completes with
WinError 64 — exactly what the gateway logged twice on 2026-09-30 before its HTTP port went
dark while the process stayed up. The next client must still be served. The contrast case
runs the same scenario without the guard and proves the standard library closes the port,
so this test fails for the right reason if the guard ever stops working.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the proactor loop is Windows-only")

WINERROR_NETNAME_DELETED = 64


def _aborted_accept_error() -> OSError:
    # OSError(errno, strerror, filename, winerror): sets .winerror, as the proactor does.
    return OSError(22, "The specified network name is no longer available", None, WINERROR_NETNAME_DELETED)


async def _scenario() -> str:
    """Start a server, fail its first accept with WinError 64, then connect a real client.

    Returns "served" when the client got its reply, "refused" when the port was closed.
    """
    loop = asyncio.get_running_loop()
    real_accept = loop._proactor.accept
    first: dict[str, asyncio.Future] = {}

    def flaky_accept(sock):
        if "f" not in first:
            first["f"] = loop.create_future()
            return first["f"]
        return real_accept(sock)

    loop._proactor.accept = flaky_accept

    async def handle(reader, writer):
        writer.write(b"pong")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        for _ in range(50):
            if "f" in first:
                break
            await asyncio.sleep(0.01)
        first["f"].set_exception(_aborted_accept_error())
        await asyncio.sleep(0.05)
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 3)
        except (ConnectionRefusedError, OSError):
            return "refused"
        data = await asyncio.wait_for(reader.read(4), 3)
        writer.close()
        return "served" if data == b"pong" else f"wrong reply {data!r}"
    finally:
        loop._proactor.accept = real_accept
        server.close()


def _run_on_fresh_proactor(install_guard: bool, monkeypatch) -> str:
    from asyncio import proactor_events

    from navig.platform import accept_guard

    # Each case starts from the standard library's method — not whatever is on the class
    # now: another test in this process may already have installed the guard.
    stdlib = accept_guard.stdlib_start_serving or proactor_events.BaseProactorEventLoop._start_serving
    monkeypatch.setattr(proactor_events.BaseProactorEventLoop, "_start_serving", stdlib)
    monkeypatch.setattr(accept_guard, "_installed", False)
    monkeypatch.setattr(accept_guard, "stdlib_start_serving", accept_guard.stdlib_start_serving)
    if install_guard:
        assert accept_guard.install() is True
    loop = asyncio.ProactorEventLoop()
    loop.set_exception_handler(lambda _loop, _ctx: None)  # the stdlib path reports; keep output clean
    try:
        return loop.run_until_complete(_scenario())
    finally:
        loop.close()


@windows_only
def test_the_server_keeps_accepting_after_an_aborted_client(monkeypatch):
    assert _run_on_fresh_proactor(True, monkeypatch) == "served"


@windows_only
def test_without_the_guard_the_standard_library_closes_the_port(monkeypatch):
    """The contrast: proves the scenario reproduces the real failure, so the test above
    cannot pass vacuously."""
    assert _run_on_fresh_proactor(False, monkeypatch) == "refused"


@windows_only  # OSError only carries .winerror on Windows
def test_only_per_client_errors_are_absorbed():
    from navig.platform.accept_guard import is_transient_accept_error

    assert is_transient_accept_error(_aborted_accept_error())
    assert is_transient_accept_error(OSError(0, "reset", None, 10054))
    assert not is_transient_accept_error(OSError(0, "too many open files", None, 24))
    assert not is_transient_accept_error(ValueError("not an OSError"))


def test_every_navig_listener_installs_the_guard():
    """The gateway, the Telegram worker's own gateway and the supervisor's health server
    each open a listening socket; each must install the guard before it starts."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "navig"
    for rel in ("gateway/server.py", "daemon/telegram_worker.py", "daemon/supervisor.py"):
        assert "accept_guard.install()" in (root / rel).read_text(encoding="utf-8"), rel


# --- The orphan accept-helper task's "Task exception was never retrieved" noise ---------
#
# Keeping the listener alive (above) still leaves the proactor's fire-and-forget
# accept_coro helper holding the same WinError 64 unretrieved — asyncio then prints a full
# traceback that reads exactly like a crash, which on the operator's machine (2026-10-09)
# was misread as "the daemon stopped". These pin that the helper's exception is retrieved.


def _run_task_to_completion_without_retrieving(exc: BaseException, attach_consumer: bool):
    """Build a task that fails with ``exc`` and run it to completion WITHOUT consuming its
    exception (``asyncio.wait`` does not retrieve child exceptions, unlike ``gather``), so
    the only thing that can mark it retrieved is our done-callback. Returns the task."""
    from navig.platform.accept_guard import _consume_accept_helper_error

    async def _raise():
        raise exc

    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(_raise())
        if attach_consumer:
            task.add_done_callback(_consume_accept_helper_error)
        loop.run_until_complete(asyncio.wait({task}))
        return task
    finally:
        loop.close()


@windows_only  # a transient winerror only exists on Windows
def test_the_transient_accept_helper_exception_is_retrieved():
    task = _run_task_to_completion_without_retrieving(_aborted_accept_error(), attach_consumer=True)
    # _log_traceback False == asyncio will NOT warn "Task exception was never retrieved".
    assert task._log_traceback is False


def test_without_the_consumer_the_helper_exception_stays_unretrieved():
    """The contrast: proves the assertion above cannot pass vacuously — the same task run
    the same way, but with no done-callback, is still flagged for the GC-time warning."""
    task = _run_task_to_completion_without_retrieving(OSError("boom"), attach_consumer=False)
    assert task._log_traceback is True


def test_a_nontransient_helper_exception_is_also_retrieved():
    """Non-transient errors are still retrieved here (the real report always comes through
    the accept loop); the helper's duplicate must not surface as a bare crashing traceback."""
    task = _run_task_to_completion_without_retrieving(OSError("disk full"), attach_consumer=True)
    assert task._log_traceback is False


def _reinstall_from_stdlib_baseline(monkeypatch):
    """Reset the module to the stdlib baseline and install() fresh, so these tests do not
    depend on whether another test already installed the guard in this process."""
    from asyncio import proactor_events
    from asyncio.windows_events import IocpProactor

    from navig.platform import accept_guard

    stdlib_ss = accept_guard.stdlib_start_serving or proactor_events.BaseProactorEventLoop._start_serving
    stdlib_acc = accept_guard.stdlib_accept or IocpProactor.accept
    monkeypatch.setattr(proactor_events.BaseProactorEventLoop, "_start_serving", stdlib_ss)
    monkeypatch.setattr(IocpProactor, "accept", stdlib_acc)
    monkeypatch.setattr(accept_guard, "_installed", False)
    monkeypatch.setattr(accept_guard, "stdlib_start_serving", accept_guard.stdlib_start_serving)
    monkeypatch.setattr(accept_guard, "stdlib_accept", accept_guard.stdlib_accept)
    assert accept_guard.install() is True
    return stdlib_acc


@windows_only
def test_install_wraps_iocp_accept(monkeypatch):
    from asyncio.windows_events import IocpProactor

    from navig.platform import accept_guard

    stdlib_acc = _reinstall_from_stdlib_baseline(monkeypatch)
    assert IocpProactor.accept is not stdlib_acc
    assert accept_guard.stdlib_accept is stdlib_acc


@windows_only
def test_the_wrapper_tags_the_helper_task_the_proactor_spawns(monkeypatch):
    """The wrapper must attach the consumer to whatever helper task the stdlib accept spins
    off — captured by briefly intercepting tasks.ensure_future for that one sync call."""
    from asyncio import tasks
    from asyncio.windows_events import IocpProactor

    from navig.platform import accept_guard

    _reinstall_from_stdlib_baseline(monkeypatch)

    tagged = []
    monkeypatch.setattr(accept_guard, "_consume_accept_helper_error", tagged.append)

    loop = asyncio.new_event_loop()

    async def _noop():
        return None

    def fake_stdlib_accept(self, listener):
        fut = loop.create_future()
        fut.set_result(None)
        tasks.ensure_future(_noop(), loop=loop)  # the helper the wrapper should tag
        return fut

    monkeypatch.setattr(accept_guard, "stdlib_accept", fake_stdlib_accept)
    try:
        returned = IocpProactor.accept(object(), object())
        assert returned is not None
        # The consumer is a done-callback, so it only records once the helper task runs to
        # completion — drain the loop, then assert exactly that one task reached it.
        pending = asyncio.all_tasks(loop)
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        assert len(tagged) == 1
    finally:
        loop.close()
