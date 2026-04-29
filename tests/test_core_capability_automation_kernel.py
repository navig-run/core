"""
Batch 90 — tests for:
  navig/core/capability_registry.py   (registry helpers, is_enabled)
  navig/core/automation_engine.py     (WorkflowEngine, WorkflowStep, execute_action)
  navig/core/kernel.py                (NavigKernel bootstrap, resolve_intent, parse helpers)
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  capability_registry.py                                                 ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestCapabilityTier:
    def test_tier_values(self):
        from navig.core.capability_registry import CapabilityTier

        assert CapabilityTier.CORE == "core"
        assert CapabilityTier.OPTIONAL == "optional"
        assert CapabilityTier.LABS == "labs"


class TestRegistryContent:
    def test_registry_has_core_entries(self):
        from navig.core.capability_registry import REGISTRY, CapabilityTier

        cores = [k for k, v in REGISTRY.items() if v.tier == CapabilityTier.CORE]
        assert len(cores) >= 5
        assert "vault" in REGISTRY
        assert "telegram" in REGISTRY

    def test_registry_has_optional_entries(self):
        from navig.core.capability_registry import REGISTRY, CapabilityTier

        opts = [k for k, v in REGISTRY.items() if v.tier == CapabilityTier.OPTIONAL]
        assert len(opts) >= 3

    def test_registry_has_labs_entries(self):
        from navig.core.capability_registry import REGISTRY, CapabilityTier

        labs = [k for k, v in REGISTRY.items() if v.tier == CapabilityTier.LABS]
        assert len(labs) >= 1

    def test_each_entry_has_tier(self):
        from navig.core.capability_registry import REGISTRY

        for name, entry in REGISTRY.items():
            assert entry.tier is not None, f"{name} missing tier"


class TestGetTier:
    def test_known_core_capability(self):
        from navig.core.capability_registry import CapabilityTier, get_tier

        assert get_tier("vault") == CapabilityTier.CORE

    def test_known_optional_capability(self):
        from navig.core.capability_registry import CapabilityTier, get_tier

        assert get_tier("matrix") == CapabilityTier.OPTIONAL

    def test_unknown_capability_returns_none(self):
        from navig.core.capability_registry import get_tier

        assert get_tier("nonexistent_xyz") is None


class TestGetByTier:
    def test_get_core_returns_only_core(self):
        from navig.core.capability_registry import CapabilityTier, get_core

        result = get_core()
        assert all(v.tier == CapabilityTier.CORE for v in result.values())
        assert len(result) >= 5

    def test_get_optional_returns_only_optional(self):
        from navig.core.capability_registry import CapabilityTier, get_optional

        result = get_optional()
        assert all(v.tier == CapabilityTier.OPTIONAL for v in result.values())

    def test_get_labs_returns_only_labs(self):
        from navig.core.capability_registry import CapabilityTier, get_labs

        result = get_labs()
        assert all(v.tier == CapabilityTier.LABS for v in result.values())


class TestIsEnabled:
    def test_core_always_enabled(self):
        from navig.core.capability_registry import is_enabled

        assert is_enabled("vault") is True
        assert is_enabled("telegram") is True
        assert is_enabled("memory") is True

    def test_optional_disabled_without_config(self):
        from navig.core.capability_registry import is_enabled

        assert is_enabled("matrix") is False
        assert is_enabled("mesh") is False

    def test_optional_enabled_with_config_key_true(self):
        from navig.core.capability_registry import is_enabled

        config = {"matrix": {"enabled": True}}
        assert is_enabled("matrix", config) is True

    def test_optional_disabled_when_config_key_false(self):
        from navig.core.capability_registry import is_enabled

        config = {"matrix": {"enabled": False}}
        assert is_enabled("matrix", config) is False

    def test_unknown_capability_is_false(self):
        from navig.core.capability_registry import is_enabled

        assert is_enabled("foobar_nonexistent") is False

    def test_optional_no_config_key_returns_false(self):
        """A capability with config_key=None is off even if tier==OPTIONAL."""
        from navig.core.capability_registry import REGISTRY, CapabilityEntry, CapabilityTier, is_enabled

        # genesis_lab has no config_key
        entry = REGISTRY.get("genesis_lab")
        if entry and entry.config_key is None and entry.tier != CapabilityTier.CORE:
            assert is_enabled("genesis_lab", {"some": "config"}) is False

    def test_nested_config_key_walk(self):
        from navig.core.capability_registry import is_enabled

        config = {"mesh": {"enabled": True}}
        assert is_enabled("mesh", config) is True


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  automation_engine.py                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestWorkflowStep:
    def test_defaults(self):
        from navig.core.automation_engine import WorkflowStep

        step = WorkflowStep(action="wait", args={"seconds": 1})
        assert step.platform_override is None
        assert step.capture is None
        assert step.if_condition is None


class TestWorkflowEngineLoadWorkflow:
    def test_returns_none_when_file_missing(self, tmp_path):
        from navig.core.automation_engine import WorkflowEngine

        engine = WorkflowEngine()
        engine._workflows_dir = tmp_path
        engine._workflow_cache = {}
        with patch("navig.platform.paths.config_dir", return_value=tmp_path):
            result = engine.load_workflow("nonexistent")
        assert result is None

    def test_loads_workflow_from_yaml(self, tmp_path):
        from navig.core.automation_engine import WorkflowEngine

        engine = WorkflowEngine()
        engine._workflows_dir = tmp_path
        engine._workflow_cache = {}

        data = {
            "name": "test_wf",
            "description": "A test workflow",
            "variables": {"env": "dev"},
            "steps": [
                {"action": "wait", "args": {"seconds": 0.0}}
            ],
        }
        wf_file = tmp_path / "test_wf.yaml"
        wf_file.write_text(yaml.dump(data), encoding="utf-8")

        wf = engine.load_workflow("test_wf")
        assert wf is not None
        assert wf.name == "test_wf"
        assert len(wf.steps) == 1
        assert wf.steps[0].action == "wait"

    def test_caches_workflow_on_second_load(self, tmp_path):
        from navig.core.automation_engine import WorkflowEngine

        engine = WorkflowEngine()
        engine._workflows_dir = tmp_path
        engine._workflow_cache = {}

        data = {"name": "cached", "steps": []}
        wf_file = tmp_path / "cached.yaml"
        wf_file.write_text(yaml.dump(data), encoding="utf-8")

        wf1 = engine.load_workflow("cached")
        wf2 = engine.load_workflow("cached")
        assert wf1 is wf2  # same cached object


class TestWorkflowEngineExecuteAction:
    def _engine(self):
        from navig.core.automation_engine import WorkflowEngine

        e = WorkflowEngine()
        e._workflows_dir = Path(tempfile.mkdtemp())
        return e

    def test_action_wait(self):
        with patch("time.sleep") as mock_sleep:
            engine = self._engine()
            result = engine._execute_action("wait", {"seconds": "2.5"})
            mock_sleep.assert_called_once_with(2.5)
            assert result is True

    def test_action_run_command_success(self):
        engine = self._engine()
        result = engine._execute_action("run_command", {"command": "echo hello"})
        # On windows 'echo' might include trailing spaces
        assert isinstance(result, str)

    def test_action_unknown_returns_none_when_no_adapter(self):
        engine = self._engine()
        # With no adapter on the current platform (mocked out)
        with patch.object(type(engine), "adapter", new_callable=lambda: property(lambda self: None)):
            result = engine._execute_action("unknown_action", {})
            assert result is None

    def test_action_wait_default_seconds(self):
        with patch("time.sleep") as mock_sleep:
            engine = self._engine()
            engine._execute_action("wait", {})
            mock_sleep.assert_called_once_with(1.0)


class TestWorkflowEngineExecuteWorkflow:
    def _engine_and_wf(self):
        from navig.core.automation_engine import Workflow, WorkflowEngine, WorkflowStep

        engine = WorkflowEngine()
        engine._workflows_dir = Path(tempfile.mkdtemp())
        wf = Workflow(
            name="test",
            steps=[WorkflowStep(action="wait", args={"seconds": "0"})],
            description="",
            variables={"x": "1"},
        )
        return engine, wf

    def test_returns_updated_vars(self):
        with patch("time.sleep"):
            engine, wf = self._engine_and_wf()
            result = engine.execute_workflow(wf, {"extra": "yes"})
        assert result["x"] == "1"
        assert result["extra"] == "yes"

    def test_variable_substitution_in_args(self):
        from navig.core.automation_engine import Workflow, WorkflowEngine, WorkflowStep

        calls = []

        engine = WorkflowEngine()
        engine._workflows_dir = Path(tempfile.mkdtemp())

        original_exec = engine._execute_action

        def capturing_exec(action, args):
            calls.append((action, args))
            return True

        engine._execute_action = capturing_exec

        wf = Workflow(
            name="var_test",
            steps=[WorkflowStep(action="run_command", args={"command": "echo {{env}}"})],
            variables={"env": "production"},
        )
        engine.execute_workflow(wf, {})
        assert calls[0][1]["command"] == "echo production"

    def test_condition_false_skips_step(self):
        from navig.core.automation_engine import Workflow, WorkflowEngine, WorkflowStep

        calls = []

        engine = WorkflowEngine()
        engine._workflows_dir = Path(tempfile.mkdtemp())
        engine._execute_action = lambda a, b: calls.append(a) or True

        wf = Workflow(
            name="cond_test",
            steps=[WorkflowStep(action="wait", args={}, if_condition="False")],
        )
        engine.execute_workflow(wf)
        assert calls == []

    def test_capture_stores_result_in_vars(self):
        from navig.core.automation_engine import Workflow, WorkflowEngine, WorkflowStep

        engine = WorkflowEngine()
        engine._workflows_dir = Path(tempfile.mkdtemp())
        engine._execute_action = lambda a, b: "captured_value"

        wf = Workflow(
            name="capture_test",
            steps=[WorkflowStep(action="run_command", args={}, capture="my_var")],
        )
        result = engine.execute_workflow(wf)
        assert result["my_var"] == "captured_value"


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  kernel.py                                                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestNavigKernelInit:
    def test_init_creates_instance(self, tmp_path):
        from navig.core.kernel import NavigKernel

        k = NavigKernel(str(tmp_path))
        assert k.root_path == str(tmp_path)
        assert k.skills == {}
        assert k.commands == {}
        assert k.packs == {}

    def test_bootstrap_runs_without_error(self, tmp_path):
        from navig.core.kernel import NavigKernel

        k = NavigKernel(str(tmp_path))
        k.bootstrap()  # no skills/packs — should complete silently


class TestNavigKernelParseMemoryParams:
    def _kernel(self, tmp_path):
        from navig.core.kernel import NavigKernel

        return NavigKernel(str(tmp_path))

    def test_recall_with_args(self, tmp_path):
        k = self._kernel(tmp_path)
        params = k._parse_memory_params("recall", ["what", "is", "x"])
        assert params == {"query": "what is x"}

    def test_recall_empty_args(self, tmp_path):
        k = self._kernel(tmp_path)
        params = k._parse_memory_params("recall", [])
        assert params == {}

    def test_remember_content(self, tmp_path):
        k = self._kernel(tmp_path)
        params = k._parse_memory_params("remember", ["hello", "world"])
        assert params["content"] == "hello world"
        assert "tags" not in params

    def test_remember_with_type_flag(self, tmp_path):
        k = self._kernel(tmp_path)
        params = k._parse_memory_params("remember", ["note", "--type", "fact"])
        assert params["content"] == "note"
        assert "fact" in params["tags"]

    def test_checkpoint_returns_root_path(self, tmp_path):
        k = self._kernel(tmp_path)
        params = k._parse_memory_params("checkpoint", [])
        assert params == {"root_path": str(tmp_path)}

    def test_unknown_method_returns_empty(self, tmp_path):
        k = self._kernel(tmp_path)
        params = k._parse_memory_params("unknown_method", ["a", "b"])
        assert params == {}


class TestNavigKernelResolveIntent:
    def _kernel_with_skill(self, tmp_path):
        from navig.core.kernel import NavigKernel
        from navig.core.models import NavigCommand, SkillManifest

        k = NavigKernel(str(tmp_path))
        cmd = NavigCommand(
            name="deploy",
            syntax="navig deploy run",
            description="Deploy application to server",
        )
        cmd.source_skill = "deploy-skill"
        k.commands["deploy"] = cmd
        return k

    def test_resolve_known_command_by_syntax(self, tmp_path):
        k = self._kernel_with_skill(tmp_path)
        result = k.resolve_intent("navig deploy run my-app")
        assert result is not None

    def test_resolve_returns_none_for_no_match(self, tmp_path):
        from navig.core.kernel import NavigKernel

        k = NavigKernel(str(tmp_path))
        result = k.resolve_intent("something completely unrelated xyz")
        assert result is None


class TestNavigKernelLoadSkillFile:
    def test_parses_valid_skill_file(self, tmp_path):
        from navig.core.kernel import NavigKernel

        k = NavigKernel(str(tmp_path))

        skill_content = """\
---
name: test-skill
description: A test skill
navig_commands: []
examples: []
---
# Test Skill
"""
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(skill_content, encoding="utf-8")

        k._parse_skill_file(str(skill_file))
        assert "test-skill" in k.skills

    def test_ignores_file_without_frontmatter(self, tmp_path):
        from navig.core.kernel import NavigKernel

        k = NavigKernel(str(tmp_path))

        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("# No frontmatter here\n", encoding="utf-8")

        k._parse_skill_file(str(skill_file))
        assert k.skills == {}

    def test_handles_malformed_skill_file(self, tmp_path):
        from navig.core.kernel import NavigKernel

        k = NavigKernel(str(tmp_path))

        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("---\nbad: yaml: [\n---\n", encoding="utf-8")

        # Should not raise — logs debug
        k._parse_skill_file(str(skill_file))
