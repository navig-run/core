"""`_port_holders`' netstat fallback must not depend on the English word "LISTENING".

The gateway kills whoever holds its port before binding. When psutil is unavailable the
fallback shells out to ``netstat -ano`` and used to select rows with ``"LISTENING" in
line`` — but that word is localized by Windows (Russian prints ПРОСЛУШИВАНИЕ), so on a
non-English install the fallback matched nothing and the port holder silently became
"nobody". Columns are positional and locale-independent, so that is what it reads now.

Row shape, verified against real output on this machine::

    TCP    0.0.0.0:135    0.0.0.0:0    LISTENING    2300
    proto  local          foreign      state        pid
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

import pytest

from navig.commands.gateway import _port_holders

_LISTENING_RU = "ПРОСЛУШИВАНИЕ"

NETSTAT = (
    "\r\nActive Connections\r\n\r\n"
    "  Proto  Local Address          Foreign Address        State           PID\r\n"
    "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       2300\r\n"
    "  TCP    0.0.0.0:7420           0.0.0.0:0              {state}       4242\r\n"
    "  TCP    127.0.0.1:51000        127.0.0.1:7420         ESTABLISHED     9001\r\n"
    "  TCP    [::]:7420              [::]:0                 {state}       4243\r\n"
    "  UDP    0.0.0.0:7420           *:*                                    5555\r\n"
)


@pytest.fixture
def _no_psutil():
    """Force the subprocess fallback, whatever the machine actually has installed."""
    with patch("psutil.net_connections", side_effect=RuntimeError("no psutil")):
        yield


@pytest.mark.parametrize("state", ["LISTENING", _LISTENING_RU])
def test_holder_is_found_whatever_the_state_word_is(_no_psutil, state) -> None:
    with patch("sys.platform", "win32"), patch.object(
        subprocess, "check_output", return_value=NETSTAT.format(state=state)
    ):
        pids = _port_holders(7420)

    assert 4242 in pids, f"the port holder was not found with state={state!r}"
    assert 4243 in pids, "an IPv6 listener on the same port is also a holder"


def test_a_foreign_address_on_that_port_is_not_a_holder(_no_psutil) -> None:
    """9001 connects TO 7420 from port 51000; it does not hold it.

    Characterization, not a regression: the old code's ``f":{port} " in line`` matched this
    row's foreign column, but its ``"LISTENING" in line`` conjunct then excluded it. Reading
    the local-address column reaches the same verdict without depending on that accident.
    """
    with patch("sys.platform", "win32"), patch.object(
        subprocess, "check_output", return_value=NETSTAT.format(state="LISTENING")
    ):
        pids = _port_holders(7420)

    assert 9001 not in pids


def test_unrelated_ports_are_ignored(_no_psutil) -> None:
    with patch("sys.platform", "win32"), patch.object(
        subprocess, "check_output", return_value=NETSTAT.format(state="LISTENING")
    ):
        assert 2300 not in _port_holders(7420)
        assert _port_holders(135) == [2300]


def test_our_own_pid_is_never_reported(_no_psutil) -> None:
    mine = os.getpid()
    rows = (
        "  Proto  Local Address    Foreign Address   State       PID\r\n"
        f"  TCP    0.0.0.0:7420     0.0.0.0:0         LISTENING   {mine}\r\n"
    )
    with patch("sys.platform", "win32"), patch.object(
        subprocess, "check_output", return_value=rows
    ):
        assert _port_holders(7420) == []


def test_a_failed_netstat_yields_no_holders_rather_than_raising(_no_psutil) -> None:
    with patch("sys.platform", "win32"), patch.object(
        subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, "netstat")
    ):
        assert _port_holders(7420) == []
