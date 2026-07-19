"""`navig gateway start` must not shoot the operator's brain to claim a port.

The start sequence has TWO kill paths. #173 scoped the cmdline sweep
(`_supersede_other_gateways`) by config dir — and left `_free_port` completely unscoped:
it `taskkill /F`'d **whatever** was listening on the port, with no identity check at all.

So a gateway started from ANY other navig — a test, a smoke run, a second venv — that
resolved to the same port force-killed the operator's LIVE production daemon. That is
precisely the bug #173 claimed to close, reached through the other door. It happened for
real: the daemon was force-killed with no shutdown line in the log, and the bot went
offline until someone noticed.

The rule is the one `config_dir_of` already documents: a process you cannot identify is
NOT yours, and you must never kill what you cannot identify.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.commands import gateway

OURS = Path("/home/op/.navig").resolve()
THEIRS = Path("/tmp/isolated-test-config").resolve()

LIVE_DAEMON = 4242
UNRELATED_APP = 7777
OUR_STALE = 1234


@pytest.fixture
def ours(monkeypatch):
    # _free_port imports `paths` INSIDE the function, so patch it at the source module.
    monkeypatch.setattr("navig.platform.paths.config_dir", lambda: OURS)
    return OURS


def _run(monkeypatch, holders, dirs) -> list[int]:
    """Drive _free_port with an injected process table; returns the PIDs it killed."""
    killed: list[int] = []
    returned = gateway._free_port(
        8789,
        holders_reader=lambda _port: holders,
        config_dir_reader=lambda pid: dirs.get(pid),
        killer=killed.append,
    )
    assert returned == killed, "the reported kills must match what was actually killed"
    return killed


def test_a_gateway_on_another_config_dir_is_NOT_killed(ours, monkeypatch):
    """THE bug. A test/CI gateway on an isolated config dir claimed port 8789 and
    executed the operator's live daemon to get it."""
    killed = _run(monkeypatch, [LIVE_DAEMON], {LIVE_DAEMON: THEIRS})

    assert killed == [], "a different config dir is a DIFFERENT BRAIN — never kill it"


def test_a_process_we_cannot_identify_is_NOT_killed(ours, monkeypatch):
    """config_dir_of() returns None when the environment is unreadable (another user,
    no psutil, permission denied). Unknown is not 'mine'."""
    killed = _run(monkeypatch, [UNRELATED_APP], {UNRELATED_APP: None})

    assert killed == [], "you must never kill what you cannot identify"


def test_our_own_stale_gateway_IS_killed(ours, monkeypatch):
    """The legitimate purpose survives: one brain per config dir. A stale instance of
    OURS keeps serving old cached code and must be superseded."""
    killed = _run(monkeypatch, [OUR_STALE], {OUR_STALE: OURS})

    assert killed == [OUR_STALE]


def test_only_our_own_is_killed_when_several_hold_the_port(ours, monkeypatch):
    """The realistic table: our stale instance, a foreign brain, and an unknown process."""
    killed = _run(
        monkeypatch,
        [OUR_STALE, LIVE_DAEMON, UNRELATED_APP],
        {OUR_STALE: OURS, LIVE_DAEMON: THEIRS, UNRELATED_APP: None},
    )

    assert killed == [OUR_STALE]
    assert LIVE_DAEMON not in killed and UNRELATED_APP not in killed


def test_nothing_holds_the_port(ours, monkeypatch):
    assert _run(monkeypatch, [], {}) == []


def test_we_kill_nothing_if_we_cannot_identify_OURSELVES(monkeypatch):
    """If our own config dir cannot be resolved we have no basis for comparison — so we
    have no business killing anything."""
    def boom():
        raise OSError("no home dir")

    monkeypatch.setattr("navig.platform.paths.config_dir", boom)

    killed: list[int] = []
    gateway._free_port(
        8789,
        holders_reader=lambda _p: [OUR_STALE],
        config_dir_reader=lambda _pid: OURS,
        killer=killed.append,
    )

    assert killed == []
