"""The script runner, driven with REAL process trees.

The probe is Python on purpose: a node→node tree passes a tree-kill test
vacuously on Windows (libuv puts the child in a Job Object, so it dies with its
parent anyway). Python's subprocess uses no Job Object — the property that makes
a real tree orphan — so a Python parent→child→grandchild is the honest probe.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

psutil = pytest.importorskip("psutil")

from navig.projects import runner as runner_mod  # noqa: E402
from navig.projects.runner import ProjectRunner, RunError, derive_argv  # noqa: E402

PARENT = textwrap.dedent("""
    import subprocess, sys, time, pathlib
    out = pathlib.Path(sys.argv[1])
    code = int(sys.argv[2])
    if code < 0:
        # parent -> child -> grandchild, all sleeping; record every pid
        child = subprocess.Popen([sys.executable, "-c",
            "import subprocess,sys,time,pathlib;"
            "g=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']);"
            "pathlib.Path(sys.argv[1]).write_text(str(g.pid));"
            "time.sleep(120)", str(out) + ".grand"])
        out.write_text(str(child.pid))
        print("server listening", flush=True)
        time.sleep(120)
    print(f"done with {code}", flush=True)
    sys.exit(code)
""")


@pytest.fixture()
def run_env(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    script = tmp_path / "parent.py"
    script.write_text(PARENT, encoding="utf-8")
    marker = tmp_path / "pids.txt"

    def fake_argv(root: Path, s: dict) -> tuple[list[str], Path]:
        code = {"npm:.:dev": "-1", "npm:.:build": "0"}.get(s["key"], "3")
        return [sys.executable, str(script), str(marker), code], root

    monkeypatch.setattr(runner_mod, "derive_argv", fake_argv)
    r = ProjectRunner(base=tmp_path / "runs")
    yield r, project, marker
    for v in r.list_runs(include_finished=False):  # never leave a tree behind
        try:
            r.stop(v["id"])
        except RunError:
            pass


def _wait(pred, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.1)
    return False


def _alive(pid: int) -> bool:
    try:
        p = psutil.Process(pid)
        return p.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def test_stop_kills_the_whole_tree(run_env) -> None:
    r, root, marker = run_env
    run = r.start("shop", root, "npm:.:dev")
    assert run["state"] in ("starting", "running")
    grand = Path(str(marker) + ".grand")
    assert _wait(lambda: marker.exists() and grand.exists()), "the tree never came up"
    assert _wait(lambda: "server listening" in r.tail(run["id"])["text"])

    status = runner_mod._read_json(r._status_path(run["id"]))
    pids = [status["shim_pid"], status["child_pid"], int(marker.read_text()), int(grand.read_text())]
    assert all(_alive(p) for p in pids)

    view = r.stop(run["id"])
    assert view["state"] == "stopped"
    assert _wait(lambda: not any(_alive(p) for p in pids), 10), [p for p in pids if _alive(p)]


@pytest.mark.parametrize(("key", "state", "code"), [("npm:.:build", "exited", 0), ("npm:.:clean", "failed", 3)])
def test_exit_codes_are_recorded(run_env, key: str, state: str, code: int) -> None:
    r, root, _marker = run_env
    run = r.start("shop", root, key)
    assert _wait(lambda: r.get(run["id"])["state"] == state), r.get(run["id"])
    view = r.get(run["id"])
    assert view["exit_code"] == code
    assert f"done with {code}" in r.tail(run["id"])["text"]
    r.list_runs()  # finalises history lazily

    from navig.projects.store import get_project_store

    hist = get_project_store().list_runs(root)
    assert any(h["id"] == run["id"] and h["exit_code"] == code and h["outcome"] == state for h in hist)


def test_one_instance_per_script(run_env) -> None:
    r, root, marker = run_env
    first = r.start("shop", root, "npm:.:dev")
    assert _wait(lambda: marker.exists())
    again = r.start("shop", root, "npm:.:dev")
    assert again["id"] == first["id"] and again["already_running"] is True


def test_unknown_script_is_404(run_env) -> None:
    r, root, _marker = run_env
    with pytest.raises(RunError) as exc:
        r.start("shop", root, "npm:.:nope")
    assert exc.value.status == 404


def test_a_recycled_pid_is_never_adopted(run_env) -> None:
    """A status file naming a LIVE pid with the wrong create_time is a stale claim."""
    r, root, _marker = run_env
    import json
    import os

    spec = {"id": "stale-1", "root": str(root), "script_key": "npm:.:dev", "name": "dev",
            "created_at": "2026-01-01T00:00:00+00:00", "created_ts": 0, "shim_create_time": 1.0,
            "log_path": "", "status_path": str(r._status_path("stale-1"))}
    r.base.mkdir(parents=True, exist_ok=True)
    r._spec_path("stale-1").write_text(json.dumps(spec), encoding="utf-8")
    r._status_path("stale-1").write_text(json.dumps({"shim_pid": os.getpid(), "state": "running"}),
                                         encoding="utf-8")
    view = r.get("stale-1")
    assert view["state"] == "lost"
    # …and stopping it must not touch the live process that now owns the pid.
    r.stop("stale-1")
    assert _alive(os.getpid())


def test_a_run_survives_its_launcher_being_tree_killed(run_env, tmp_path: Path) -> None:
    """`navig service restart` tree-kills the daemon; a dev server must not die with it."""
    r, root, marker = run_env
    launcher = tmp_path / "launcher.py"
    launcher.write_text(textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(Path(runner_mod.__file__).parents[2])!r})
        from pathlib import Path
        import navig.projects.runner as rm
        rm.derive_argv = lambda root, s: ([sys.executable, {str(tmp_path / 'parent.py')!r},
                                          {str(marker)!r}, "-1"], root)
        run = rm.ProjectRunner(base=Path({str(r.base)!r})).start("shop", Path({str(root)!r}), "npm:.:dev")
        print(run["id"], flush=True)
        time.sleep(120)
    """), encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(launcher)], stdout=subprocess.PIPE, text=True)
    try:
        run_id = proc.stdout.readline().strip()
        assert run_id, "the launcher never started a run"
        assert _wait(lambda: marker.exists())
        # Kill the launcher's whole tree, the way a daemon restart does.
        launcher_proc = psutil.Process(proc.pid)
        for p in [launcher_proc, *launcher_proc.children(recursive=True)]:
            try:
                p.kill()
            except psutil.Error:
                pass
        proc.wait(timeout=10)
        time.sleep(0.5)
        assert r.get(run_id)["state"] == "running"
        assert _alive(int(marker.read_text()))
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.parametrize("name", ["dev&calc", "dev && calc", "a|b", "x\"y", "", "-rf"])
def test_unsafe_script_names_never_reach_argv(project: Path, name: str) -> None:
    with pytest.raises(RunError):
        derive_argv(project, {"name": name, "kind": "npm", "runner": "npm", "cwd": ""})


def test_argv_is_derived_from_the_manifest_not_the_client(project: Path) -> None:
    import shutil

    if not shutil.which("pnpm") and not shutil.which("npm"):
        pytest.skip("no node package manager on PATH")
    argv, cwd = derive_argv(project, {"name": "dev", "kind": "npm", "runner": "npm", "cwd": "apps/web"})
    assert argv[-2:] == ["run", "dev"] and cwd == (project / "apps" / "web").resolve()
    with pytest.raises(RunError):
        derive_argv(project, {"name": "dev", "kind": "npm", "runner": "npm", "cwd": "../.."})
