"""``find_app_root()`` must never answer with the home directory.

``~/.navig`` — the GLOBAL config dir — sits in the parent chain of every directory under
the home folder. A bare upward walk therefore "found a project" from ``~/Documents`` or
``%LOCALAPPDATA%\\Temp`` and returned ``~`` as the project root. Measured before the fix:
with ``NAVIG_CONFIG_DIR`` pointed at an empty sandbox, ``navig host list`` run from a temp
dir under the home folder printed the operator's REAL hosts and IPs, because
``ConfigManager.base_dir`` had silently become the real ``~/.navig``.

These tests drive the real function against a fake home so the guard is deterministic on
every platform (the maintainer's real home really does hold a ``.navig``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import navig.platform.paths as paths


def _real_find_app_root():
    # tests/conftest.py wraps ``paths.find_app_root`` to suppress detection of the source
    # checkout; a tmp_path project still goes through the real walk, but be explicit and
    # call the underlying function so this file tests the implementation, not the wrapper.
    fn = paths.find_app_root
    return getattr(fn, "__wrapped__", None) or fn


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home" / "operator"
    (home / ".navig").mkdir(parents=True)
    monkeypatch.setattr(paths, "home_dir", lambda: home)
    return home


def test_a_cwd_under_the_home_folder_finds_no_project(fake_home: Path, monkeypatch):
    workdir = fake_home / "Documents" / "notes"
    workdir.mkdir(parents=True)
    monkeypatch.chdir(workdir)

    assert _real_find_app_root()() is None


def test_the_isolated_config_dir_is_not_a_project_either(
    fake_home: Path, tmp_path: Path, monkeypatch
):
    # NAVIG_CONFIG_DIR is the documented isolation knob. A sandbox placed under the
    # home folder (a temp dir is the natural place) must stay a sandbox.
    sandbox = fake_home / "AppData" / "Local" / "Temp" / "navig-sandbox"
    (sandbox / ".navig").mkdir(parents=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(sandbox / ".navig"))
    workdir = sandbox / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    assert _real_find_app_root()() is None


def test_a_real_project_under_the_home_folder_is_still_found(fake_home: Path, monkeypatch):
    project = fake_home / "projects" / "blog"
    (project / ".navig").mkdir(parents=True)
    src = project / "src" / "posts"
    src.mkdir(parents=True)
    monkeypatch.chdir(src)

    assert _real_find_app_root()() == project


def test_config_manager_base_dir_stays_isolated_under_the_home_folder(
    fake_home: Path, tmp_path: Path, monkeypatch
):
    """The user-visible consequence: hosts come from the sandbox, not the real home."""
    real_hosts = fake_home / ".navig" / "hosts"
    real_hosts.mkdir()
    (real_hosts / "prod-secret.yaml").write_text(
        "name: prod-secret\nhost: 203.0.113.9\nport: 22\nuser: root\n", encoding="utf-8"
    )
    sandbox = tmp_path / "sandbox-cfg"
    sandbox.mkdir()
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(sandbox))
    workdir = fake_home / "Desktop" / "demo"
    workdir.mkdir(parents=True)
    monkeypatch.chdir(workdir)

    from navig.config import ConfigManager

    cm = ConfigManager()
    assert cm.base_dir == sandbox
    assert "prod-secret" not in cm.list_hosts()


def test_reaching_the_global_dir_ends_the_walk(fake_home: Path, tmp_path: Path, monkeypatch):
    """A `.navig` ABOVE the global config dir must never be reached.

    First version of the guard skipped the global dir and kept climbing — into whatever
    `.navig` sat higher up (in the test suite: the source checkout's own `core/.navig`,
    where a subprocess-driven ledger test then wrote). The cwd's own `.navig` being the
    configured dir is a real layout (`NAVIG_CONFIG_DIR=<project>/.navig`), and the answer
    there is None: `base_dir` then resolves to `config_dir()`, the same directory.
    """
    (tmp_path / ".navig").mkdir()  # a "project" above everything, standing in for core/.navig
    space = fake_home / "space"
    cfg = space / ".navig"
    cfg.mkdir(parents=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    monkeypatch.chdir(space)

    assert _real_find_app_root()() is None
    assert paths.config_dir() == cfg
