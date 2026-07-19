"""`navig backup list` / `restore` must survive a corrupt backup metadata.json.

A single unreadable ``metadata.json`` (disk error, manual edit, a partially-copied backup
dir, a legacy pre-atomic backup) used to crash the whole `navig backup list` via an
unguarded ``json.load`` — hiding EVERY backup exactly when the operator most needs to see
them, and blocking a restore that could otherwise still proceed. Reads now go through
``_read_backup_metadata``, which degrades to ``None`` (→ shown as "unknown"), never raising.
"""

from __future__ import annotations

import json
from pathlib import Path

from navig.commands.backup import _read_backup_metadata, list_backups_cmd

# ── the guard itself ─────────────────────────────────────────────────────────


def test_read_metadata_valid(tmp_path):
    f = tmp_path / "metadata.json"
    f.write_text(json.dumps({"type": "config", "timestamp": "t"}), encoding="utf-8")
    assert _read_backup_metadata(f) == {"type": "config", "timestamp": "t"}


def test_read_metadata_corrupt_returns_none(tmp_path):
    f = tmp_path / "metadata.json"
    f.write_text('{"type": "db", "timestamp": "t"', encoding="utf-8")  # truncated / partial
    assert _read_backup_metadata(f) is None


def test_read_metadata_missing_returns_none(tmp_path):
    assert _read_backup_metadata(tmp_path / "nope.json") is None


def test_read_metadata_non_mapping_returns_none(tmp_path):
    f = tmp_path / "metadata.json"
    f.write_text("[1, 2, 3]", encoding="utf-8")  # valid JSON, but not a dict
    assert _read_backup_metadata(f) is None


# ── list must not crash + must surface EVERY backup ──────────────────────────


def _make_backup(backups_dir: Path, name: str, metadata_text: str | None) -> Path:
    d = backups_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "payload.txt").write_text("data", encoding="utf-8")
    if metadata_text is not None:
        (d / "metadata.json").write_text(metadata_text, encoding="utf-8")
    return d


def test_list_does_not_crash_and_surfaces_every_backup(tmp_path, monkeypatch, capsys):
    backups = tmp_path / "backups"
    _make_backup(backups, "good", json.dumps({"type": "config", "timestamp": "t"}))
    _make_backup(backups, "corrupt", '{"type": "db", TRUNCATED')  # THE crash trigger
    _make_backup(backups, "nometa", None)

    class _CM:
        backups_dir = backups

    monkeypatch.setattr("navig.config.get_config_manager", lambda: _CM())

    # Previously: json.load on 'corrupt' raised → the entire listing crashed.
    list_backups_cmd({"json": True})  # must NOT raise

    out = capsys.readouterr().out
    payload = json.loads([ln for ln in out.splitlines() if ln.strip().startswith("{")][-1])
    names = {b["name"] for b in payload["backups"]}
    assert names == {"good", "corrupt", "nometa"}, "a corrupt backup must be shown, not hide the rest"
    corrupt = next(b for b in payload["backups"] if b["name"] == "corrupt")
    assert corrupt["type"] == "unknown"


def test_list_table_mode_does_not_crash_on_corrupt_metadata(tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    _make_backup(backups, "corrupt", "}{ not json")

    class _CM:
        backups_dir = backups

    monkeypatch.setattr("navig.config.get_config_manager", lambda: _CM())
    list_backups_cmd({})  # table mode — must NOT raise
