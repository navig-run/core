"""`navig doctor` must answer "can a daemon start on this port?", not just "is anyone
listening?".

`check_sockets` used `connect_ex` alone and then disclaimed the gap in prose — ✓ Port
8789 is not in use (OS-reserved ranges may still block binding …). That is a green tick
over an unknown, the exact class `test_doctor_honesty` exists to stop, and the unknown is
answerable: attempt the bind.

The two questions come apart precisely where it matters. **Windows RESERVES port ranges**
(Hyper-V / WSL / Docker): `bind()` raises PermissionError while nothing is listening and
`Get-NetTCPConnection` reports the port free. Measured on the developer machine that
found this — 8680-8779, 8790-8889, 9011-9110, 9181-9580 reserved, `_DAEMON_PORT` (8765)
inside the first, and the gateway's 8789 surviving only by landing in a one-port gap.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.commands import doctor

pytestmark = pytest.mark.unit


def _sock(*, listening: bool, bind_error: OSError | None = None) -> MagicMock:
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.connect_ex.return_value = 0 if listening else 111
    if bind_error is not None:
        m.bind.side_effect = bind_error
    return m


def _row(results, label):
    for _icon, ok, text in results:
        if label in text:
            return ok, text
    raise AssertionError(f"no {label!r} row in {results}")


def test_a_free_and_bindable_port_is_green():
    with patch.object(doctor.socket, "socket", return_value=_sock(listening=False)):
        results = doctor.check_sockets(target_port=8789)

    ok, text = _row(results, "Port Occupation")
    assert ok is True
    assert "bindable" in text, text


def test_a_listening_port_is_green_when_this_install_owns_it():
    """A bound port is healthy only when the listener is OURS.

    This test used to assert "listening ⇒ green", full stop — which is the contract that
    produced a green ✓ over a second NAVIG authority on 2026-10-22. The ownership question
    is now part of the row, so the mock must answer it: a listening port whose PID is in
    this install's pid files is genuinely healthy and must stay green.
    """
    with (
        patch.object(doctor.socket, "socket", return_value=_sock(listening=True)),
        patch.object(doctor, "_port_owner_pids", return_value={4242}),
        patch.object(doctor, "_managed_daemon_pids", return_value={4242}),
    ):
        results = doctor.check_sockets(target_port=8789)

    ok, text = _row(results, "Port Occupation")
    assert ok is True, text
    assert "this install" in text.lower(), text


def test_a_listening_port_owned_by_anyone_else_is_RED():
    """The 2026-10-22 symptom: something is listening on the gateway port and it is not us."""
    with (
        patch.object(doctor.socket, "socket", return_value=_sock(listening=True)),
        patch.object(doctor, "_port_owner_pids", return_value={10792}),
        patch.object(doctor, "_managed_daemon_pids", return_value={38248}),
    ):
        results = doctor.check_sockets(target_port=8789)

    ok, text = _row(results, "Port Occupation")
    assert ok is False, "a foreign listener must never be reported as a healthy gateway"
    assert "second NAVIG authority" in text, text


def test_free_but_UNBINDABLE_is_not_green():
    """THE bug: nothing listening AND nothing can bind — previously a cheerful ✓.

    PermissionError is the Windows reserved-range shape specifically.
    """
    err = PermissionError(13, "An attempt was made to access a socket in a way "
                              "forbidden by its access permissions")
    with patch.object(doctor.socket, "socket", return_value=_sock(listening=False, bind_error=err)):
        results = doctor.check_sockets(target_port=8789)

    ok, text = _row(results, "Port Occupation")
    assert ok is False, "cannot bind ≠ healthy — a green light here tells you not to look"
    assert "NOT bindable" in text, text


def test_the_unbindable_message_is_actionable_on_windows(monkeypatch):
    """An operator who cannot see WHY a port is unusable will blame NAVIG. Name the
    one command that reveals a reservation."""
    monkeypatch.setattr(doctor.sys, "platform", "win32")
    err = OSError(13, "forbidden")
    with patch.object(doctor.socket, "socket", return_value=_sock(listening=False, bind_error=err)):
        results = doctor.check_sockets(target_port=8789)

    _ok, text = _row(results, "Port Occupation")
    assert "excludedportrange" in text, text


def test_the_hint_is_omitted_off_windows(monkeypatch):
    """Anti-vacuity for the test above: the hint must be conditional, not always present,
    or it is noise on the platforms where reservations do not exist."""
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    err = OSError(98, "Address already in use")
    with patch.object(doctor.socket, "socket", return_value=_sock(listening=False, bind_error=err)):
        results = doctor.check_sockets(target_port=8789)

    _ok, text = _row(results, "Port Occupation")
    assert "excludedportrange" not in text, text
    assert "NOT bindable" in text, text


def test_the_daemon_port_is_checked_too():
    """The gateway is not the only port NAVIG binds. Checking one and labelling the row
    "Network Sockets" protected a path, not the surface — and `_DAEMON_PORT` is the one
    actually sitting inside a reservation on the machine that found this.
    """
    with patch.object(doctor.socket, "socket", return_value=_sock(listening=False)):
        results = doctor.check_sockets(target_port=8789)

    ok, text = _row(results, "Daemon Port")
    assert ok is True
    assert str(doctor._DAEMON_PORT) in text, text


def test_the_daemon_row_is_skipped_when_it_IS_the_target():
    """No duplicate PORT row when the two ports coincide (a custom gateway port of 8765).

    The row count is no longer the assertion: check_sockets also reports the brain role
    (client vs brain), which has nothing to do with port duplication. Assert on the
    port rows this test is actually about, so an unrelated row cannot either hide a
    duplicate or fail this test when it is added.
    """
    with patch.object(doctor.socket, "socket", return_value=_sock(listening=False)):
        results = doctor.check_sockets(target_port=doctor._DAEMON_PORT)

    port_rows = [r for r in results if r[0] is not None and "Port" in r[2] or "Daemon Port" in r[2]]
    labels = [r[2] for r in results if "Port Occupation" in r[2] or "Daemon Port" in r[2]]
    assert len(labels) == 1, results
    assert "Daemon Port" not in " ".join(labels), "the daemon row must be skipped when it IS the target"


def _daemon_row_with_listener(cmdline):
    """check_sockets with the gateway port free and the daemon port held by *cmdline*."""
    def _socket(*_a, **_k):
        return _sock(listening=True)

    with (
        patch.object(doctor.socket, "socket", side_effect=_socket),
        patch.object(doctor, "_port_owner_pids", return_value={4242}),
        patch.object(doctor, "_managed_daemon_pids", return_value={4242}),
        patch.object(doctor, "_gateway_owner", return_value=cmdline),
    ):
        results = doctor.check_sockets(target_port=8789)
    return _row(results, "Daemon Port")


def test_a_foreign_listener_on_the_daemon_port_is_not_a_running_daemon():
    """Any process on 8765 answered connect_ex and was reported "daemon running" —
    while that very listener is what keeps the IPC/MCP daemon from binding."""
    ok, text = _daemon_row_with_listener(r"C:\tools\other-server.exe --port 8765")

    assert ok is False, text
    assert "other-server.exe" in text, text
    assert "daemon running" not in text, text


def test_a_navig_listener_on_the_daemon_port_stays_green():
    ok, text = _daemon_row_with_listener("pythonw.exe -m navig.daemon.entry")

    assert ok is True, text
    assert "daemon running" in text, text


def test_an_unidentifiable_daemon_port_listener_is_not_green():
    ok, text = _daemon_row_with_listener(None)

    assert ok is False, text
