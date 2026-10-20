"""`navig backup import` restores config.yaml — the settings half of an export.

Every archive has always carried `config.yaml` and nothing ever read it back, so
an import restored hosts and apps while the target kept its own settings. That
is the half that identifies a brain: `deck.api_key`, `cloud.mode` and the licence
live there, so a migrated brain that inherits nothing mints a NEW identity.

Policies under test:
  --brain (and --replace)  → replace the target's settings
  merge (the default)      → fill a gap, never overwrite settings that exist
  an archive whose config  → refuse BEFORE a single host is written
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest
import typer

from navig.commands import config_backup as cb

CONFIG = "deck:\n  api_key: keep-me\ncloud:\n  mode: lighthouse\n"
TARGET_CONFIG = "deck:\n  api_key: local-one\n"


class FakeConfigManager:
    """A config dir that can be read AND written (hosts as yaml files)."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir

    def get_config_directories(self) -> list[Path]:
        return [self.config_dir]

    def list_hosts(self) -> list[str]:
        d = self.config_dir / "hosts"
        return [p.stem for p in sorted(d.glob("*.yaml"))] if d.is_dir() else []

    def list_apps(self, host: str) -> list[str]:
        d = self.config_dir / "apps" / host
        return [p.stem for p in sorted(d.glob("*.yaml"))] if d.is_dir() else []

    def host_exists(self, name: str) -> bool:
        return (self.config_dir / "hosts" / f"{name}.yaml").is_file()

    def app_exists(self, host: str, app: str) -> bool:
        return (self.config_dir / "apps" / host / f"{app}.yaml").is_file()

    def load_host_config(self, name: str) -> dict:
        return {}

    def save_host_config(self, name: str, cfg: dict) -> None:
        import yaml

        d = self.config_dir / "hosts"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    def load_app_config(self, host: str, app: str) -> dict:
        return {}

    def save_app_config(self, host: str, app: str, cfg: dict) -> None:
        import yaml

        d = self.config_dir / "apps" / host
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{app}.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    def get_execution_mode(self) -> str:
        return "interactive"

    def get_confirmation_level(self) -> str:
        return "standard"


def _archive(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    """A real tar.gz shaped like an export (root dir `navig-config/`)."""
    root = tmp_path / "src" / "navig-config"
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    out = tmp_path / name
    with tarfile.open(out, "w:gz") as tar:
        tar.add(root, arcname="navig-config")
    return out


@pytest.fixture
def target(tmp_path, monkeypatch):
    """Point config+data at a target dir with its own settings and one host."""
    config_dir = tmp_path / "target-config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(TARGET_CONFIG, encoding="utf-8")
    (config_dir / "hosts").mkdir()
    (config_dir / "hosts" / "preexisting.yaml").write_text("host: local\n", encoding="utf-8")
    data_dir = tmp_path / "target-data"
    monkeypatch.setattr("navig.config.get_config_manager", lambda: FakeConfigManager(config_dir))
    monkeypatch.setattr("navig.platform.paths.data_dir", lambda: data_dir)

    from navig.daemon.supervisor import NavigDaemon

    monkeypatch.setattr(NavigDaemon, "is_running", lambda *a, **k: False)
    return config_dir


def _import(archive: Path, **opts):
    options = {"file": archive, "merge": True, "yes": True, "json": True}
    options.update(opts)
    cb.import_config(options)


def _with_host(config_dir: Path) -> dict[str, str]:
    """An archive carrying settings + a host the target does not have."""
    return {
        "config.yaml": CONFIG,
        "manifest.json": "{}",
        "hosts/newhost.yaml": "host: from-archive\n",
    }


def test_merge_fills_an_absent_config(target, tmp_path):
    target.joinpath("config.yaml").unlink()
    _import(_archive(tmp_path, "a.tar.gz", _with_host(target)), scope="config")

    assert target.joinpath("config.yaml").read_text(encoding="utf-8") == CONFIG
    assert target.joinpath("hosts/newhost.yaml").is_file()  # and hosts still import


def test_merge_keeps_settings_the_target_already_has(target, tmp_path):
    """Merge fills a gap; it never takes settings over (that is --replace)."""
    _import(_archive(tmp_path, "a.tar.gz", _with_host(target)), scope="config")

    assert target.joinpath("config.yaml").read_text(encoding="utf-8") == TARGET_CONFIG
    # Nothing was overwritten, so nothing needed saving.
    assert not target.joinpath("config.yaml.pre-import").exists()
    assert target.joinpath("hosts/newhost.yaml").is_file()


def test_replace_takes_settings_from_the_archive(target, tmp_path):
    _import(_archive(tmp_path, "a.tar.gz", _with_host(target)), scope="config", merge=False)

    assert target.joinpath("config.yaml").read_text(encoding="utf-8") == CONFIG
    assert target.joinpath("config.yaml.pre-import").read_text(encoding="utf-8") == TARGET_CONFIG


def test_brain_import_replaces_settings_whatever_the_merge_mode(target, tmp_path):
    """The brain's identity is why the bundle exists — it is carried over."""
    files = _with_host(target)
    files["brain/license.key"] = ""  # staged shape; not asserted here
    _import(_archive(tmp_path, "brain.tar.gz", files), scope="brain")

    assert "api_key: keep-me" in target.joinpath("config.yaml").read_text(encoding="utf-8")
    assert target.joinpath("config.yaml.pre-import").read_text(encoding="utf-8") == TARGET_CONFIG


def test_an_archive_config_that_does_not_parse_stops_the_import(target, tmp_path):
    """Refuse BEFORE a single host is written: no half-restored machine."""
    bad = _with_host(target)
    bad["config.yaml"] = "deck: [unclosed\n  bad: yes"
    with pytest.raises(typer.Exit) as exc:
        _import(_archive(tmp_path, "bad.tar.gz", bad), scope="config", merge=False)

    assert exc.value.exit_code == 1
    # Nothing written: the target keeps its own settings and its own hosts.
    assert target.joinpath("config.yaml").read_text(encoding="utf-8") == TARGET_CONFIG
    assert not target.joinpath("config.yaml.pre-import").exists()
    assert not target.joinpath("hosts/newhost.yaml").exists()


def test_an_archive_with_no_settings_is_not_an_error(target, tmp_path):
    """A host-only archive (json exports, older files) imports settings-free."""
    _import(
        _archive(tmp_path, "hosts-only.tar.gz", {"hosts/newhost.yaml": "host: x\n"}),
        scope="config",
    )
    assert target.joinpath("config.yaml").read_text(encoding="utf-8") == TARGET_CONFIG
    assert target.joinpath("hosts/newhost.yaml").is_file()
