"""NavigDaemon._kill_orphan_daemons must be scoped to OUR config dir.

The orphan sweep enumerates navig processes machine-wide (PowerShell / pgrep matching
``navig gateway start`` / ``navig.daemon`` / ``telegram_worker``) and force-kills them.
Unscoped, a ``navig service stop`` under a different ``NAVIG_CONFIG_DIR`` — or a daemon
booting under one — force-killed the operator's LIVE brain (all lights green, no
shutdown line). This mirrors the gateway supersede guard: a process whose config dir
differs from ours, or can't be read, is left alone.
"""

from __future__ import annotations

import os
from pathlib import Path

from navig.daemon.supervisor import NavigDaemon

_MINE = Path("/home/u/.navig")
_THEIRS = Path("/tmp/smoke-config")


def _reader(mapping: dict[int, Path | None]):
    return lambda pid: mapping.get(pid)


def test_kills_only_processes_sharing_our_config_dir():
    """THE REGRESSION: a daemon/gateway on a different config dir is a different brain."""
    killed: list[int] = []
    out = NavigDaemon._kill_orphan_daemons(
        config_dir=_MINE,
        # 101 is the operator's real gateway on a DIFFERENT config dir; 202/303 are ours.
        pids=[101, 202, 303],
        config_dir_reader=_reader({101: _THEIRS, 202: _MINE, 303: _MINE}),
        killer=killed.append,
    )
    assert out == [202, 303], "only processes sharing OUR config dir may be killed"
    assert killed == [202, 303]
    assert 101 not in out, "killed a process belonging to a different brain (the catastrophe)"


def test_unreadable_config_dir_is_never_killed():
    """A process whose config dir can't be read (foreign user / no psutil) is left alone."""
    killed: list[int] = []
    out = NavigDaemon._kill_orphan_daemons(
        config_dir=_MINE,
        pids=[101, 202],
        config_dir_reader=_reader({101: None, 202: None}),
        killer=killed.append,
    )
    assert out == []
    assert killed == []


def test_unresolved_reader_path_still_matches_ours():
    """A reader handing back an UNRESOLVED path must not widen the sweep machine-wide:
    both sides are normalized, so our own brain is still correctly matched."""
    killed: list[int] = []
    out = NavigDaemon._kill_orphan_daemons(
        config_dir=_MINE.resolve(),  # caller passes a resolved dir
        pids=[202],
        config_dir_reader=_reader({202: _MINE}),  # reader returns the UNRESOLVED form
        killer=killed.append,
    )
    assert out == [202], "normalization mismatch would silently skip our own stale instance"


class TestAncestorsAreNeverKilled:
    """THE REGRESSION: the sweep killed the daemon that was starting.

    ``_enumerate_navig_pids`` matches any command line *mentioning* ``navig.daemon`` —
    which includes this process's own launcher chain (``py.exe`` → ``python.exe``) — and
    ``_force_kill_pid`` is ``taskkill /F /T``, a TREE kill. Sweeping the launcher took
    the booting daemon down with it, before ``_write_pid``: the ``navig service install``
    task ran, exited 1, wrote no log, and autostart silently delivered nothing.
    """

    def test_an_ancestor_is_left_alone(self):
        killed: list[int] = []
        out = NavigDaemon._kill_orphan_daemons(
            config_dir=_MINE,
            pids=[777, 202],
            keep={os.getpid(), 777},  # 777 = the launcher that spawned us
            config_dir_reader=_reader({777: _MINE, 202: _MINE}),
            killer=killed.append,
        )
        assert out == [202], "killing an ancestor tree kills the daemon that is starting"
        assert 777 not in killed

    def test_a_stale_sibling_is_still_reaped(self):
        """Ancestor protection must not turn the sweep into a no-op."""
        killed: list[int] = []
        out = NavigDaemon._kill_orphan_daemons(
            config_dir=_MINE,
            pids=[202],
            keep={os.getpid()},
            config_dir_reader=_reader({202: _MINE}),
            killer=killed.append,
        )
        assert out == [202]

    def test_ancestors_are_computed_when_not_injected(self, monkeypatch):
        """Production path: the real sweep asks single_instance for the protected tree."""
        import navig.daemon.single_instance as si

        monkeypatch.setattr(si, "ancestor_pids", lambda: {os.getpid(), 4242})
        killed: list[int] = []
        NavigDaemon._kill_orphan_daemons(
            config_dir=_MINE,
            pids=[4242, 202],
            config_dir_reader=_reader({4242: _MINE, 202: _MINE}),
            killer=killed.append,
        )
        assert killed == [202]

    def test_unavailable_ancestor_lookup_still_protects_self(self, monkeypatch):
        import navig.daemon.single_instance as si

        def boom():
            raise RuntimeError("psutil missing")

        monkeypatch.setattr(si, "ancestor_pids", boom)
        me = os.getpid()
        killed: list[int] = []
        NavigDaemon._kill_orphan_daemons(
            config_dir=_MINE,
            pids=[me, 202],
            config_dir_reader=_reader({me: _MINE, 202: _MINE}),
            killer=killed.append,
        )
        assert killed == [202]


def test_self_and_exclude_pid_are_skipped():
    killed: list[int] = []
    me = os.getpid()
    out = NavigDaemon._kill_orphan_daemons(
        exclude_pid=999,
        config_dir=_MINE,
        pids=[999, me, 202],
        config_dir_reader=_reader({999: _MINE, me: _MINE, 202: _MINE}),
        killer=killed.append,
    )
    assert out == [202], "must never kill self or the explicitly-excluded pid"
    assert 999 not in out
    assert me not in out
