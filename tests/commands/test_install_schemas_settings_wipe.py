"""Regression: `navig config schema install --write-vscode-settings` must never
WIPE the user's own `.vscode/settings.json`.

`install_schemas` read-modify-writes the user's editor settings to add a
`yaml.schemas` block. The old read fell back to `{}` on ANY read failure, then
wrote that `{}` back — so a transient sharing-violation lock on the file (it's open
in the editor, an AV/backup agent landed mid-write) silently erased every setting
the user had. The fix routes the read through ``load_json_for_update`` (raises
``JsonReadError`` on a persistent transient lock → we SKIP the write) and the write
through ``atomic_write_json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from navig.commands.config import install_schemas
from navig.core.json_io import JsonReadError

pytestmark = pytest.mark.unit


def _read_settings(cwd: Path) -> dict:
    return json.loads((cwd / ".vscode" / "settings.json").read_text(encoding="utf-8"))


def _payload(out: str) -> dict:
    """Extract the trailing JSON payload from stdout (a ch.warning line may precede it)."""
    return json.loads(out[out.index("{") :])


def test_transient_lock_does_not_wipe_existing_settings(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    original = {"editor.fontSize": 14, "my.custom.setting": "keep-me"}
    (vscode / "settings.json").write_text(json.dumps(original, indent=2), encoding="utf-8")

    # Simulate a persistent transient lock on the read: load_json_for_update raises.
    def _boom(*_a, **_k):
        raise JsonReadError("settings.json is locked (simulated sharing violation)")

    monkeypatch.setattr("navig.core.json_io.load_json_for_update", _boom)

    with pytest.raises(typer.Exit) as exc:
        install_schemas(scope="project", write_vscode_settings=True, options={"json": True})
    assert exc.value.exit_code == 0

    # THE INVARIANT: the file is byte-for-byte the user's original — NOT wiped, NOT
    # partially rewritten. yaml.schemas was never added because we refused to write.
    assert _read_settings(tmp_path) == original

    payload = _payload(capsys.readouterr().out)
    assert payload["vscode_settings_written"] is False


def test_existing_settings_are_preserved_and_augmented(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    original = {"editor.fontSize": 14, "my.custom.setting": "keep-me"}
    (vscode / "settings.json").write_text(json.dumps(original, indent=2), encoding="utf-8")

    with pytest.raises(typer.Exit) as exc:
        install_schemas(scope="project", write_vscode_settings=True, options={"json": True})
    assert exc.value.exit_code == 0

    result = _read_settings(tmp_path)
    # user keys survive untouched
    assert result["editor.fontSize"] == 14
    assert result["my.custom.setting"] == "keep-me"
    # and our schema block was merged in
    assert isinstance(result["yaml.schemas"], dict)
    assert any(".navig/hosts" in "".join(v) for v in result["yaml.schemas"].values())

    payload = _payload(capsys.readouterr().out)
    assert payload["vscode_settings_written"] is True


def test_missing_settings_file_is_created_fresh(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    # no .vscode dir at all

    with pytest.raises(typer.Exit) as exc:
        install_schemas(scope="project", write_vscode_settings=True, options={"json": True})
    assert exc.value.exit_code == 0

    result = _read_settings(tmp_path)
    assert "yaml.schemas" in result

    payload = _payload(capsys.readouterr().out)
    assert payload["vscode_settings_written"] is True


def test_corrupt_settings_file_is_quarantined_not_wiped(monkeypatch, tmp_path, capsys):
    """A genuinely malformed settings.json is preserved as *.corrupt, not silently discarded."""
    monkeypatch.chdir(tmp_path)
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    (vscode / "settings.json").write_text("{ this is not valid json ", encoding="utf-8")

    with pytest.raises(typer.Exit) as exc:
        install_schemas(scope="project", write_vscode_settings=True, options={"json": True})
    assert exc.value.exit_code == 0

    # load_json_for_update quarantines corrupt content rather than dropping it on the floor.
    assert (vscode / "settings.json.corrupt").exists()
    # and a fresh, valid settings.json with our schema block now exists
    result = _read_settings(tmp_path)
    assert "yaml.schemas" in result

    payload = _payload(capsys.readouterr().out)
    assert payload["vscode_settings_written"] is True
