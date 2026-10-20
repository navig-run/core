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
