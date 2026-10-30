"""The flat ``apps/<name>.yaml`` layout must survive an archive round trip.

That one directory holds two kinds of file, told apart by ``HostManager.list_hosts``:
a legacy HOST (no ``host`` field, or an address in it) and a flat-layout APP (``host``
names the host it belongs to). The archive copies the directory as-is, but:

* the export COUNTED every file there as an app, so a legacy-layout install reported
  ``hosts == 0`` and the zero-host guard failed a populated backup with exit 1;
* the import only read ``apps/<host>/<app>.yaml`` subdirectories, so every flat app
  (and legacy host) was exported, reported present, and silently never restored.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from navig.commands import config_backup as cb


class _SourceCM:
    def __init__(self, root: Path) -> None:
        self.config_dir = root

    def get_config_directories(self) -> list[Path]:
        return [self.config_dir]


class _TargetCM:
    """An empty target that records what the import writes."""

    def __init__(self, root: Path) -> None:
        self.config_dir = root
        self.hosts: dict[str, dict] = {}
        self.apps: dict[tuple[str, str], dict] = {}

    def get_config_directories(self) -> list[Path]:
        return [self.config_dir]

    def host_exists(self, name: str) -> bool:
        return name in self.hosts

    def app_exists(self, host: str, app: str) -> bool:
        return (host, app) in self.apps

    def save_host_config(self, name: str, cfg: dict) -> None:
        self.hosts[name] = cfg

    def save_app_config(self, host: str, app: str, cfg: dict) -> None:
        self.apps[(host, app)] = cfg

    def get_execution_mode(self) -> str:
        return "interactive"

    def get_confirmation_level(self) -> str:
        return "standard"


@pytest.fixture
def flat_source(tmp_path: Path) -> Path:
    root = tmp_path / "src" / ".navig"
    (root / "apps").mkdir(parents=True)
    (root / "apps" / "oldhost.yaml").write_text(
        "host: 10.0.0.5\nuser: deploy\n", encoding="utf-8"
    )  # legacy host
    (root / "apps" / "web.yaml").write_text(
        "host: prod\nwebserver:\n  type: nginx\n", encoding="utf-8"
    )  # flat app on host "prod"
    return root


def _export(monkeypatch: pytest.MonkeyPatch, root: Path) -> tuple[dict, Path]:
    import navig.config as config_mod

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: _SourceCM(root))
    out = Path(tempfile.mkdtemp()) / "cfg.tar.gz"
    return cb._create_archive(out, include_secrets=True), out


def test_legacy_host_files_are_counted_as_hosts(monkeypatch, flat_source):
    result, _ = _export(monkeypatch, flat_source)
    assert result["hosts"] == 1
    assert result["apps"] == 1


def test_flat_apps_and_legacy_hosts_are_imported(monkeypatch, flat_source, tmp_path):
    _, archive = _export(monkeypatch, flat_source)

    target = _TargetCM(tmp_path / "target")
    target.config_dir.mkdir()
    monkeypatch.setattr("navig.config.get_config_manager", lambda: target)
    monkeypatch.setattr("navig.platform.paths.data_dir", lambda: tmp_path / "data")
    from navig.daemon.supervisor import NavigDaemon

    monkeypatch.setattr(NavigDaemon, "is_running", lambda *a, **k: False)

    cb.import_config({"file": archive, "merge": True, "yes": True, "json": True})

    assert ("prod", "web") in target.apps
    assert target.apps[("prod", "web")]["webserver"] == {"type": "nginx"}
    assert target.hosts["oldhost"]["host"] == "10.0.0.5"


def test_inspect_lists_flat_apps(monkeypatch, flat_source, capsys):
    _, archive = _export(monkeypatch, flat_source)

    cb.inspect_export({"file": archive, "json": True})

    data = json.loads(capsys.readouterr().out)
    assert "web" in data["apps"].get("prod", {})
    assert "oldhost" in data["hosts"]


def test_classifier_matches_list_hosts_rule():
    assert cb._is_legacy_host_config({"user": "x"})  # no host field
    assert cb._is_legacy_host_config({"host": "1.2.3.4"})  # an address
    assert cb._is_legacy_host_config(yaml.safe_load(""))  # empty file
    assert not cb._is_legacy_host_config({"host": "prod"})  # an app bound to a host
    assert not cb._is_legacy_host_config(["not", "a", "mapping"])
