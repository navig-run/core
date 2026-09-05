"""`stop_launched` must never terminate a process it cannot attribute to its own launch.

`navig cdp status` prints a promise: "NAVIG never touches a browser it did not launch."
`_debug_browser_pids` kept that promise only when a `--user-data-dir` was recorded — and
`default_debug_profile_dir` is applied ONLY for `BROWSER_APPS` (chrome/edge/brave). An Electron
app (discord/notion/slack/vscode) keeps its own profile, so it is recorded with
`user_data_dir=None`, and matching then fell back to the port alone: ANY process serving that
port was selected, and `stop_launched` terminates whatever the function returns.

That is a live hazard, not a hypothetical — a debug browser belonging to another tool sitting on
the same port would have been killed by `navig cdp stop`. Attribution now requires a second
signal: our recorded profile dir, or the executable we launched.
"""

from __future__ import annotations

import time

import pytest

from navig.browser import targets as t


class _Proc:
    def __init__(self, pid: int, cmdline: list[str], created: float | None = None):
        self.info = {"pid": pid, "cmdline": cmdline}
        self.pid = pid
        # Default: created just before "now", i.e. before whatever record_launched() stamps —
        # which is what a genuine, non-recycled process looks like.
        self._created = created if created is not None else time.time() - 5

    # The stub has to mirror the parts of psutil.Process the code actually calls, or the PID
    # identity guard silently takes its "cannot identify" branch and the test proves nothing.
    def create_time(self) -> float:
        return self._created

    def name(self) -> str:
        return (self.info["cmdline"][0] or "").rsplit("\\", 1)[-1].rsplit("/", 1)[-1]

    def cmdline(self) -> list[str]:
        return list(self.info["cmdline"])


def _fake_psutil(monkeypatch, procs: list[_Proc]):
    """Install a psutil stub so the sweep sees exactly *procs* and never the real machine."""
    import sys
    import types

    by_pid = {p.pid: p for p in procs}

    class _NoSuchProcess(Exception):
        pass

    def _process(pid):
        if pid not in by_pid:
            raise _NoSuchProcess(pid)
        return by_pid[pid]

    stub = types.ModuleType("psutil")
    stub.process_iter = lambda _attrs=None: list(procs)  # type: ignore[attr-defined]
    stub.Process = _process  # type: ignore[attr-defined]
    stub.NoSuchProcess = _NoSuchProcess  # type: ignore[attr-defined]
    stub.Error = Exception  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psutil", stub)


_OURS_ELECTRON = _Proc(101, [r"C:\Apps\Discord\Discord.exe", "--remote-debugging-port=9333"])
_FOREIGN = _Proc(
    202,
    [
        r"C:\Program Files\Chrome\chrome.exe",
        "--remote-debugging-port=9333",
        r"--user-data-dir=C:\Users\someone\.gemini\antigravity-browser-profile",
    ],
)


def test_electron_launch_does_not_select_a_foreign_browser(monkeypatch) -> None:
    """The regression: with user_data_dir=None both processes used to match on port alone."""
    _fake_psutil(monkeypatch, [_OURS_ELECTRON, _FOREIGN])
    pids = t._debug_browser_pids(9333, None, exe_hint=r"C:\Apps\Discord\Discord.exe")
    assert pids == [101]


def test_short_app_id_matches_the_real_executable(monkeypatch) -> None:
    """The registry stores a short id ("discord") for a non-absolute app."""
    _fake_psutil(monkeypatch, [_OURS_ELECTRON, _FOREIGN])
    assert t._debug_browser_pids(9333, None, exe_hint="discord") == [101]


def test_unattributable_sweep_returns_nothing(monkeypatch) -> None:
    """No profile dir AND no exe hint → refuse to guess. A leaked browser we report honestly
    is cheaper than killing the operator's session."""
    _fake_psutil(monkeypatch, [_OURS_ELECTRON, _FOREIGN])
    assert t._debug_browser_pids(9333, None) == []


def test_user_data_dir_still_scopes_when_present(monkeypatch) -> None:
    """Unchanged behaviour for chrome/edge/brave, which do get a navig profile dir."""
    ours = _Proc(
        303,
        [
            r"C:\Chrome\chrome.exe",
            "--remote-debugging-port=9333",
            r"--user-data-dir=C:\Users\me\.navig\cdp-profiles\chrome",
        ],
    )
    _fake_psutil(monkeypatch, [ours, _FOREIGN])
    pids = t._debug_browser_pids(9333, r"C:\Users\me\.navig\cdp-profiles\chrome")
    assert pids == [303]


def test_renderer_children_are_never_returned(monkeypatch) -> None:
    child = _Proc(
        404, [r"C:\Apps\Discord\Discord.exe", "--remote-debugging-port=9333", "--type=renderer"]
    )
    _fake_psutil(monkeypatch, [_OURS_ELECTRON, child])
    assert t._debug_browser_pids(9333, None, exe_hint="discord") == [101]


