"""Batch 66 — spaces/next_action, deploy/history, cli/_singletons."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.spaces.next_action — first_pending_task, SpaceNextAction
# ---------------------------------------------------------------------------

class TestFirstPendingTask:
    def _call(self, text):
        from navig.spaces.next_action import first_pending_task
        return first_pending_task(text)

    def test_empty_string_returns_empty(self):
        assert self._call("") == ""

    def test_none_str_returns_empty(self):
        assert self._call(None) == ""

    def test_detects_unchecked_task(self):
        result = self._call("- [ ] Deploy to staging")
        assert result == "Deploy to staging"

    def test_ignores_checked_tasks(self):
        result = self._call("- [x] Done\n- [X] Also done")
        assert result == ""

    def test_returns_first_unchecked_task(self):
        md = "- [x] first done\n- [ ] second pending\n- [ ] third pending"
        assert self._call(md) == "second pending"

    def test_strips_whitespace_from_task_name(self):
        result = self._call("- [ ]   Trim me   ")
        assert result == "Trim me"


class TestGetSpaceNextAction:
    def test_returns_none_when_path_not_exist(self, tmp_path):
        from navig.spaces.next_action import get_space_next_action
        from navig.spaces.contracts import SpaceConfig, normalize_space_name

        fake_cfg = SpaceConfig(
            requested_name="devops",
            canonical_name="devops",
            path=tmp_path / "nonexistent",
            scope="global",
        )
        with patch("navig.spaces.next_action.resolve_space", return_value=fake_cfg):
            result = get_space_next_action("devops")
        assert result is None

    def test_returns_next_action_when_space_exists(self, tmp_path):
        from navig.spaces.next_action import get_space_next_action
        from navig.spaces.contracts import SpaceConfig
        from navig.spaces.progress import SpaceProgress

        space_dir = tmp_path / "devops"
        space_dir.mkdir()
        (space_dir / "CURRENT_PHASE.md").write_text("- [ ] Ship it\n", encoding="utf-8")
        (space_dir / "VISION.md").write_text("# DevOps\n", encoding="utf-8")

        fake_cfg = SpaceConfig(
            requested_name="devops", canonical_name="devops",
            path=space_dir, scope="global",
        )
        fake_progress = SpaceProgress(
            name="devops", scope="global", path=space_dir,
            goal="Own the infra", completion_pct=30.0, last_updated="2024-01-01",
        )
        with (
            patch("navig.spaces.next_action.resolve_space", return_value=fake_cfg),
            patch("navig.spaces.next_action.read_space_progress", return_value=fake_progress),
        ):
            result = get_space_next_action("devops")

        assert result is not None
        assert result.next_task == "Ship it"
        assert result.space == "devops"
        assert result.completion_pct == 30.0


class TestSelectBestNextAction:
    def test_returns_none_when_no_spaces(self, tmp_path):
        from navig.spaces.next_action import select_best_next_action
        with patch("navig.spaces.next_action.collect_spaces_progress", return_value=[]):
            result = select_best_next_action()
        assert result is None

    def test_returns_lowest_completion_with_pending_task(self, tmp_path):
        from navig.spaces.next_action import select_best_next_action
        from navig.spaces.progress import SpaceProgress

        space_dir = tmp_path / "s1"
        space_dir.mkdir()
        (space_dir / "CURRENT_PHASE.md").write_text("- [ ] next step\n", encoding="utf-8")

        rows = [
            SpaceProgress(name="high", scope="global", path=space_dir,
                          goal="goal", completion_pct=80.0, last_updated=""),
            SpaceProgress(name="low", scope="global", path=space_dir,
                          goal="goal2", completion_pct=10.0, last_updated=""),
        ]
        with (
            patch("navig.spaces.next_action.collect_spaces_progress", return_value=rows),
            patch("navig.spaces.next_action._safe_read", return_value="- [ ] next step"),
        ):
            result = select_best_next_action()

        assert result is not None
        assert result.space == "low"


# ---------------------------------------------------------------------------
# navig.deploy.history — DeployHistory
# ---------------------------------------------------------------------------

class TestDeployHistory:
    def test_append_and_read_basic(self, tmp_path):
        from navig.deploy.history import DeployHistory
        dh = DeployHistory(tmp_path)
        dh.append({"app": "myapp", "host": "prod", "status": "ok"})
        entries = dh.read()
        assert len(entries) == 1
        assert entries[0]["app"] == "myapp"

    def test_read_missing_file_returns_empty(self, tmp_path):
        from navig.deploy.history import DeployHistory
        dh = DeployHistory(tmp_path)
        assert dh.read() == []

    def test_read_newest_first(self, tmp_path):
        from navig.deploy.history import DeployHistory
        dh = DeployHistory(tmp_path)
        dh.append({"seq": 1})
        dh.append({"seq": 2})
        dh.append({"seq": 3})
        entries = dh.read()
        assert entries[0]["seq"] == 3
        assert entries[1]["seq"] == 2

    def test_read_limit(self, tmp_path):
        from navig.deploy.history import DeployHistory
        dh = DeployHistory(tmp_path)
        for i in range(10):
            dh.append({"i": i})
        entries = dh.read(limit=3)
        assert len(entries) == 3

    def test_read_filter_app(self, tmp_path):
        from navig.deploy.history import DeployHistory
        dh = DeployHistory(tmp_path)
        dh.append({"app": "foo", "host": "x"})
        dh.append({"app": "bar", "host": "y"})
        result = dh.read(app="foo")
        assert all(e["app"] == "foo" for e in result)
        assert len(result) == 1

    def test_read_filter_host(self, tmp_path):
        from navig.deploy.history import DeployHistory
        dh = DeployHistory(tmp_path)
        dh.append({"app": "a", "host": "prod"})
        dh.append({"app": "b", "host": "staging"})
        result = dh.read(host="prod")
        assert len(result) == 1
        assert result[0]["host"] == "prod"

    def test_trim_keeps_last_n(self, tmp_path):
        from navig.deploy.history import DeployHistory
        dh = DeployHistory(tmp_path, keep=3)
        for i in range(10):
            dh.append({"i": i})
        entries = dh.read(limit=100)
        assert len(entries) == 3

    def test_malformed_json_lines_skipped(self, tmp_path):
        from navig.deploy.history import DeployHistory
        dh = DeployHistory(tmp_path)
        hist_path = tmp_path / "deploy_history.jsonl"
        hist_path.write_text('{"ok": 1}\nnot-json\n{"ok": 2}\n', encoding="utf-8")
        entries = dh.read()
        assert len(entries) == 2
        assert all("ok" in e for e in entries)

    def test_blank_lines_skipped(self, tmp_path):
        from navig.deploy.history import DeployHistory
        dh = DeployHistory(tmp_path)
        hist_path = tmp_path / "deploy_history.jsonl"
        hist_path.write_text('{"ok": 1}\n\n\n{"ok": 2}\n', encoding="utf-8")
        entries = dh.read()
        assert len(entries) == 2


# ---------------------------------------------------------------------------
# navig.cli._singletons
# ---------------------------------------------------------------------------

class TestSetNoCache:
    def test_set_no_cache_true(self):
        import navig.cli._singletons as sng
        with (
            patch("navig.config.set_config_cache_bypass"),
            patch("navig.config.reset_config_manager"),
        ):
            sng.set_no_cache(True)
        assert sng._NO_CACHE is True
        sng.set_no_cache(False)  # restore

    def test_set_no_cache_false(self):
        import navig.cli._singletons as sng
        sng.set_no_cache(False)
        assert sng._NO_CACHE is False

    def test_set_no_cache_exception_silenced(self):
        import navig.cli._singletons as sng
        with patch("navig.config.set_config_cache_bypass", side_effect=RuntimeError("boom")):
            sng.set_no_cache(True)  # must not raise
        sng.set_no_cache(False)


class TestGetConfigManager:
    def test_delegates_to_config_module(self):
        from navig.cli._singletons import _get_config_manager
        fake_cm = MagicMock()
        with patch("navig.config.get_config_manager", return_value=fake_cm):
            result = _get_config_manager()
        assert result is fake_cm


class TestGetTunnelManager:
    def test_returns_class(self):
        from navig.cli._singletons import _get_tunnel_manager
        import navig.cli._singletons as sng
        sng._TunnelManager = None  # reset cache
        klass = _get_tunnel_manager()
        assert klass is not None

    def test_cached_on_second_call(self):
        from navig.cli._singletons import _get_tunnel_manager
        first = _get_tunnel_manager()
        second = _get_tunnel_manager()
        assert first is second


class TestGetRemoteOperations:
    def test_returns_class(self):
        from navig.cli._singletons import _get_remote_operations
        import navig.cli._singletons as sng
        sng._RemoteOperations = None  # reset cache
        klass = _get_remote_operations()
        assert klass is not None

    def test_cached_on_second_call(self):
        from navig.cli._singletons import _get_remote_operations
        first = _get_remote_operations()
        second = _get_remote_operations()
        assert first is second
