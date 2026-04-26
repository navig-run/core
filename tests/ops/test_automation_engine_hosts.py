"""
Tests for:
  - navig.core.automation_engine  (WorkflowStep, Workflow dataclasses; variable resolution)
  - navig.core.hosts               (HostManager cache, exists, list_hosts)

All tests are hermetic — no real SSH or network calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_host_config(config_dirs=None, accessible=True):
    """Return a mock HostConfigProvider."""
    cfg = MagicMock()
    cfg.get_config_directories.return_value = config_dirs or []
    cfg._is_directory_accessible.return_value = accessible
    cfg.verbose = False
    cfg.global_config_dir = Path("/fake/global")
    cfg.base_dir = Path("/fake/base")
    cfg.app_config_dir = None
    return cfg


# ===========================================================================
# navig.core.automation_engine — data types
# ===========================================================================

class TestWorkflowStep:
    def test_minimal_construction(self):
        from navig.core.automation_engine import WorkflowStep

        step = WorkflowStep(action="click", args={"selector": "#btn"})
        assert step.action == "click"
        assert step.args == {"selector": "#btn"}
        assert step.platform_override is None
        assert step.capture is None
        assert step.if_condition is None

    def test_full_construction(self):
        from navig.core.automation_engine import WorkflowStep

        step = WorkflowStep(
            action="type",
            args={"text": "hello"},
            platform_override={"windows": {"action": "type_win"}},
            capture="output",
            if_condition="platform == 'windows'",
        )
        assert step.action == "type"
        assert step.platform_override is not None
        assert step.capture == "output"
        assert step.if_condition == "platform == 'windows'"

    def test_args_default_empty_dict(self):
        from navig.core.automation_engine import WorkflowStep

        # The dataclass has no default for args, so check it works with empty dict
        step = WorkflowStep(action="wait", args={})
        assert step.args == {}


class TestWorkflow:
    def test_minimal_construction(self):
        from navig.core.automation_engine import Workflow

        wf = Workflow(name="mywf", steps=[])
        assert wf.name == "mywf"
        assert wf.steps == []
        assert wf.description == ""
        assert wf.variables is None

    def test_with_steps(self):
        from navig.core.automation_engine import Workflow, WorkflowStep

        steps = [WorkflowStep(action="open", args={"url": "https://example.com"})]
        wf = Workflow(name="browse", steps=steps, description="Browse example")
        assert len(wf.steps) == 1
        assert wf.steps[0].action == "open"

    def test_with_variables(self):
        from navig.core.automation_engine import Workflow

        wf = Workflow(name="mywf", steps=[], variables={"host": "server", "port": "22"})
        assert wf.variables["host"] == "server"
        assert wf.variables["port"] == "22"

    def test_name_is_string(self):
        from navig.core.automation_engine import Workflow

        wf = Workflow(name="test", steps=[])
        assert isinstance(wf.name, str)


class TestWorkflowEngineVariableSubstitution:
    """Test the variable substitution in execute_workflow via a patched adapter."""

    def _engine(self):
        from navig.core.automation_engine import WorkflowEngine

        eng = WorkflowEngine.__new__(WorkflowEngine)
        eng._navig_root = Path("/fake")
        eng._workflows_dir = Path("/fake/workflows")
        eng._workflow_cache = {}
        eng._ahk = None
        eng._linux = None
        eng._macos = None
        return eng

    def test_variable_resolved_in_step_args(self):
        from navig.core.automation_engine import Workflow, WorkflowStep

        eng = self._engine()

        results = []

        def fake_execute(action, args):
            results.append((action, args))

        eng._execute_action = fake_execute

        step = WorkflowStep(action="type", args={"text": "{{greeting}}"})
        wf = Workflow(name="test", steps=[step], variables={"greeting": "hello"})
        eng.execute_workflow(wf)

        assert results[0][1]["text"] == "hello"

    def test_runtime_variables_override_workflow_defaults(self):
        from navig.core.automation_engine import Workflow, WorkflowStep

        eng = self._engine()
        results = []
        eng._execute_action = lambda a, args: results.append((a, args))

        step = WorkflowStep(action="type", args={"text": "{{name}}"})
        wf = Workflow(name="test", steps=[step], variables={"name": "default"})
        eng.execute_workflow(wf, variables={"name": "override"})

        assert results[0][1]["text"] == "override"

    def test_step_without_variable_template_unchanged(self):
        from navig.core.automation_engine import Workflow, WorkflowStep

        eng = self._engine()
        results = []
        eng._execute_action = lambda a, args: results.append((a, args))

        step = WorkflowStep(action="click", args={"id": "btn"})
        wf = Workflow(name="test", steps=[step])
        eng.execute_workflow(wf)

        assert results[0][1]["id"] == "btn"


# ===========================================================================
# navig.core.hosts — HostManager
# ===========================================================================

class TestHostManagerInvalidateCache:
    def test_invalidate_all(self):
        from navig.core.hosts import HostManager

        cfg = _mock_host_config()
        mgr = HostManager(cfg)
        mgr._host_config_cache = {"host1": {"hostname": "h1"}, "host2": {"hostname": "h2"}}
        mgr._hosts_list_cache = (["host1", "host2"], (1.0, 2))

        mgr.invalidate_cache()

        assert mgr._host_config_cache == {}
        assert mgr._hosts_list_cache is None

    def test_invalidate_specific_host(self):
        from navig.core.hosts import HostManager

        cfg = _mock_host_config()
        mgr = HostManager(cfg)
        mgr._host_config_cache = {"host1": {}, "host2": {}}

        mgr.invalidate_cache("host1")

        assert "host1" not in mgr._host_config_cache
        assert "host2" in mgr._host_config_cache

    def test_invalidate_nonexistent_host_no_error(self):
        from navig.core.hosts import HostManager

        cfg = _mock_host_config()
        mgr = HostManager(cfg)
        mgr._host_config_cache = {}
        # Should not raise
        mgr.invalidate_cache("ghost")


class TestHostManagerExists:
    def test_false_when_no_config_dirs(self):
        from navig.core.hosts import HostManager

        cfg = _mock_host_config(config_dirs=[])
        mgr = HostManager(cfg)
        assert mgr.exists("myhost") is False

    def test_true_via_new_format_hosts_dir(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        (hosts_dir / "myhost.yaml").write_text("hostname: 1.2.3.4\n", encoding="utf-8")

        cfg = _mock_host_config(config_dirs=[tmp_path])
        mgr = HostManager(cfg)
        assert mgr.exists("myhost") is True

    def test_true_via_legacy_apps_dir(self, tmp_path):
        from navig.core.hosts import HostManager

        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()
        (apps_dir / "myhost.yaml").write_text("hostname: 1.2.3.4\n", encoding="utf-8")

        cfg = _mock_host_config(config_dirs=[tmp_path])
        mgr = HostManager(cfg)
        assert mgr.exists("myhost") is True

    def test_false_when_file_not_present(self, tmp_path):
        from navig.core.hosts import HostManager

        (tmp_path / "hosts").mkdir()
        cfg = _mock_host_config(config_dirs=[tmp_path])
        mgr = HostManager(cfg)
        assert mgr.exists("ghost") is False


class TestHostManagerListHosts:
    def test_empty_when_no_config_dirs(self):
        from navig.core.hosts import HostManager

        cfg = _mock_host_config(config_dirs=[])
        mgr = HostManager(cfg)
        assert mgr.list_hosts() == []

    def test_discovers_hosts_from_new_format(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        for name in ["alpha", "beta", "gamma"]:
            (hosts_dir / f"{name}.yaml").write_text(f"hostname: {name}\n", encoding="utf-8")

        cfg = _mock_host_config(config_dirs=[tmp_path])
        mgr = HostManager(cfg)
        result = mgr.list_hosts()
        assert sorted(result) == ["alpha", "beta", "gamma"]

    def test_result_is_sorted(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        for name in ["zebra", "alpha", "mango"]:
            (hosts_dir / f"{name}.yaml").write_text("hostname: x\n", encoding="utf-8")

        cfg = _mock_host_config(config_dirs=[tmp_path])
        mgr = HostManager(cfg)
        result = mgr.list_hosts()
        assert result == sorted(result)

    def test_caches_result_on_second_call(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        (hosts_dir / "host1.yaml").write_text("hostname: h1\n", encoding="utf-8")

        cfg = _mock_host_config(config_dirs=[tmp_path])
        mgr = HostManager(cfg)

        first = mgr.list_hosts()
        second = mgr.list_hosts()
        assert first == second
        assert mgr._hosts_list_cache is not None

    def test_cache_invalidated_after_invalidate_call(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        (hosts_dir / "host1.yaml").write_text("hostname: h1\n", encoding="utf-8")

        cfg = _mock_host_config(config_dirs=[tmp_path])
        mgr = HostManager(cfg)
        _ = mgr.list_hosts()
        assert mgr._hosts_list_cache is not None

        mgr.invalidate_cache()
        assert mgr._hosts_list_cache is None

    def test_inaccessible_dir_skipped(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        (hosts_dir / "myhost.yaml").write_text("hostname: x\n", encoding="utf-8")

        cfg = _mock_host_config(config_dirs=[tmp_path], accessible=False)
        mgr = HostManager(cfg)
        result = mgr.list_hosts()
        # With inaccessible dir, the host should NOT appear
        assert "myhost" not in result
