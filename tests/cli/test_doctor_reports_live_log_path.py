"""`navig doctor` must say where the daemon is ACTUALLY logging.

Born from an expensive misdiagnosis. `navig.log` lives in `config_dir()`, and
`config_dir()` becomes the PROJECT `.navig/` whenever the process's cwd is inside
a navig project -- so the file follows whatever directory the daemon was launched
from. Seven of them existed on one machine. The one an operator naturally opens,
`~/.navig/navig.log`, showed EIGHT DAYS with zero lines and read exactly like a
dead or log-blind daemon, while the daemon was running fine and logging into the
desktop app's workspace -- and a cron job ran an hour after that file's last entry.

So this row does not print where the log SHOULD be. It asks the running process.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from navig.commands import doctor


class _Handle:
    def __init__(self, path: str) -> None:
        self.path = path


class _Proc:
    """A process with open files and children, shaped like psutil's."""

    def __init__(self, files: list[str], kids: list[_Proc] | None = None, cwd: str = "C:/somewhere"):
        self._files = files
        self._kids = kids or []
        self._cwd = cwd

    def open_files(self):
        return [_Handle(p) for p in self._files]

    def children(self, recursive: bool = False):
        return self._kids

    def cwd(self):
        return self._cwd


def _wire(monkeypatch, proc, *, running=True, pid=4242, config_dir: Path | None = None):
    import navig.daemon.supervisor as sup
    from navig.platform import paths

    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: running))
    monkeypatch.setattr(sup.NavigDaemon, "read_pid", staticmethod(lambda: pid))
    monkeypatch.setattr(doctor, "_check", doctor._check)  # keep the real formatter
    fake_psutil = SimpleNamespace(Process=lambda _pid: proc)
    monkeypatch.setitem(__import__("sys").modules, "psutil", fake_psutil)
    if config_dir is not None:
        monkeypatch.setattr(paths, "config_dir", lambda: config_dir)


def _row(results, label):
    """CheckResult is a (icon, ok, line) tuple carrying .label/.detail."""
    return next(r for r in results if r.label == label)


def test_it_finds_the_log_held_by_a_CHILD_not_the_supervisor(monkeypatch, tmp_path) -> None:
    """The supervisor does not open navig.log -- its gateway child does.

    Measured: supervisor 113012 held daemon.log/gateway.log while gateway 79708 held
    the navig.log actually being written. Asking only the supervisor finds nothing and
    invites a guess.
    """
    child = _Proc([str(tmp_path / "navig.log")])
    parent = _Proc(["C:/x/daemon.log", "C:/x/gateway.log"], kids=[child])
    _wire(monkeypatch, parent, config_dir=tmp_path)

    row = _row(doctor.check_logs(), "daemon log path")
    assert row[1] is True
    assert "navig.log" in row.detail


def test_a_log_somewhere_else_is_NAMED_and_flagged(monkeypatch, tmp_path) -> None:
    """The whole point: the operator must be told the path is not the default one."""
    elsewhere = "C:/Users/x/.navig-os/workspaces/my-workspace/.navig/navig.log"
    _wire(monkeypatch, _Proc([elsewhere]), config_dir=tmp_path)

    row = _row(doctor.check_logs(), "daemon log path")
    assert row[1] is False, "a log in an unexpected place must not read as a plain tick"
    assert elsewhere in row.detail, "the row must NAME the real path, not just complain"
    assert str(tmp_path / "navig.log") in row.detail, "it must also say what was expected"


def test_it_NEVER_invents_a_path(monkeypatch, tmp_path) -> None:
    """The anti-guess floor.

    The first cut derived a path from the process cwd and produced
    `~/.navig/.navig/navig.log` -- a path nothing writes. Printing an invented
    location is the very defect this row exists to prevent, so when no navig.log is
    open the row must say so and offer the cwd as context, not a fabricated filename.
    """
    _wire(monkeypatch, _Proc(["C:/x/daemon.log"], cwd="C:/Users/x/.navig"), config_dir=tmp_path)

    row = _row(doctor.check_logs(), "daemon log path")
    assert row[1] is False
    assert "no navig.log is open" in row.detail
    doubled = ".navig" + chr(92) + ".navig"   # chr(92) so no escape sequence here
    assert doubled not in row.detail, "invented a doubled path"
    assert "C:/Users/x/.navig" in row.detail, "the cwd is the honest thing to offer"


def test_a_stopped_daemon_is_not_a_green_tick(monkeypatch, tmp_path) -> None:
    """Could-not-look is never healthy -- the doctor rule."""
    _wire(monkeypatch, _Proc([]), running=False, config_dir=tmp_path)

    row = _row(doctor.check_logs(), "daemon log path")
    assert row[1] is False
    assert "not running" in row.detail


def test_missing_psutil_degrades_instead_of_crashing(monkeypatch, tmp_path) -> None:
    """doctor must survive an environment without the optional dependency."""
    import builtins

    _wire(monkeypatch, _Proc([]), config_dir=tmp_path)
    real_import = builtins.__import__

    def _no_psutil(name, *a, **k):
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_psutil)
    row = _row(doctor.check_logs(), "daemon log path")
    assert row[1] is False
    assert "psutil" in row.detail


def test_the_check_is_wired_into_the_report() -> None:
    """A check nobody calls is documentation. Pin the registration."""
    import inspect

    source = inspect.getsource(doctor)
    assert "check_cache_dir() + check_logs()" in source, (
        "check_logs() is not in the Filesystem section -- it would never run"
    )
