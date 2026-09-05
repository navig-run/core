"""A health-check port that cannot bind must not take the whole daemon down.

`_start_health_server()` is awaited at the top of `_supervisor_loop()` — OUTSIDE the
try/except that wraps everything after it. So an escaping `OSError` from
`asyncio.start_server` meant not one supervised child was ever started: the
*diagnostics* killing the *supervision*.

It is reachable: `--health-port` is chosen when installing NAVIG as a PERSISTENT
service (systemd / NSSM / Task Scheduler), so a service manager would restart the
daemon into the identical failure forever. And the port need not be "in use" to be
unbindable — Windows RESERVES whole ranges (Hyper-V / WSL / Docker) where `bind()`
raises PermissionError with nothing listening; on this machine 8680-8779, 8790-8889,
9011-9110 and 9181-9580 are all reserved, so an operator picking a round number has a
real chance of landing in one.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.daemon import supervisor as sup

pytestmark = pytest.mark.unit


def _daemon(health_port: int):
    return sup.NavigDaemon(health_port=health_port)


async def _start(daemon) -> None:
    await daemon._start_health_server()


def test_a_disabled_health_port_binds_nothing(monkeypatch):
    """Anti-vacuity: 0 means off, and must not be confused with a failed bind."""
    called = False

    async def _never(*_a, **_k):
        nonlocal called
        called = True

    monkeypatch.setattr(asyncio, "start_server", _never)
    d = _daemon(0)
    asyncio.run(_start(d))

    assert called is False
    assert d._health_server is None


def test_an_unbindable_health_port_does_not_propagate(monkeypatch):
    """The bug: this OSError used to escape and abort `_supervisor_loop` before any
    child started. PermissionError is the Windows reserved-range shape specifically."""

    async def _refuse(*_a, **_k):
        raise PermissionError(13, "An attempt was made to access a socket in a way "
                                  "forbidden by its access permissions")

    monkeypatch.setattr(asyncio, "start_server", _refuse)
    d = _daemon(8765)

    asyncio.run(_start(d))  # must NOT raise

    assert d._health_server is None, "a failed bind must leave no half-open server"


def test_the_failure_is_reported_actionably(monkeypatch, capsys):
    """A degradation nobody can see is the trap this repo keeps re-learning.

    The message must name the port, say the daemon continues, and point at the
    Windows reserved-range check — otherwise the operator reads "no health endpoint"
    as "the daemon is broken" and starts debugging the wrong thing.
    """
    async def _refuse(*_a, **_k):
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(asyncio, "start_server", _refuse)
    d = _daemon(9300)

    logged: list[str] = []
    monkeypatch.setattr(d.logger, "error", lambda msg, *a: logged.append(msg % a if a else msg))

    asyncio.run(_start(d))

    assert len(logged) == 1, logged
    line = logged[0]
    assert "9300" in line, line
    assert "WITHOUT it" in line, "must say the daemon carries on"
    assert "excludedportrange" in line, "must point at the Windows reserved-range check"
    assert "--health-port" in line, "must name the flag that fixes it"


def test_a_successful_bind_is_still_recorded(monkeypatch):
    """Anti-vacuity for all of the above: the happy path must still set the server,
    or 'never raises' could be satisfied by never binding at all."""
    sentinel = object()

    async def _ok(*_a, **_k):
        return sentinel

    monkeypatch.setattr(asyncio, "start_server", _ok)
    d = _daemon(9300)
    asyncio.run(_start(d))

    assert d._health_server is sentinel
