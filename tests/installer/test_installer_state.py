"""Tests for navig.installer.state — save, load_last, _manifest_path."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.installer.contracts import Action, InstallerContext, ModuleState, Result
from navig.installer.state import load_last, save


def _ctx(tmp_path: Path) -> InstallerContext:
    return InstallerContext(profile="node", config_dir=tmp_path)


def _action(aid: str = "mod.step") -> Action:
    return Action(id=aid, description="do step", module="mod")


def _result(state: ModuleState = ModuleState.APPLIED, aid: str = "mod.step") -> Result:
    return Result(action_id=aid, state=state, message="ok")


class TestSave:
    def test_returns_path(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        p = save([_action()], [_result()], _ctx(tmp_path), manifest_path=manifest)
        assert p == manifest
        assert manifest.exists()

    def test_writes_one_line_per_action(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        actions = [_action("a"), _action("b")]
        results = [_result(aid="a"), _result(aid="b")]
        save(actions, results, _ctx(tmp_path), manifest_path=manifest)
        lines = manifest.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_line_is_valid_json(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        save([_action()], [_result()], _ctx(tmp_path), manifest_path=manifest)
        record = json.loads(manifest.read_text().strip())
        assert record["action_id"] == "mod.step"
        assert record["state"] == "applied"

    def test_record_contains_expected_fields(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        save([_action()], [_result()], _ctx(tmp_path), manifest_path=manifest)
        record = json.loads(manifest.read_text().strip())
        for field in ("profile", "action_id", "module", "state", "message", "ts"):
            assert field in record

    def test_creates_parent_directory(self, tmp_path):
        manifest = tmp_path / "deep" / "nested" / "manifest.jsonl"
        save([_action()], [_result()], _ctx(tmp_path), manifest_path=manifest)
        assert manifest.exists()

    def test_python_version_recorded(self, tmp_path):
        import sys
        manifest = tmp_path / "manifest.jsonl"
        save([_action()], [_result()], _ctx(tmp_path), manifest_path=manifest)
        record = json.loads(manifest.read_text().strip())
        assert record["python"] == sys.version


class TestLoadLast:
    def test_empty_when_no_history_dir(self, tmp_path):
        result = load_last(tmp_path / "nodir", profile="node")
        assert result == []

    def test_empty_when_no_matching_manifest(self, tmp_path):
        (tmp_path / "history").mkdir()
        result = load_last(tmp_path, profile="node")
        assert result == []

    def test_loads_most_recent_manifest(self, tmp_path):
        history = tmp_path / "history"
        history.mkdir()
        m1 = history / "install_node_20240101T000000Z.jsonl"
        m2 = history / "install_node_20240201T000000Z.jsonl"
        m1.write_text('{"action_id": "old"}\n')
        m2.write_text('{"action_id": "new"}\n')
        result = load_last(tmp_path, profile="node")
        assert result[0]["action_id"] == "new"

    def test_filters_by_profile(self, tmp_path):
        history = tmp_path / "history"
        history.mkdir()
        (history / "install_node_20240101T000000Z.jsonl").write_text('{"p": "node"}\n')
        (history / "install_operator_20240101T000000Z.jsonl").write_text('{"p": "operator"}\n')
        result = load_last(tmp_path, profile="operator")
        assert result[0]["p"] == "operator"

    def test_no_profile_filter_returns_any(self, tmp_path):
        history = tmp_path / "history"
        history.mkdir()
        (history / "install_node_20240101T000000Z.jsonl").write_text('{"x": 1}\n')
        result = load_last(tmp_path)
        assert len(result) == 1

    def test_roundtrip_save_load(self, tmp_path):
        ctx = _ctx(tmp_path)
        manifest = tmp_path / "history" / "install_node_test.jsonl"
        save([_action("do.setup")], [_result(aid="do.setup")], ctx, manifest_path=manifest)
        result = load_last(tmp_path, profile="node")
        assert result[0]["action_id"] == "do.setup"
