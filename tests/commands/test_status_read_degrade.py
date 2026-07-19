"""Regression: a status/list command reading its (single) config file must degrade,
not crash, when that file is corrupt or malformed.

The milder tail of the #410 / #419 "a failed read degrades, not cascades" family — a
single-file read here (not a per-item loop), but the principle is the same: a
hand-edited or damaged config must not throw a traceback out of a read-only command.

- `get_wiki_config` used a raw `yaml.safe_load` that raised on a corrupt
  `.meta/config.yaml`.
- `list_quick_actions` used a raw `yaml.safe_load` that raised on a corrupt
  `quick_actions.yaml`, and `**data` that raised on a non-mapping action entry.

Both now route through the never-raises `safe_load_yaml` + an isinstance guard.
(The read-MODIFY-write quick-action paths — add/delete — are deliberately left
fail-loud; degrading them to `{}` would re-introduce the config-wipe class.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.commands.suggest import add_quick_action, list_quick_actions
from navig.commands.wiki import get_wiki_config

_CORRUPT_YAML = "key: [1, 2\n  : : :"  # unterminated flow seq → yaml.YAMLError


# ── get_wiki_config ─────────────────────────────────────────────────────────


def _wiki_with_config(tmp_path: Path, text: str) -> Path:
    (tmp_path / ".meta").mkdir(parents=True)
    (tmp_path / ".meta" / "config.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def test_wiki_config_reads_a_valid_mapping(tmp_path: Path):
    wiki = _wiki_with_config(tmp_path, "title: My Wiki\nversion: 2\n")
    assert get_wiki_config(wiki) == {"title": "My Wiki", "version": 2}


def test_wiki_config_missing_is_empty(tmp_path: Path):
    assert get_wiki_config(tmp_path) == {}  # no .meta/config.yaml at all


def test_wiki_config_corrupt_degrades_to_empty(tmp_path: Path):
    wiki = _wiki_with_config(tmp_path, _CORRUPT_YAML)
    assert get_wiki_config(wiki) == {}  # must not raise


def test_wiki_config_non_mapping_degrades_to_empty(tmp_path: Path):
    wiki = _wiki_with_config(tmp_path, "- just\n- a\n- list\n")
    assert get_wiki_config(wiki) == {}


# ── list_quick_actions ──────────────────────────────────────────────────────


def _isolate_config_dir(monkeypatch, tmp_path: Path) -> Path:
    class _CM:
        global_config_dir = str(tmp_path)

    monkeypatch.setattr("navig.config.get_config_manager", lambda: _CM())
    return tmp_path


def test_quick_actions_valid(monkeypatch, tmp_path: Path):
    _isolate_config_dir(monkeypatch, tmp_path)
    (tmp_path / "quick_actions.yaml").write_text(
        "deploy:\n  command: navig deploy\n  description: ship it\n", encoding="utf-8"
    )
    result = list_quick_actions()
    assert result == [{"name": "deploy", "command": "navig deploy", "description": "ship it"}]


def test_quick_actions_missing_is_empty(monkeypatch, tmp_path: Path):
    _isolate_config_dir(monkeypatch, tmp_path)
    assert list_quick_actions() == []


def test_quick_actions_corrupt_degrades_to_empty(monkeypatch, tmp_path: Path):
    _isolate_config_dir(monkeypatch, tmp_path)
    (tmp_path / "quick_actions.yaml").write_text(_CORRUPT_YAML, encoding="utf-8")
    assert list_quick_actions() == []  # must not raise


def test_quick_actions_skips_a_malformed_entry(monkeypatch, tmp_path: Path):
    """A single non-mapping action value must be skipped (used to raise on **data),
    not blank the whole list."""
    _isolate_config_dir(monkeypatch, tmp_path)
    (tmp_path / "quick_actions.yaml").write_text(
        "good:\n  command: ls\nbroken: not-a-mapping\n", encoding="utf-8"
    )
    result = list_quick_actions()
    assert result == [{"name": "good", "command": "ls"}]  # 'broken' skipped, 'good' kept


def test_quick_actions_non_mapping_root_is_empty(monkeypatch, tmp_path: Path):
    _isolate_config_dir(monkeypatch, tmp_path)
    (tmp_path / "quick_actions.yaml").write_text("- a\n- b\n", encoding="utf-8")
    assert list_quick_actions() == []


# ── add_quick_action: the read-MODIFY-write path must not wipe ───────────────


def test_add_quick_action_preserves_existing_actions(monkeypatch, tmp_path: Path):
    """Adding one action must keep the others — the whole point of the read-modify-write."""
    _isolate_config_dir(monkeypatch, tmp_path)
    qf = tmp_path / "quick_actions.yaml"
    qf.write_text("old:\n  command: navig old\n", encoding="utf-8")

    assert add_quick_action("new", "navig new", "the new one") is True

    names = {a["name"] for a in list_quick_actions()}
    assert names == {"old", "new"}  # 'old' survived the add


def test_add_quick_action_refuses_a_corrupt_file_without_wiping(monkeypatch, tmp_path: Path):
    """A corrupt quick_actions.yaml must not be overwritten by the add — refuse (return
    False) and leave the file byte-for-byte intact, rather than turning a bad read into {}
    and wiping the real actions on save (the config-wipe class)."""
    _isolate_config_dir(monkeypatch, tmp_path)
    qf = tmp_path / "quick_actions.yaml"
    qf.write_text(_CORRUPT_YAML, encoding="utf-8")
    before = qf.read_bytes()

    assert add_quick_action("new", "navig new") is False  # refused, not raised
    assert qf.read_bytes() == before  # file left exactly as it was — nothing wiped


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
