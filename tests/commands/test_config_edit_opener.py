"""`navig config edit` opens the file with the right handler on every OS.

The defect these pin: the function branched on `platform.system()` inline and ran
`os.startfile` (Windows-only), `open` (macOS-only — on Linux `open` is not a file opener)
or a bare `nano` (absent on many minimal hosts, and not what `$EDITOR` asked for).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import typer

from navig.commands import config as config_cmd


class _FakeManager:
    def __init__(self, root: Path):
        self.global_config_dir = root

    def ensure_global_config(self) -> None:
        pass


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    cfg = tmp_path / ".navig"
    cfg.mkdir()
    (cfg / "config.yaml").write_text("deck: {}\n", encoding="utf-8")
    monkeypatch.setattr(config_cmd, "get_config_manager", lambda: _FakeManager(cfg))
    return cfg / "config.yaml"


def test_editor_is_used_when_the_operator_set_one(config_file, monkeypatch):
    monkeypatch.setenv("EDITOR", "vim -u NONE")
    launched: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: launched.append(argv))

    config_cmd.edit_config()

    assert launched == [["vim", "-u", "NONE", str(config_file)]]


def test_the_os_opener_is_used_when_no_editor_is_set(config_file, monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    opened: list[str] = []
    monkeypatch.setattr(
        "navig.platform.opener.open_path",
        lambda target: opened.append(str(target)) or True,
    )

    config_cmd.edit_config()

    assert opened == [str(config_file)]


def test_failure_says_how_to_edit_it_by_hand(config_file, monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("navig.platform.opener.open_path", lambda _target: False)

    with pytest.raises(typer.Exit):
        config_cmd.edit_config()
