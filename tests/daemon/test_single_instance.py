"""Tests for navig.daemon.single_instance — single-instance enforcement.

A restart must supersede every other instance of a role (so no stale process keeps
serving old cached code), WITHOUT ever killing the current process or its
ancestors (the supervisor that spawned us, NSSM, the launching shell).
"""

from __future__ import annotations

import os
from pathlib import Path

from navig.daemon import single_instance as si


def _table():
    return [
        (1001, "C:\\Python\\pythonw.exe -m navig gateway start --port 8789"),
        (1002, "C:\\Python\\pythonw.exe -m navig.daemon.entry"),
        (1003, "C:\\Python\\python.exe -m navig.daemon.telegram_worker"),
        (1004, "C:\\Python\\python.exe some_other_app.py"),
        (1005, "/usr/bin/python -m navig gateway start"),
    ]


def test_kills_only_matching_gateways():
    killed: list[int] = []
    out = si.kill_other_instances(
        si.GATEWAY_PATTERNS, table=_table(), keep={os.getpid()}, killer=killed.append
    )
    # Both gateway processes killed; the worker + unrelated app are left alone.
    assert set(out) == {1001, 1005}
    assert killed == out
    assert 1003 not in out  # telegram_worker is a different role
    assert 1004 not in out  # unrelated process


def test_never_kills_self_or_ancestors():
    killed: list[int] = []
    # 1001 is a gateway but it's "us"; 1005 is a gateway but it's an ancestor.
    out = si.kill_other_instances(
        si.GATEWAY_PATTERNS, table=_table(), keep={1001, 1005}, killer=killed.append
    )
    assert out == []  # both matching gateways are protected
    assert killed == []


def test_daemon_patterns_match_all_daemon_modules():
    killed: list[int] = []
    out = si.kill_other_instances(
        si.DAEMON_PATTERNS, table=_table(), keep={os.getpid()}, killer=killed.append
    )
    # '-m navig.daemon' matches entry AND telegram_worker (both navig.daemon.*),
    # which is what a daemon-level sweep wants; the lone gateway is left alone.
    assert set(out) == {1002, 1003}
    assert 1001 not in out and 1005 not in out  # gateways untouched by DAEMON_PATTERNS


def test_empty_table_is_noop():
    assert si.kill_other_instances(si.GATEWAY_PATTERNS, table=[], keep=set(), killer=lambda p: None) == []


# ── config-dir scoping: a different brain must never be killed ────────────────
#
# Unscoped, this sweep matched on cmdline alone and force-killed machine-wide. So a
# gateway started from ANY other navig — a second venv, a CI job, a temp-config smoke
# test — took down the operator's LIVE production daemon. (Verified: the operator's
# gateway runs as `pythonw.exe -m navig gateway start`, exactly what the pattern matches.)
# "Never boot a second gateway locally" was a standing rule *because* of this.

_MINE = Path("/home/u/.navig")
_THEIRS = Path("/tmp/smoke-config")


def _cfg_reader(mapping: dict[int, Path | None]):
    return lambda pid: mapping.get(pid)


def test_scoped_sweep_leaves_another_brain_alone():
    """THE REGRESSION: a gateway on a different config dir is a different brain."""
    killed: list[int] = []
    out = si.kill_other_instances(
        si.GATEWAY_PATTERNS, table=_table(), keep={os.getpid()}, killer=killed.append,
        config_dir=_MINE,
        # 1001 is the operator's real gateway; 1005 is a temp-config smoke test.
        config_dir_reader=_cfg_reader({1001: _THEIRS, 1005: _MINE}),
    )
    assert out == [1005], "only the gateway sharing OUR config dir may be killed"
    assert 1001 not in out, "killed a gateway belonging to a different brain"
    assert killed == out


def test_scoped_sweep_still_supersedes_my_own_stale_gateway():
    """The dev intent survives: a stale gateway on MY config dir is still reaped."""
    killed: list[int] = []
    out = si.kill_other_instances(
        si.GATEWAY_PATTERNS, table=_table(), keep={os.getpid()}, killer=killed.append,
        config_dir=_MINE, config_dir_reader=_cfg_reader({1001: _MINE, 1005: _MINE}),
    )
    assert set(out) == {1001, 1005}


def test_unreadable_config_dir_is_never_killed():
    """You must not kill what you cannot identify (another user's process, no psutil)."""
    killed: list[int] = []
    out = si.kill_other_instances(
        si.GATEWAY_PATTERNS, table=_table(), keep={os.getpid()}, killer=killed.append,
        config_dir=_MINE, config_dir_reader=_cfg_reader({1001: None, 1005: None}),
    )
    assert out == []
    assert killed == []


def test_unscoped_call_is_still_machine_wide():
    """Back-compat: omitting config_dir keeps the old (dangerous) behaviour, so this is
    an opt-in narrowing rather than a silent change under every caller."""
    out = si.kill_other_instances(
        si.GATEWAY_PATTERNS, table=_table(), keep={os.getpid()}, killer=lambda p: None
    )
    assert set(out) == {1001, 1005}


def test_gateway_supersede_passes_our_config_dir():
    """The real caller must scope the sweep — an unscoped call here is the whole bug."""
    import navig.commands.gateway as gw

    seen: dict = {}

    def fake_kill(patterns, **kw):
        seen["patterns"] = patterns
        seen["config_dir"] = kw.get("config_dir")
        return []

    import navig.daemon.single_instance as si_mod
    real = si_mod.kill_other_instances
    si_mod.kill_other_instances = fake_kill
    try:
        gw._supersede_other_gateways()
    finally:
        si_mod.kill_other_instances = real

    assert seen.get("config_dir") is not None, "supersede swept machine-wide (unscoped)"
    from navig.platform import paths
    assert Path(seen["config_dir"]) == paths.config_dir()
