import json
from pathlib import Path

import pytest
import typer

from navig.commands.config import _default_config_roots, install_schemas


def test_default_config_roots_uses_navig_config_dir_for_global(monkeypatch, tmp_path):
    cfg_root = tmp_path / "custom-config-root"
    cfg_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg_root))

    roots = _default_config_roots("global")

    assert roots == [("global", cfg_root)]


def test_install_schemas_global_targets_navig_config_dir(monkeypatch, tmp_path, capsys):
    cfg_root = tmp_path / "custom-config-root"
    cfg_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg_root))

    with pytest.raises(typer.Exit) as exc_info:
        install_schemas(
            scope="global",
            write_vscode_settings=False,
            options={"json": True},
        )

    assert exc_info.value.exit_code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["scope"] == "global"
    for installed_path in payload["installed"]:
        assert Path(installed_path).is_relative_to(cfg_root)
