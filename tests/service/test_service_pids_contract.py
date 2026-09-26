"""`navig service pids` — the pids an external process sweeper must spare.

To a cleanup script a navig daemon and a leaked helper look the same: python,
parent gone. The operator's hourly sweep killed the daemon twice on 2026-09-14
for exactly that reason, and the sweeper-side fix was hand-patched into their
script. This is the supported contract: every navig-owned tree, rooted at the
pid files navig writes, identity-verified (a recycled pid is not the owner),
expanded to descendants, and printable as a plain exclusion list.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

from navig.commands.service import service_app
from navig.daemon import supervisor as sup

runner = CliRunner()
pytest.importorskip("psutil")


def _sleeper_with_child():
    """A process tree: a python that spawns a python that sleeps."""
    code = "import subprocess, sys, time\np = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(45)'])\ntime.sleep(45)"
    return subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def _kill_tree(proc) -> None:
    try:
        import psutil

        for c in psutil.Process(proc.pid).children(recursive=True):
            c.kill()
    except Exception:  # noqa: BLE001
        pass
    proc.kill()


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Point every root pid file at a throwaway dir."""
    monkeypatch.setattr(sup, "PID_FILE", tmp_path / "supervisor.pid")
    monkeypatch.setattr(sup.paths, "config_dir", lambda: tmp_path)
    (tmp_path / "agent").mkdir()
    return tmp_path


def _wait_for_child(pid: int, timeout: float = 10.0) -> None:
    import psutil

    deadline = time.time() + timeout
    while time.time() < deadline:
        if psutil.Process(pid).children():
            return
        time.sleep(0.1)
    raise AssertionError("the sleeper never spawned its child")


def test_the_supervisor_tree_is_listed_with_its_descendants(roots):
    proc = _sleeper_with_child()
    try:
        (roots / "supervisor.pid").write_text(str(proc.pid), encoding="utf-8")
        _wait_for_child(proc.pid)

        trees = sup.NavigDaemon.owned_process_trees()

        assert [t["role"] for t in trees] == ["supervisor"]
        pids = [m["pid"] for m in trees[0]["members"]]
        assert pids[0] == proc.pid and len(pids) == 2, pids
        assert trees[0]["pid_file"].endswith("supervisor.pid")
    finally:
        _kill_tree(proc)


def test_a_gateway_inside_the_supervisor_tree_is_not_listed_twice(roots):
    proc = _sleeper_with_child()
    try:
        _wait_for_child(proc.pid)
        import psutil

        child = psutil.Process(proc.pid).children()[0].pid
        (roots / "supervisor.pid").write_text(str(proc.pid), encoding="utf-8")
        (roots / "gateway.pid").write_text(str(child), encoding="utf-8")

        trees = sup.NavigDaemon.owned_process_trees()

        assert [t["role"] for t in trees] == ["supervisor"], trees
    finally:
        _kill_tree(proc)


def test_a_standalone_gateway_and_agent_are_their_own_trees(roots):
    a, b = _sleeper_with_child(), _sleeper_with_child()
    try:
        (roots / "gateway.pid").write_text(str(a.pid), encoding="utf-8")
        (roots / "agent" / "agent.pid").write_text(str(b.pid), encoding="utf-8")

        trees = sup.NavigDaemon.owned_process_trees()

        assert [t["role"] for t in trees] == ["gateway", "agent"]
        assert {t["root"] for t in trees} == {a.pid, b.pid}
    finally:
        _kill_tree(a)
        _kill_tree(b)


def test_a_recycled_pid_is_not_ours_to_spare(roots):
    """A pid file older than the process naming it: that process is a stranger.
    Listing it would make a sweeper spare something that is not navig."""
    proc = _sleeper_with_child()
    try:
        f = roots / "supervisor.pid"
        f.write_text(str(proc.pid), encoding="utf-8")
        stale = time.time() - 3600
        os.utime(f, (stale, stale))

        assert sup.NavigDaemon.owned_process_trees() == []
    finally:
        _kill_tree(proc)


def test_a_dead_root_contributes_nothing(roots):
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    (roots / "supervisor.pid").write_text(str(p.pid), encoding="utf-8")

    assert sup.NavigDaemon.owned_process_trees() == []


def test_no_pid_files_means_no_trees(roots):
    assert sup.NavigDaemon.owned_process_trees() == []


# ── the command ──────────────────────────────────────────────────────────────


def _fake_trees(monkeypatch, trees):
    monkeypatch.setattr(sup.NavigDaemon, "owned_process_trees", staticmethod(lambda: trees))


_TREES = [
    {
        "role": "supervisor",
        "pid_file": "C:/u/.navig/daemon/supervisor.pid",
        "root": 100,
        "members": [{"pid": 100, "name": "pythonw.exe"}, {"pid": 101, "name": "pythonw.exe"}],
    },
    {
        "role": "agent",
        "pid_file": "C:/u/.navig/agent/agent.pid",
        "root": 200,
        "members": [{"pid": 200, "name": "python.exe"}],
    },
]


def test_plain_is_one_pid_per_line_and_nothing_else(monkeypatch):
    """The exclusion-list shape: a sweeper pipes this straight into a HashSet."""
    _fake_trees(monkeypatch, _TREES)

    r = runner.invoke(service_app, ["pids", "--plain"])

    assert r.exit_code == 0, r.output
    assert r.output.split() == ["100", "101", "200"]


def test_json_carries_the_pid_files_a_sweeper_may_read_directly(monkeypatch):
    _fake_trees(monkeypatch, _TREES)

    r = runner.invoke(service_app, ["pids", "--json"])

    data = json.loads(r.output)
    assert [t["pid_file"] for t in data["trees"]] == [
        "C:/u/.navig/daemon/supervisor.pid",
        "C:/u/.navig/agent/agent.pid",
    ]


def test_the_table_names_every_pid_and_the_nudge(monkeypatch):
    _fake_trees(monkeypatch, _TREES)

    r = runner.invoke(service_app, ["pids"], env={"COLUMNS": "160"})

    assert r.exit_code == 0, r.output
    for pid in ("100", "101", "200"):
        assert pid in r.output
    assert "supervisor" in r.output and "agent" in r.output
    assert "--plain" in r.output


def test_nothing_running_says_so_and_exits_zero(monkeypatch):
    _fake_trees(monkeypatch, [])

    r = runner.invoke(service_app, ["pids"])

    assert r.exit_code == 0 and "nothing to spare" in r.output
    assert runner.invoke(service_app, ["pids", "--plain"]).output.strip() == ""