@pytest.mark.parametrize(
    "argv0, hint, expected",
    [
        (r"C:\Apps\Discord\Discord.exe", "discord", True),
        (r"C:\Apps\Discord\Discord.exe", r"C:\Other\Discord.exe", True),  # basename match
        ("/usr/bin/discord", "discord", True),
        (r"C:\Chrome\chrome.exe", "discord", False),
        ("", "discord", False),
        (r"C:\Apps\Discord\Discord.exe", "", False),
    ],
)
def test_exe_matching_rules(argv0: str, hint: str, expected: bool) -> None:
    assert t._exe_matches(argv0, hint) is expected


def test_stop_launched_only_kills_attributable_processes(monkeypatch, tmp_path) -> None:
    """End-to-end through stop_launched: the foreign browser must not be terminated."""
    monkeypatch.setattr(t, "_launched_registry_path", lambda: tmp_path / "launched.json")
    killed: list[int] = []
    monkeypatch.setattr(t, "_terminate_pid", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(t, "probe_port", lambda *a, **k: None)  # "closed" — no real network
    _fake_psutil(monkeypatch, [_OURS_ELECTRON, _FOREIGN])

    t.record_launched(9333, 101, "discord", None)  # Electron: no user_data_dir
    res = t.stop_launched(9333)

    assert res["ok"] is True
    assert 202 not in killed, "terminated a browser NAVIG did not launch"
    assert killed.count(101) >= 1


# ── the tracked PID is the OTHER door into the same promise ──────────────────


def _entry(pid: int, *, started: float, app: str = "chrome", udd: str | None = None) -> dict:
    return {"pid": pid, "app": app, "user_data_dir": udd, "started": started}


def test_a_recycled_pid_is_never_killed(monkeypatch) -> None:
    """The core hazard: the tracked PID is usually a dead launcher, and dead PIDs get reused.

    `cdp-launched.json` lives in the config dir and outlives reboots, so by the time anyone runs
    `navig cdp stop` the recorded number may belong to something else entirely — which
    `_terminate_pid` would kill along with its whole process tree.
    """
    now = time.time()
    # An unrelated process that inherited the number LONG after we recorded it.
    squatter = _Proc(101, [r"C:\Python\python.exe", "-c", "work()"], created=now)
    _fake_psutil(monkeypatch, [squatter])

    assert t._pid_is_still_the_recorded_process(101, _entry(101, started=now - 3600)) is False


def test_the_genuine_recorded_pid_is_still_killed(monkeypatch) -> None:
    """Anti-vacuity: a guard that never lets go would silently break `navig cdp stop`."""
    now = time.time()
    ours = _Proc(101, [r"C:\Apps\Chrome\chrome.exe", "--remote-debugging-port=9333"], created=now - 5)
    _fake_psutil(monkeypatch, [ours])

    assert t._pid_is_still_the_recorded_process(101, _entry(101, started=now)) is True


def test_a_pid_reused_by_another_browser_is_still_refused(monkeypatch) -> None:
    """Matching the executable is not enough — the operator's own Chrome would match too."""
    now = time.time()
    someone_elses_chrome = _Proc(
        101, [r"C:\Program Files\Chrome\chrome.exe", "--remote-debugging-port=9999"], created=now
    )
    _fake_psutil(monkeypatch, [someone_elses_chrome])

    assert t._pid_is_still_the_recorded_process(101, _entry(101, started=now - 3600)) is False


def test_a_process_that_no_longer_looks_like_ours_is_refused(monkeypatch) -> None:
    """Second signal: even within the time window it must resemble what we launched."""
    now = time.time()
    unrelated = _Proc(101, [r"C:\Windows\notepad.exe"], created=now - 5)
    _fake_psutil(monkeypatch, [unrelated])

    assert t._pid_is_still_the_recorded_process(101, _entry(101, started=now)) is False


def test_a_registry_entry_without_a_timestamp_is_refused(monkeypatch) -> None:
    """Legacy entries cannot prove identity, and unprovable means do not kill."""
    now = time.time()
    ours = _Proc(101, [r"C:\Apps\Chrome\chrome.exe", "--remote-debugging-port=9333"], created=now - 5)
    _fake_psutil(monkeypatch, [ours])

    entry = _entry(101, started=now)
    del entry["started"]
    assert t._pid_is_still_the_recorded_process(101, entry) is False


def test_a_vanished_pid_is_refused(monkeypatch) -> None:
    """Nothing to kill — and refusing keeps the 'cannot identify' branch honest."""
    _fake_psutil(monkeypatch, [])
    assert t._pid_is_still_the_recorded_process(999, _entry(999, started=time.time())) is False


def test_stop_launched_does_not_kill_a_recycled_tracked_pid(monkeypatch, tmp_path) -> None:
    """End-to-end through the command path, which is where the damage would happen."""
    monkeypatch.setattr(t, "_launched_registry_path", lambda: tmp_path / "launched.json")
    killed: list[int] = []
    monkeypatch.setattr(t, "_terminate_pid", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(t, "probe_port", lambda *a, **k: None)  # "closed" — no real network

    now = time.time()
    _fake_psutil(monkeypatch, [_Proc(101, [r"C:\Python\python.exe", "-c", "work()"], created=now)])

    t.record_launched(9333, 101, "chrome", None)
    data = t._read_launched()
    data["9333"]["started"] = now - 3600  # recorded long before that process existed
    t._write_launched(data)

    t.stop_launched(9333)

    assert 101 not in killed, "killed a process that merely inherited the recorded PID"


# ── _terminate_pid must report GONE, not "we tried" ──────────────────────────


def test_terminate_pid_reports_gone_for_a_real_process() -> None:
    """`stop_all_launched` prints this return as `orphans_reclaimed` — it must be a fact."""
    import subprocess
    import sys

    pytest.importorskip("psutil")
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(45)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert t._terminate_pid(proc.pid) is True
        assert proc.poll() is not None, "reported gone while still running"
    finally:
        try:
            proc.kill()
        except OSError:
            pass


def test_terminate_pid_kills_the_children_too() -> None:
    """A browser is never one process — a bare parent kill leaves renderers behind."""
    import subprocess
    import sys
    import time as _time

    psutil = pytest.importorskip("psutil")
    parent_code = (
        "import subprocess, sys, time; "
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(45)']); "
        "print(g.pid, flush=True); "
        "time.sleep(45)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    gpid = None
    try:
        gpid = int(proc.stdout.readline().strip())
        assert psutil.pid_exists(gpid), "child should be alive before the sweep"

        assert t._terminate_pid(proc.pid) is True

        for _ in range(50):
            try:
                if not psutil.Process(gpid).is_running():
                    break
            except psutil.Error:
                break
            _time.sleep(0.1)
        try:
            still_up = psutil.Process(gpid).is_running()
        except psutil.Error:
            still_up = False
        assert not still_up, "child outlived the parent's termination"
    finally:
        for pid in (gpid, proc.pid):
            if pid is not None:
                try:
                    psutil.Process(pid).kill()
                except psutil.Error:
                    pass


def test_terminate_pid_reports_true_for_a_pid_that_is_already_gone() -> None:
    """Already gone is the good case — it must not read as a failure."""
    import subprocess
    import sys

    pytest.importorskip("psutil")
    proc = subprocess.Popen([sys.executable, "-c", "pass"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc.wait(timeout=30)
    assert t._terminate_pid(proc.pid) is True


def _psutil_stub(monkeypatch, proc_obj, *, no_such: bool = False):
    """A psutil whose Process(pid) is exactly *proc_obj* — for failure modes a real process
    cannot be made to exhibit on demand (access denied, surviving SIGKILL)."""
    import sys
    import types

    class _NoSuchProcess(Exception):
        pass

    stub = types.ModuleType("psutil")
    stub.NoSuchProcess = _NoSuchProcess  # type: ignore[attr-defined]
    stub.Error = Exception  # type: ignore[attr-defined]

    def _process(_pid):
        if no_such:
            raise _NoSuchProcess(_pid)
        return proc_obj

    stub.Process = _process  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psutil", stub)
    return stub


class _Unkillable:
    """Ignores every signal — stands in for a process we lack the rights to stop."""

    def children(self, recursive=False):
        return []

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        raise TimeoutError("still running")

    def is_running(self):
        return True


class _Unreadable:
    """psutil can see the PID but not inspect it (AccessDenied on a foreign-user process)."""

    def children(self, recursive=False):
        raise PermissionError("access denied")


def test_terminate_pid_does_not_claim_success_over_a_surviving_process(monkeypatch) -> None:
    """The old code returned True straight after kill(), verifying nothing."""
    _psutil_stub(monkeypatch, _Unkillable())
    assert t._terminate_pid(1234) is False


def test_terminate_pid_does_not_read_access_denied_as_already_gone(monkeypatch) -> None:
    """The catch-all handler treated ANY inspection failure as "already gone" -> True."""
    _psutil_stub(monkeypatch, _Unreadable())
    assert t._terminate_pid(1234) is False


def test_terminate_pid_still_reports_true_when_the_process_really_vanished(monkeypatch) -> None:
    """Anti-vacuity for the two above: NoSuchProcess must stay a success."""
    _psutil_stub(monkeypatch, None, no_such=True)
    assert t._terminate_pid(1234) is True
