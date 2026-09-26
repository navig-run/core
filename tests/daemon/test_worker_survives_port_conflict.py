"""The Telegram worker must not die because its EXTRA HTTP server can't bind.

This worker's job is the Telegram bot. The deck HTTP server it also starts is a
bonus — and when the daemon additionally runs a dedicated `gateway` child (daemon
config `gateway: true`, which is what carries the Lighthouse uplink), BOTH bind
`gateway.config.port`. The loser raised straight out of `site.start()` and took the
whole telegram-bot process down.

Measured on the operator's install::

    OSError: [Errno 10048] error while attempting to bind on address
             ('127.0.0.1', 8789): only one usage of each socket address ...

    telegram-bot exited with code 1 - restarting in 120s   (attempt 536)

A crash loop every two minutes for ~18 hours. The supervisor dutifully restarted it
into the same conflict each time, and the child's log lived under
`%LOCALAPPDATA%/navig/logs/telegram-bot.log` — not with the other logs — so
nothing visible said why.

Skipping is deliberately better than retrying on a free port: a second deck API on a
DIFFERENT port is a split brain (both 8789 and 5176 answered /api/deck/status during
that window) and clients resolve a single address through gateway.json.
"""

from __future__ import annotations

import errno

import pytest

from navig.daemon import telegram_worker as tw


class _Runner:
    def __init__(self) -> None:
        self.cleaned = False

    async def setup(self) -> None:
        pass

    async def cleanup(self) -> None:
        self.cleaned = True


class _Cfg:
    host = "127.0.0.1"
    port = 8789


class _Gateway:
    def __init__(self) -> None:
        self._app = object()
        self._runner: object | None = None
        self.config = _Cfg()
        self.running = True


class _Site:
    """A TCPSite whose start() fails the way a taken port does."""

    def __init__(self, exc: Exception | None) -> None:
        self._exc = exc
        self.started = False

    async def start(self) -> None:
        if self._exc:
            raise self._exc
        self.started = True


def _patch(monkeypatch: pytest.MonkeyPatch, exc: Exception | None) -> dict:
    """Stub the two aiohttp objects that touch the network, nothing else.

    ⚠ `_start_gateway_http` does `from aiohttp import web` INSIDE the function, so
    patching a `web` attribute on the worker module does nothing — the first version
    of this test did exactly that and silently ran against REAL aiohttp, attempting
    a real bind on the operator's live port. Patch aiohttp.web itself.

    `web.Application` is left real: it allocates no socket, and `@web.middleware`
    above it needs the genuine decorator.
    """
    from aiohttp import web as aiohttp_web

    made: dict = {}

    def _runner(app, **kw):
        made["runner"] = _Runner()
        return made["runner"]

    def _site(runner, host, port, **kw):
        made["site"] = _Site(exc)
        made["bound"] = (host, port)
        return made["site"]

    monkeypatch.setattr(aiohttp_web, "AppRunner", _runner)
    monkeypatch.setattr(aiohttp_web, "TCPSite", _site)

    # Imported INSIDE the function, so patch it on its source module.
    import navig.gateway.routes as _routes

    monkeypatch.setattr(_routes, "register_all_routes", lambda *a, **k: None)
    return made


async def test_a_taken_port_does_not_kill_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE regression: this used to propagate and exit the process with code 1."""
    gw = _Gateway()
    made = _patch(monkeypatch, OSError(errno.EADDRINUSE, "address in use"))

    await tw._start_gateway_http(gw, {"enabled": False}, {})

    assert made["runner"].cleaned, "the AppRunner was leaked after a failed bind"
    assert gw._runner is None, (
        "a half-set runner would be cleaned up a second time on shutdown"
    )


async def test_a_free_port_still_starts_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anti-vacuity floor: if the happy path stopped starting, the test above would
    pass for the wrong reason."""
    gw = _Gateway()
    made = _patch(monkeypatch, None)

    await tw._start_gateway_http(gw, {"enabled": False}, {})

    assert made["site"].started is True
    assert gw._runner is made["runner"], "the runner must be kept for shutdown"


async def test_an_unrelated_oserror_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only EADDRINUSE is survivable. Swallowing every OSError would hide a real
    failure to serve behind a warning."""
    gw = _Gateway()
    _patch(monkeypatch, OSError(errno.EACCES, "permission denied"))

    with pytest.raises(OSError):
        await tw._start_gateway_http(gw, {"enabled": False}, {})
