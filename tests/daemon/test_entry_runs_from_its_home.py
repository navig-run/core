"""The daemon binds to its own home, whichever way it was launched.

The scheduled task runs `navig.daemon.entry` with WorkingDirectory = config_dir().
`navig gateway restart` runs the same module from wherever the operator's shell
is. `ConfigManager.base_dir` follows the cwd into a project `.navig/`, and
navig.log is attached under base_dir — so the two launch paths produced daemons
that logged to different files (2026-09-14: a restart from inside the repo sent
the log to `<repo>/.navig/navig.log` while `~/.navig/navig.log` went silent, and
an absence in the silent file was nearly read as proof of a fix).

One chdir at the entry makes every launch path yield the task's process shape.
It is deliberately NOT the chdir main.py forbids — that rule is about the active
SPACE, which a long-lived daemon must not bind to; config_dir() is not a space.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import navig.daemon.entry as entry


def _stop_before_the_daemon(monkeypatch):
    """Let main() run its preamble, then stop it at the duplicate guard so no
    real supervisor is ever constructed (a hanging test reports nothing)."""
    import navig.daemon.supervisor as supervisor

    monkeypatch.setattr(supervisor.NavigDaemon, "is_running", staticmethod(lambda: True))
    monkeypatch.setattr(supervisor.NavigDaemon, "read_pid", staticmethod(lambda: 1))
    monkeypatch.setattr(
        supervisor.NavigDaemon, "_verify_daemon_pid", staticmethod(lambda pid: True)
    )


def test_the_entry_chdirs_to_the_config_dir_before_the_guard(tmp_path, monkeypatch):
    """The exact trap: launched from inside a project, the process must end up in
    its home, not the project — or base_dir, and with it the log, follows the shell."""
    home = tmp_path / "navig-home"
    project = tmp_path / "some-project"
    (project / ".navig").mkdir(parents=True)
    home.mkdir()
    monkeypatch.setattr("navig.platform.paths.config_dir", lambda: home)
    _stop_before_the_daemon(monkeypatch)

    monkeypatch.chdir(project)
    assert Path.cwd().resolve() == project.resolve()

    entry.main()

    assert Path.cwd().resolve() == home.resolve(), (
        f"the daemon is still standing in {Path.cwd()} — its log would land in "
        f"{Path.cwd() / '.navig' / 'navig.log'} instead of {home / 'navig.log'}"
    )


def test_an_unresolvable_home_does_not_block_the_boot(tmp_path, monkeypatch):
    """Best-effort: a home that cannot be entered must not turn into a daemon
    that never starts. The guard must still be reached."""

    def _boom():
        raise RuntimeError("no home")

    monkeypatch.setattr("navig.platform.paths.config_dir", _boom)
    reached: list[str] = []
    import navig.daemon.supervisor as supervisor

    def _guard():
        reached.append("guard")
        return True

    monkeypatch.setattr(supervisor.NavigDaemon, "is_running", staticmethod(_guard))
    monkeypatch.setattr(supervisor.NavigDaemon, "read_pid", staticmethod(lambda: 1))
    monkeypatch.setattr(
        supervisor.NavigDaemon, "_verify_daemon_pid", staticmethod(lambda pid: True)
    )
    monkeypatch.chdir(tmp_path)

    entry.main()  # must not raise

    assert reached == ["guard"]
    assert Path.cwd().resolve() == tmp_path.resolve()  # unchanged, not somewhere random


def test_it_is_not_the_active_space_chdir(tmp_path, monkeypatch):
    """The rule main.py enforces still holds: the daemon binds to its HOME, never
    to the active space — even when one is set."""
    home = tmp_path / "home"
    space = tmp_path / "active-space"
    home.mkdir()
    (space / ".navig").mkdir(parents=True)
    monkeypatch.setattr("navig.platform.paths.config_dir", lambda: home)
    monkeypatch.setattr("navig.spaces.active.get_active_working_dir", lambda: space, raising=False)
    _stop_before_the_daemon(monkeypatch)
    monkeypatch.chdir(tmp_path)

    entry.main()

    assert Path.cwd().resolve() == home.resolve()
    assert Path.cwd().resolve() != space.resolve()


def test_children_inherit_the_home(monkeypatch, tmp_path):
    """The chdir only pays off if the gateway and the worker end up there too.
    Both default children are registered with cwd=None, i.e. inherited."""
    import navig.daemon.supervisor as supervisor

    d = supervisor.NavigDaemon.__new__(supervisor.NavigDaemon)
    d.children = []
    d.logger = __import__("logging").getLogger("test")
    d.add_child = lambda c: d.children.append(c)
    # The public registration helpers for the two default children.
    d.add_telegram_bot()
    d.add_gateway(port=8789)

    assert {c.name for c in d.children} == {"telegram-bot", "gateway"}
    for c in d.children:
        if c.name in ("telegram-bot", "gateway"):
            assert c.cwd is None, (
                f"{c.name} pins its own cwd={c.cwd}; it would not inherit the home"
            )


def test_a_relative_config_home_is_resolved_before_the_chdir(tmp_path, monkeypatch):
    """`NAVIG_CONFIG_DIR=.navig` names `<launch>/.navig`. Left relative across the
    chdir, every later `config_dir()` answered `<launch>/.navig/.navig` — the stop and
    duplicate guards then read the wrong pid file and could boot a second daemon."""
    launch = tmp_path / "launch"
    (launch / ".navig").mkdir(parents=True)
    monkeypatch.chdir(launch)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", ".navig")
    _stop_before_the_daemon(monkeypatch)

    entry.main()

    from navig.platform import paths

    expected = (launch / ".navig").resolve()
    assert Path(paths.config_dir()).resolve() == expected, (
        f"config_dir() drifted to {paths.config_dir()!s} after the chdir"
    )
    assert Path.cwd().resolve() == expected


def test_the_launch_directory_env_file_is_still_loaded(tmp_path, monkeypatch):
    """The chdir to the home happens before `.env` discovery, so `Path.cwd()/.env`
    became the home's `.env` and a project `.env` carrying TELEGRAM_BOT_TOKEN was never
    read — a restart came up without the credentials it had before."""
    pytest.importorskip("dotenv")
    import navig.daemon.supervisor as supervisor

    home = tmp_path / "home"
    launch = tmp_path / "project"
    home.mkdir()
    launch.mkdir()
    (launch / ".env").write_text("NAVIG_TEST_LAUNCH_ENV=from-launch\n", encoding="utf-8")
    monkeypatch.setenv("NAVIG_TEST_LAUNCH_ENV", "placeholder")  # restored on teardown
    monkeypatch.delenv("NAVIG_TEST_LAUNCH_ENV")
    monkeypatch.setattr("navig.platform.paths.config_dir", lambda: home)
    monkeypatch.setattr(supervisor.NavigDaemon, "is_running", staticmethod(lambda: False))
    monkeypatch.setattr(entry, "_load_config", lambda: {"telegram_bot": False})

    class _FakeDaemon:
        def __init__(self, *a, **k):
            pass

        def run(self):
            pass

    monkeypatch.setattr(supervisor, "NavigDaemon", _FakeDaemon)
    monkeypatch.chdir(launch)

    entry.main()

    assert os.environ.get("NAVIG_TEST_LAUNCH_ENV") == "from-launch"


def test_a_config_manager_cached_in_the_project_is_rebound_to_the_home(tmp_path, monkeypatch):
    """`navig service start --foreground` runs main() inside the CLI process, whose
    callback already cached a ConfigManager with base_dir = the project `.navig/`. The
    chdir alone left that manager — and the log under its base_dir — in the project."""
    from navig.config import get_config_manager, reset_config_manager

    home = tmp_path / "navig-home"
    project = tmp_path / "some-project"
    (project / ".navig").mkdir(parents=True)
    home.mkdir()
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(home))
    _stop_before_the_daemon(monkeypatch)
    monkeypatch.chdir(project)
    reset_config_manager()
    try:
        assert Path(get_config_manager().base_dir).resolve() == (project / ".navig").resolve()

        entry.main()

        assert Path(get_config_manager().base_dir).resolve() == home.resolve(), (
            "the cached ConfigManager still points at the project after the chdir"
        )
    finally:
        reset_config_manager()


def test_a_missing_config_home_is_created_before_the_chdir(tmp_path, monkeypatch):
    """A fresh install or a new absolute NAVIG_CONFIG_DIR has no home yet. The chdir
    failed silently and was never retried, leaving the daemon in the launch project."""
    home = tmp_path / "not-yet" / "navig-home"
    project = tmp_path / "some-project"
    project.mkdir()
    monkeypatch.setattr("navig.platform.paths.config_dir", lambda: home)
    _stop_before_the_daemon(monkeypatch)
    monkeypatch.chdir(project)

    entry.main()

    assert home.is_dir()
    assert Path.cwd().resolve() == home.resolve()
