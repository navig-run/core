"""The gateway's loop exception filter drops exactly the known-benign reports.

Regression for a daemon log line:

    Task exception was never retrieved
    future: <Task finished ... coro=<RequestHandler.start() ...>
        exception=TypeError("'NoneType' object is not callable")>

An upstream aiohttp race (present through 3.14.4): a request is queued, the client
hangs up before ``start()`` resumes, ``connection_lost`` clears ``_request_factory``,
and ``start()`` calls ``None``. The race is reproduced against the REAL aiohttp
handler, so the filter is matched against the context asyncio actually builds.

Also pins that the filter is installed by ``NavigGateway.start()`` itself — it used to
live only in ``run_gateway()``, which no live entry point calls.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock

import pytest

aiohttp_web = pytest.importorskip("aiohttp.web")

from navig.gateway import server as gateway_server  # noqa: E402
from navig.gateway.server import install_loop_noise_filter  # noqa: E402


async def _reproduce_request_factory_race() -> dict:
    """Drive aiohttp into the race; return the context asyncio's ``Task.__del__`` reports."""

    async def _handler(_request):
        return aiohttp_web.Response(text="ok")

    proto = aiohttp_web.Server(_handler)()
    transport = MagicMock()
    transport.is_closing.return_value = False
    transport.get_extra_info.return_value = None
    proto.connection_made(transport)
    task = proto._task_handler
    await asyncio.sleep(0)  # start() is now parked on its waiter

    # Same loop iteration: the request arrives, then the peer is gone.
    proto.data_received(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    proto.connection_lost(None)
    for _ in range(5):
        await asyncio.sleep(0)
    assert task is not None and task.done()
    exc = task.exception()
    assert isinstance(exc, TypeError), f"race did not reproduce: {exc!r}"
    # The exact dict asyncio builds for "Task exception was never retrieved".
    return {"message": "Task exception was never retrieved", "exception": exc, "future": task}


async def test_aiohttp_race_reproduces_and_is_filtered():
    ctx = await _reproduce_request_factory_race()
    assert isinstance(ctx.get("exception"), TypeError)
    assert gateway_server._is_aiohttp_request_factory_race(ctx)

    loop = asyncio.get_running_loop()
    delegated: list[dict] = []
    loop.set_exception_handler(lambda _l, c: delegated.append(c))
    install_loop_noise_filter(loop)
    loop.call_exception_handler(ctx)
    assert delegated == []


async def test_unrelated_type_error_still_surfaces():
    loop = asyncio.get_running_loop()
    delegated: list[dict] = []
    loop.set_exception_handler(lambda _l, c: delegated.append(c))
    install_loop_noise_filter(loop)

    def _ours():
        f = None
        f()  # same message, but raised from OUR code, not aiohttp

    try:
        _ours()
    except TypeError as e:
        ctx = {"message": "Task exception was never retrieved", "exception": e}
    loop.call_exception_handler(ctx)
    assert delegated == [ctx]


async def test_proactor_reset_filtered_other_resets_delegated():
    loop = asyncio.get_running_loop()
    delegated: list[dict] = []
    loop.set_exception_handler(lambda _l, c: delegated.append(c))
    install_loop_noise_filter(loop)

    benign = ConnectionResetError(10054, "reset")
    benign.winerror = 10054  # type: ignore[attr-defined]
    loop.call_exception_handler({"message": "x", "exception": benign})
    other = {"message": "x", "exception": ConnectionResetError("reset")}
    loop.call_exception_handler(other)
    assert delegated == [other]


async def test_install_is_idempotent():
    loop = asyncio.get_running_loop()
    delegated: list[dict] = []
    loop.set_exception_handler(lambda _l, c: delegated.append(c))
    install_loop_noise_filter(loop)
    first = loop.get_exception_handler()
    install_loop_noise_filter(loop)
    assert loop.get_exception_handler() is first
    ctx = {"message": "boom", "exception": RuntimeError("boom")}
    loop.call_exception_handler(ctx)
    assert delegated == [ctx]  # delivered once, not twice through a double chain


def test_gateway_start_installs_the_filter():
    src = inspect.getsource(gateway_server.NavigGateway.start)
    assert "install_loop_noise_filter(asyncio.get_running_loop())" in src
