"""Batch 69 — update/targets, update/history, core/evolution/failure_summary."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.update.targets — UpdateTarget, TargetResolver
# ---------------------------------------------------------------------------

class TestUpdateTarget:
    def test_is_local_true(self):
        from navig.update.targets import UpdateTarget
        t = UpdateTarget(node_id="local", type="local")
        assert t.is_local is True

    def test_is_local_false_for_ssh(self):
        from navig.update.targets import UpdateTarget
        t = UpdateTarget(node_id="prod", type="ssh")
        assert t.is_local is False

    def test_label_returns_node_id(self):
        from navig.update.targets import UpdateTarget
        t = UpdateTarget(node_id="staging")
        assert t.label == "staging"

    def test_default_type_is_local(self):
        from navig.update.targets import UpdateTarget
        t = UpdateTarget(node_id="x")
        assert t.type == "local"


class TestTargetResolver:
    def _make_resolver(self, list_hosts=None):
        mock_cm = MagicMock()
        mock_cm.list_hosts.return_value = list_hosts or []
        from navig.update.targets import TargetResolver
        return TargetResolver(config_manager=mock_cm)

    def test_no_args_returns_local(self):
        resolver = self._make_resolver()
        targets = resolver.resolve()
        assert len(targets) == 1
        assert targets[0].node_id == "local"
        assert targets[0].is_local

    def test_host_local_returns_local(self):
        resolver = self._make_resolver()
        targets = resolver.resolve(host="local")
        assert targets[0].is_local

    def test_host_localhost_returns_local(self):
        resolver = self._make_resolver()
        targets = resolver.resolve(host="localhost")
        assert targets[0].is_local

    def test_all_hosts_includes_local_first(self):
        resolver = self._make_resolver(list_hosts=["prod"])
        resolver._cm.load_host_config.return_value = {"hostname": "prod.example.com"}
        targets = resolver.resolve(all_hosts=True)
        assert targets[0].node_id == "local"

    def test_all_hosts_includes_remote_hosts(self):
        resolver = self._make_resolver(list_hosts=["prod"])
        resolver._cm.load_host_config.return_value = {"hostname": "prod.example.com"}
        targets = resolver.resolve(all_hosts=True)
        node_ids = [t.node_id for t in targets]
        assert "prod" in node_ids

    def test_unknown_host_raises_value_error(self):
        resolver = self._make_resolver()
        resolver._cm.load_host_config.return_value = None
        with pytest.raises(ValueError, match="not found"):
            resolver.resolve(host="unknown-host")

    def test_group_raises_on_unknown_group(self):
        resolver = self._make_resolver()
        resolver._cm.get_group_hosts.side_effect = Exception("Group not found")
        with pytest.raises(ValueError):
            resolver.resolve(group="nonexistent-group")


# ---------------------------------------------------------------------------
# navig.update.history — UpdateHistory
# ---------------------------------------------------------------------------

class TestUpdateHistory:
    def test_append_and_read(self, tmp_path):
        from navig.update.history import UpdateHistory
        h = UpdateHistory(cache_dir=str(tmp_path))
        h.append({"node_id": "local", "status": "ok"})
        entries = h.read()
        assert len(entries) == 1
        assert entries[0]["status"] == "ok"

    def test_read_missing_file_returns_empty(self, tmp_path):
        from navig.update.history import UpdateHistory
        h = UpdateHistory(cache_dir=str(tmp_path))
        assert h.read() == []

    def test_read_newest_first(self, tmp_path):
        from navig.update.history import UpdateHistory
        h = UpdateHistory(cache_dir=str(tmp_path))
        for i in range(3):
            h.append({"seq": i})
        entries = h.read()
        assert entries[0]["seq"] == 2

    def test_read_filter_by_node_id(self, tmp_path):
        from navig.update.history import UpdateHistory
        h = UpdateHistory(cache_dir=str(tmp_path))
        h.append({"node_id": "local", "status": "ok"})
        h.append({"node_id": "prod", "status": "ok"})
        entries = h.read(node_id="local")
        assert all(e["node_id"] == "local" for e in entries)

    def test_read_filter_by_host(self, tmp_path):
        from navig.update.history import UpdateHistory
        h = UpdateHistory(cache_dir=str(tmp_path))
        h.append({"node_id": "prod", "status": "ok"})
        h.append({"node_id": "dev", "status": "ok"})
        entries = h.read(host="prod")
        assert len(entries) == 1

    def test_read_limit(self, tmp_path):
        from navig.update.history import UpdateHistory
        h = UpdateHistory(cache_dir=str(tmp_path))
        for i in range(10):
            h.append({"i": i})
        entries = h.read(limit=3)
        assert len(entries) == 3

    def test_clear_removes_file(self, tmp_path):
        from navig.update.history import UpdateHistory
        h = UpdateHistory(cache_dir=str(tmp_path))
        h.append({"data": 1})
        h.clear()
        assert h.read() == []

    def test_keep_limits_stored_entries(self, tmp_path):
        from navig.update.history import UpdateHistory
        h = UpdateHistory(cache_dir=str(tmp_path), keep=3)
        for i in range(10):
            h.append({"i": i})
        entries = h.read(limit=100)
        assert len(entries) == 3


# ---------------------------------------------------------------------------
# navig.core.evolution.failure_summary — summarize_check_failure
# ---------------------------------------------------------------------------

class TestSummarizeCheckFailure:
    def _call(self, stdout="", stderr=""):
        from navig.core.evolution.failure_summary import summarize_check_failure
        return summarize_check_failure(stdout, stderr)

    def test_empty_returns_empty_string(self):
        assert self._call() == ""

    def test_none_inputs_return_empty(self):
        assert self._call(None, None) == ""

    def test_failed_tests_count_from_pytest_line(self):
        result = self._call("3 failed, 10 passed", "")
        assert "Failed tests: 3" in result

    def test_failed_tests_list_from_failed_lines(self):
        stdout = "FAILED tests/test_foo.py::bar\nFAILED tests/test_baz.py::qux"
        result = self._call(stdout, "")
        assert "test_foo" in result or "First failing targets" in result

    def test_traceback_error_included(self):
        result = self._call("", "E AssertionError: expected True")
        assert "AssertionError" in result

    def test_fallback_message_when_no_specific_data(self):
        result = self._call("some random output", "")
        assert len(result) > 0

    def test_suggested_next_step_always_present(self):
        result = self._call("1 failed", "")
        assert "Suggested next step" in result

    def test_max_three_failing_targets_shown(self):
        lines = "\n".join(f"FAILED tests/test_m{i}.py::fn" for i in range(10))
        result = self._call(lines, "")
        # Should only show up to 3
        count = result.count("test_m")
        assert count <= 3
