"""A transient/unreadable read of the mount registry must NOT wipe other drive records.

The mutating commands (add/remove/verify/sync) load through `_load_registry_for_update`,
which routes to json_io's `load_json_for_update` — it RAISES `JsonReadError` on a file
that exists-with-content but is transiently unreadable (a Windows AV/backup lock). The
helper turns that into a clean `typer.Exit(1)` so the command aborts BEFORE `_save_registry`
persists an empty registry over every other junction. The old `_load_registry` returned
`{"drives": {}}` on that lock, so an `add`/`remove` then rewrote drives.json with only the
one changed record — wiping the rest (breaking mount-drive.ps1 login-restore).
"""

from __future__ import annotations

import pytest
import typer

import navig.commands.mount as m
import navig.core.json_io as jio

pytestmark = pytest.mark.integration


def _patch_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_registry_path", lambda: tmp_path / "registry" / "drives.json")


def _seed(tmp_path):
    m._save_registry({"drives": {
        "projects": {"target": "C:/mnt/projects", "source": "D:/projects", "alive": True},
        "media": {"target": "C:/mnt/media", "source": "E:/media", "alive": True},
    }})


def test_for_update_round_trips_and_is_additive(monkeypatch, tmp_path):
    _patch_registry(tmp_path, monkeypatch)
    _seed(tmp_path)
    data = m._load_registry_for_update()
    assert set(data["drives"]) == {"projects", "media"}
    data["drives"]["archive"] = {"target": "C:/mnt/archive", "source": "F:/arch", "alive": True}
    m._save_registry(data)
    assert set(m._load_registry()["drives"]) == {"projects", "media", "archive"}


def test_for_update_aborts_on_transient_lock_without_wiping(monkeypatch, tmp_path):
    _patch_registry(tmp_path, monkeypatch)
    _seed(tmp_path)
    reg = m._registry_path()
    before = reg.read_text(encoding="utf-8")
    assert "projects" in before and "media" in before

    def _locked(*_a, **_k):
        raise OSError("file is locked")

    monkeypatch.setattr(jio, "read_text_retrying", _locked)

    # The mutating load aborts the command (Exit) instead of returning {} and wiping.
    with pytest.raises(typer.Exit):
        m._load_registry_for_update()

    # The file on disk is byte-for-byte intact — the aborted command never saved. Read it
    # directly (Path.read_text, not the patched json_io) so the assertion is lock-independent.
    assert reg.read_text(encoding="utf-8") == before
    assert '"projects"' in before and '"media"' in before


def test_read_only_load_degrades_on_lock(monkeypatch, tmp_path):
    _patch_registry(tmp_path, monkeypatch)
    _seed(tmp_path)

    def _locked(*_a, **_k):
        raise OSError("file is locked")

    monkeypatch.setattr(jio, "read_text_retrying", _locked)
    # cmd_list / verify_on_startup must not crash — they degrade to an empty registry.
    assert m._load_registry() == {"drives": {}}
