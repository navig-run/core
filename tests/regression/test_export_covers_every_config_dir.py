"""The archive export must cover the same config directories `list_hosts()` does.

`_create_archive` copied ONE directory — `config_manager.config_dir` — while
`list_hosts()` merges app-specific AND global config (`get_config_directories()`).
Run inside a project, the two disagreed, so the two formats of the same command
exported different sets of hosts:

    list_hosts()               -> ['globalhost', 'projhost']
    --format json  (uses it)   -> both
    --format archive           -> projhost only

Measured against the real function before the fix. The global hosts were dropped
silently: nothing errored, and a restore from that archive looks complete.

Priority is preserved rather than flattened — `get_config_directories()` returns app
config first and the higher-priority file wins, which is exactly how NAVIG resolves a
host, so the archive holds the config that is actually in effect. A lower-priority file
it shadows is not a failure, but it is also not in the archive, so it is reported.
"""
from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path

import pytest

from navig.commands.config_backup import _create_archive


class _CM:
    """A config manager with an app-level and a global config directory."""

    def __init__(self, app_dir: Path, global_dir: Path) -> None:
        self.config_dir = app_dir
        self._dirs = [app_dir, global_dir]

    def get_config_directories(self) -> list[Path]:
        return list(self._dirs)


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    app, glob = tmp_path / "proj" / ".navig", tmp_path / "home" / ".navig"
    (app / "hosts").mkdir(parents=True)
    (glob / "hosts").mkdir(parents=True)
    return app, glob


def _archive(monkeypatch: pytest.MonkeyPatch, cm: _CM) -> tuple[dict, list[str]]:
    import navig.config as config_mod

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: cm)
    out = Path(tempfile.mkdtemp()) / "cfg.tar.gz"
    result = _create_archive(out)
    with tarfile.open(out) as tar:
        names = tar.getnames()
    return result, names


def _host_files(names: list[str]) -> set[str]:
    return {n.rsplit("/", 1)[-1] for n in names if "/hosts/" in n}


def test_hosts_from_every_config_directory_are_archived(
    monkeypatch: pytest.MonkeyPatch, dirs: tuple[Path, Path]
) -> None:
    """The bug: only the app directory made it in."""
    app, glob = dirs
    (app / "hosts" / "projhost.yaml").write_text("host: 10.0.0.1\n", encoding="utf-8")
    (glob / "hosts" / "globalhost.yaml").write_text("host: 10.0.0.2\n", encoding="utf-8")

    result, names = _archive(monkeypatch, _CM(app, glob))

    assert _host_files(names) == {"projhost.yaml", "globalhost.yaml"}
    assert result["hosts"] == 2, "the reported count must match what was archived"


def test_apps_are_merged_the_same_way(
    monkeypatch: pytest.MonkeyPatch, dirs: tuple[Path, Path]
) -> None:
    """Apps live in a nested `apps/<host>/<app>.yaml` layout, so the merge has to walk
    the tree rather than glob one level."""
    app, glob = dirs
    (app / "apps" / "h1").mkdir(parents=True)
    (glob / "apps" / "h2").mkdir(parents=True)
    (app / "apps" / "h1" / "web.yaml").write_text("path: /a\n", encoding="utf-8")
    (glob / "apps" / "h2" / "api.yaml").write_text("path: /b\n", encoding="utf-8")

    result, names = _archive(monkeypatch, _CM(app, glob))

    assert {n for n in names if n.endswith(".yaml") and "/apps/" in n} >= {
        "navig-config/apps/h1/web.yaml",
        "navig-config/apps/h2/api.yaml",
    }
    assert result["apps"] == 2


def test_a_higher_priority_file_wins_and_the_shadowed_one_is_reported(
    monkeypatch: pytest.MonkeyPatch, dirs: tuple[Path, Path]
) -> None:
    """Same host name in both directories. NAVIG resolves app config first, so the
    archive must hold THAT copy — and must say the other one was not included, since
    'quietly picked one of two' is how the original bug felt from outside."""
    app, glob = dirs
    (app / "hosts" / "dup.yaml").write_text("host: from-app\n", encoding="utf-8")
    (glob / "hosts" / "dup.yaml").write_text("host: from-global\n", encoding="utf-8")

    result, names = _archive(monkeypatch, _CM(app, glob))

    assert _host_files(names) == {"dup.yaml"}
    assert result["hosts"] == 1
    assert any("hosts/dup.yaml" in s for s in result["shadowed"])
    assert str(glob) in " ".join(result["shadowed"]), (
        "the report must name WHICH directory was shadowed, or it is not actionable"
    )


def test_nothing_is_reported_as_shadowed_when_names_do_not_collide(
    monkeypatch: pytest.MonkeyPatch, dirs: tuple[Path, Path]
) -> None:
    """The partner — a rule that always reported shadowing would satisfy the test
    above while making every normal export look degraded."""
    app, glob = dirs
    (app / "hosts" / "a.yaml").write_text("host: 1\n", encoding="utf-8")
    (glob / "hosts" / "b.yaml").write_text("host: 2\n", encoding="utf-8")

    result, _ = _archive(monkeypatch, _CM(app, glob))

    assert result["shadowed"] == []


def test_a_single_config_directory_still_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The common case — no project context, one global dir. The merge must not
    depend on there being two."""
    only = tmp_path / ".navig"
    (only / "hosts").mkdir(parents=True)
    (only / "hosts" / "solo.yaml").write_text("host: 1\n", encoding="utf-8")

    cm = _CM(only, only)
    cm._dirs = [only]
    result, names = _archive(monkeypatch, cm)

    assert _host_files(names) == {"solo.yaml"}
    assert result["hosts"] == 1 and result["shadowed"] == []


def test_the_manifest_records_which_directories_were_read(
    monkeypatch: pytest.MonkeyPatch, dirs: tuple[Path, Path]
) -> None:
    """Whoever opens the archive later has no terminal output. The manifest has to
    carry where the config came from — especially which `config.yaml` won, since that
    one is deliberately NOT merged."""
    import json

    app, glob = dirs
    (app / "hosts" / "a.yaml").write_text("host: 1\n", encoding="utf-8")
    (app / "config.yaml").write_text("log_level: INFO\n", encoding="utf-8")

    out = Path(tempfile.mkdtemp()) / "cfg.tar.gz"
    import navig.config as config_mod

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: _CM(app, glob))
    _create_archive(out)

    with tarfile.open(out) as tar:
        member = tar.extractfile("navig-config/manifest.json")
        assert member is not None
        manifest = json.load(member)

    assert manifest["config_directories"] == [str(app), str(glob)]
    assert manifest["config_yaml_source"] == str(app)
