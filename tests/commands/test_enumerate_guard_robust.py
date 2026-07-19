"""Regression: a list/inspect command must not let ONE corrupt item blank the whole view.

Same "a failed read degrades, not cascades" family as #410 (`navig backup list`):
- `navig config-backup inspect` scans every host/app YAML in an export archive — one
  corrupt file used to abort the whole inspect ("Failed to inspect export", showing nothing).
- `navig memory knowledge list` parses each row's JSON `tags` — one malformed value used to
  crash the entire table.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from navig.commands.config_backup import _safe_archive_yaml, inspect_export

# ── config-backup inspect: per-item YAML parse ──────────────────────────────


def test_safe_archive_yaml_reads_a_valid_mapping(tmp_path: Path):
    f = tmp_path / "h.yaml"
    f.write_text("host: 1.2.3.4\nuser: root\n", encoding="utf-8")
    assert _safe_archive_yaml(f) == {"host": "1.2.3.4", "user": "root"}


def test_safe_archive_yaml_flags_corrupt_yaml(tmp_path: Path):
    f = tmp_path / "h.yaml"
    f.write_text("host: [unterminated\n  : : :", encoding="utf-8")  # invalid YAML
    assert _safe_archive_yaml(f) == {"_unreadable": True}


def test_safe_archive_yaml_flags_non_mapping(tmp_path: Path):
    f = tmp_path / "h.yaml"
    f.write_text("- just\n- a\n- list\n", encoding="utf-8")  # valid YAML, not a mapping
    assert _safe_archive_yaml(f) == {"_unreadable": True}


def test_safe_archive_yaml_flags_missing_file(tmp_path: Path):
    assert _safe_archive_yaml(tmp_path / "nope.yaml") == {"_unreadable": True}


def _make_export(tmp_path: Path, host_yaml: dict[str, str]) -> Path:
    """Build a minimal .tar.gz export with a navig-config/hosts/*.yaml tree."""
    stage = tmp_path / "navig-config"
    (stage / "hosts").mkdir(parents=True)
    (stage / "manifest.json").write_text(
        '{"version": "1.0", "exported_at": "2026-07-19"}', encoding="utf-8"
    )
    for name, content in host_yaml.items():
        (stage / "hosts" / f"{name}.yaml").write_text(content, encoding="utf-8")
    archive = tmp_path / "export.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(stage, arcname="navig-config")
    return archive


def test_inspect_surfaces_good_hosts_despite_one_corrupt_yaml(tmp_path: Path, capsys):
    archive = _make_export(
        tmp_path,
        {
            "web": "host: 10.0.0.1\nuser: deploy\n",  # good
            "broken": "host: [unterminated\n : : :",  # corrupt — used to blank the whole inspect
        },
    )
    inspect_export({"file": str(archive)})  # must NOT raise
    out = capsys.readouterr().out
    assert "web" in out  # the good host is still shown
    assert "broken" in out  # the corrupt host is surfaced, not hidden
    assert "unreadable" in out.lower()


def test_inspect_json_mode_includes_unreadable_marker(tmp_path: Path, capsys):
    archive = _make_export(tmp_path, {"broken": "host: [bad\n : :"})
    inspect_export({"file": str(archive), "json": True})
    out = capsys.readouterr().out
    assert "_unreadable" in out  # honest machine-readable signal, not a crash


# ── memory knowledge list: per-row JSON tags parse ──────────────────────────


def test_knowledge_list_survives_a_corrupt_tags_row(tmp_path: Path, monkeypatch, capsys):
    from navig.memory.knowledge_base import KnowledgeBase, KnowledgeEntry

    db = tmp_path / "memory" / "knowledge.db"
    db.parent.mkdir(parents=True)
    kb = KnowledgeBase(db, embedding_provider=None)
    kb.upsert(KnowledgeEntry(key="good", content="hello", tags=["a", "b"]), compute_embedding=False)
    kb.upsert(KnowledgeEntry(key="bad", content="world", tags=["x"]), compute_embedding=False)

    # Corrupt the 'bad' row's tags column to invalid JSON (a disk/edit artifact).
    conn = kb._get_conn()
    conn.execute("UPDATE knowledge SET tags = ? WHERE key = ?", ("{not valid json", "bad"))
    conn.commit()

    import navig.commands.memory as mem

    class _Cfg:
        global_config_dir = str(tmp_path)

    monkeypatch.setattr(mem, "_get_config", lambda: _Cfg())

    # Must not raise — the malformed row used to crash the entire table.
    mem.memory_knowledge(
        action="list", key=None, content=None, query=None, tags=None, limit=20, plain=False
    )
    out = capsys.readouterr().out
    assert "good" in out  # the healthy row still renders
    assert "bad" in out  # the corrupt row is still surfaced (tags degrade to empty)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
