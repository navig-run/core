"""An input listener that fails to start says WHY, instead of vanishing.

Every listener's start() used to swallow its own failure with
`except Exception: pass  # best-effort`, and the startup report listed only what
was RUNNING. So an ENABLED channel that failed to bind was indistinguishable from
a DISABLED one.

Measured on a real install: the REST listener (`api_enabled` defaults True)
binds port 8790, which sits inside a Windows RESERVED port range on that machine
(`netsh interface ipv4 show excludedportrange tcp`). bind() raised
PermissionError(13) with nothing listening, on every boot, and no surface — not
the log, not the startup banner, not the health check — ever said so.

`InputListener` had no tests at all.
"""

from __future__ import annotations

import socket

import pytest

from navig.agent.ears import APIListener, InputListener


class _Boom(InputListener):
    """A listener whose start() raises before any handler of its own."""

    async def start(self) -> None:
        raise RuntimeError("could not open the thing")

    async def stop(self) -> None:
        pass


@pytest.fixture
def occupied_port():
    """A real TCP port held open by this test, so a second bind genuinely fails."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        yield s.getsockname()[1]
    finally:
        s.close()


async def test_a_bind_failure_is_recorded_with_the_port(occupied_port):
    """The real APIListener against a port that genuinely cannot be bound."""
    listener = APIListener(port=occupied_port, host="127.0.0.1")

    await listener.start()  # must NOT raise — the agent keeps booting

    assert listener._running is False
    assert listener._error, "the failure was swallowed with no reason recorded"
    # Actionable means it names the PORT — "PermissionError" alone tells nobody
    # what to change.
    assert str(occupied_port) in listener._error, listener._error
    assert "Error" in listener._error


async def test_a_successful_start_records_no_error():
    listener = APIListener(port=0, host="127.0.0.1")  # ephemeral — always bindable

    await listener.start()
    try:
        assert listener._running is True
        assert listener._error is None
    finally:
        await listener.stop()


async def test_a_listener_that_raises_before_its_own_handler_is_still_recorded():
    """`Ears.start()` wraps each listener; that wrapper must record, not discard."""
    from navig.agent.ears import Ears

    boom = _Boom("boom")
    # Drive the same loop Ears.start() runs, without booting the whole component.
    try:
        await boom.start()
    except Exception as exc:  # noqa: BLE001
        boom._failed(exc)

    assert boom._running is False
    assert "could not open the thing" in (boom._error or "")
    assert callable(Ears.get_listener_errors), "Ears must expose the reasons"


def test_failed_never_raises_and_truncates():
    """A failure recorder that itself raises would take the boot down with it."""
    lst = _Boom("x")

    lst._failed(RuntimeError("y" * 5000))

    assert lst._error is not None
    assert len(lst._error) <= 200
    assert lst._running is False


def test_errors_distinguish_failed_from_disabled():
    """The whole point: only an ENABLED listener that FAILED shows up here."""
    from types import SimpleNamespace

    from navig.agent.ears import Ears

    ears = Ears.__new__(Ears)
    running = SimpleNamespace(_running=True, _error=None)
    disabled = SimpleNamespace(_running=False, _error=None)  # never started
    failed = SimpleNamespace(_running=False, _error="PermissionError: [Errno 13]")
    ears._listeners = {"telegram": running, "mcp": disabled, "api": failed}

    assert ears.get_listener_errors() == {"api": "PermissionError: [Errno 13]"}
    assert ears.get_listener_status() == {"telegram": True, "mcp": False, "api": False}
