"""`navig doctor` → Daemon freshness: flag a running daemon that is executing stale code.

Every case is mocked — the real daemon, real git state and real version are never touched.
"""

from __future__ import annotations

from navig.commands import doctor


def _patch_daemon(monkeypatch, *, running=True, state=None):
    import navig.daemon.supervisor as sup

    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: running), raising=False)
    monkeypatch.setattr(sup.NavigDaemon, "read_state", staticmethod(lambda: state), raising=False)


# --------------------------------------------------------------------------- stopped


def test_silent_when_daemon_stopped(monkeypatch):
    _patch_daemon(monkeypatch, running=False)
    assert doctor.check_daemon_freshness() == []


# --------------------------------------------------------------------------- git checkout


def test_green_when_boot_commit_matches_disk(monkeypatch):
    _patch_daemon(monkeypatch, running=True, state={"boot_code": {"commit": "f" * 40, "branch": "main"}})
    monkeypatch.setattr(doctor, "_git_head", lambda _src: ("f" * 40, "fffffff"))
    monkeypatch.setattr(doctor, "_git_count", lambda _src, _rng: None)
    rows = doctor.check_daemon_freshness()
    assert len(rows) == 1
    icon, ok, text = rows[0]
    assert ok is True and icon == doctor._OK
    assert "running current commit" in text and "main" in text


def test_warns_when_daemon_is_stale(monkeypatch):
    _patch_daemon(monkeypatch, running=True, state={"boot_code": {"commit": "a" * 40, "branch": "main"}})
    monkeypatch.setattr(doctor, "_git_head", lambda _src: ("b" * 40, "bbbbbbb"))
    monkeypatch.setattr(doctor, "_git_count", lambda _src, _rng: 3)
    rows = doctor.check_daemon_freshness()
    assert len(rows) == 1
    icon, ok, text = rows[0]
    assert ok is False and icon != doctor._OK, "a stale daemon must never render ✓"
    assert "STALE" in text and "3 commit(s) behind" in text
    assert "restart" in text.lower()


def test_warns_when_boot_commit_missing_old_daemon(monkeypatch):
    _patch_daemon(monkeypatch, running=True, state={"boot_code": {}})
    monkeypatch.setattr(doctor, "_git_head", lambda _src: ("b" * 40, "bbbbbbb"))
    rows = doctor.check_daemon_freshness()
    assert len(rows) == 1
    _icon, ok, text = rows[0]
    assert ok is False and "predates freshness" in text


def test_green_notes_when_disk_is_behind_upstream(monkeypatch):
    _patch_daemon(monkeypatch, running=True, state={"boot_code": {"commit": "f" * 40, "branch": "main"}})
    monkeypatch.setattr(doctor, "_git_head", lambda _src: ("f" * 40, "fffffff"))
    monkeypatch.setattr(doctor, "_git_count", lambda _src, _rng: 5)  # 5 behind upstream
    rows = doctor.check_daemon_freshness()
    _icon, ok, text = rows[0]
    assert ok is True  # the running code == disk, so the daemon itself is fresh …
    assert "5 behind upstream" in text  # … but doctor still nudges to pull


# --------------------------------------------------------------------------- wheel install


def test_pip_install_version_mismatch_is_stale(monkeypatch):
    _patch_daemon(monkeypatch, running=True, state={"boot_code": {"install": "pip", "version": "3.20.0"}})
    monkeypatch.setattr(doctor, "_git_head", lambda _src: None)  # not a git checkout
    import navig as _nav

    monkeypatch.setattr(_nav, "__version__", "3.24.0", raising=False)
    rows = doctor.check_daemon_freshness()
    assert len(rows) == 1
    _icon, ok, text = rows[0]
    assert ok is False and "3.20.0" in text and "3.24.0" in text


def test_pip_install_version_current_is_green(monkeypatch):
    _patch_daemon(monkeypatch, running=True, state={"boot_code": {"install": "pip", "version": "3.24.0"}})
    monkeypatch.setattr(doctor, "_git_head", lambda _src: None)
    import navig as _nav

    monkeypatch.setattr(_nav, "__version__", "3.24.0", raising=False)
    rows = doctor.check_daemon_freshness()
    assert rows and rows[0][1] is True and "3.24.0" in rows[0][2]


def test_silent_when_nothing_determinable(monkeypatch):
    """No git, no version info → say nothing rather than cry wolf."""
    _patch_daemon(monkeypatch, running=True, state={"boot_code": {}})
    monkeypatch.setattr(doctor, "_git_head", lambda _src: None)
    import navig as _nav

    monkeypatch.setattr(_nav, "__version__", "", raising=False)
    assert doctor.check_daemon_freshness() == []
