"""Tests for navig.agent.tools.introspection_tools (Mission B 4c).

Hermetic: the module registry is faked (never touches ~/.navig); plugin
inspect is exercised on its honest-failure path plus registration/exposure
classification.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from navig.agent.tools.introspection_tools import ModuleListTool, PluginInspectTool


def _run(coro):
    return asyncio.run(coro)


class _FakeRegistry:
    def __init__(self):
        self._modules = {
            "goal": SimpleNamespace(
                id="goal",
                label="Goal",
                description="session goals",
                kind=SimpleNamespace(value="app"),
                category="agent",
                source="plugin",
                capability=None,
                min_tier=None,
                surfaces=["cli:goal"],
                requires=[],
            )
        }

    def discover(self):
        return self

    def get(self, module_id):
        return self._modules.get(module_id)

    def list_modules(self, include_dev=True):
        return [
            {"id": "goal", "kind": "app", "label": "Goal", "locked": False},
            {"id": "other", "kind": "tool", "label": "Other", "locked": True},
        ]

    def is_enabled(self, module_id):
        return module_id in self._modules


@pytest.fixture()
def fake_module_registry(monkeypatch):
    import navig.modules.registry as reg_mod

    monkeypatch.setattr(reg_mod, "get_registry", lambda: _FakeRegistry())


class TestModuleList:
    def test_lists_modules(self, fake_module_registry):
        result = _run(ModuleListTool().run({}))
        assert result.success is True
        payload = json.loads(result.output)
        assert payload["total"] == 2
        assert payload["modules"][0]["id"] == "goal"

    def test_single_module_detail(self, fake_module_registry):
        result = _run(ModuleListTool().run({"module": "goal"}))
        assert result.success is True
        payload = json.loads(result.output)
        assert payload["enabled"] is True
        assert payload["surfaces"] == ["cli:goal"]

    def test_unknown_module_honest(self, fake_module_registry):
        result = _run(ModuleListTool().run({"module": "ghost"}))
        assert result.success is False
        assert "Unknown module" in result.error

    def test_broken_registry_reports(self, monkeypatch):
        import navig.modules.registry as reg_mod

        def _boom():
            raise RuntimeError("registry unreadable")

        monkeypatch.setattr(reg_mod, "get_registry", _boom)
        result = _run(ModuleListTool().run({}))
        assert result.success is False
        assert "registry unreadable" in result.error


class TestPluginInspect:
    def test_missing_path_refused(self):
        result = _run(PluginInspectTool().run({}))
        assert result.success is False
        assert "path" in result.error

    def test_invalid_package_reports_honestly(self, tmp_path):
        """load_package degrades (never fatals) — the report must SAY so."""
        (tmp_path / "not-a-plugin").mkdir()
        result = _run(
            PluginInspectTool().run({"path": str(tmp_path / "not-a-plugin")})
        )
        assert result.success is True, "degraded packages are reported, not raised"
        assert "plugin.json" in result.output, (
            "the missing-manifest condition must be visible in the report: "
            f"{result.output!r}"
        )


class TestRegistrationAndExposure:
    def test_registered_under_introspection_toolset(self):
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools import register_all_tools

        register_all_tools()
        for name in ("plugin_inspect", "module_list"):
            entry = _AGENT_REGISTRY.get_entry(name)
            assert entry is not None
            assert entry.toolset == "introspection"

    def test_toolset_table_and_classifications(self):
        from navig.agent.plan_mode import PlanInterceptor
        from navig.agent.toolsets import (
            PARALLEL_SAFE_TOOLS,
            TOOLSETS,
            is_parallel_safe,
        )
        from navig.tools.approval import DESTRUCTIVE_TOOLS, is_destructive_tool

        assert TOOLSETS["introspection"] == ["plugin_inspect", "module_list"]
        assert "plugin_inspect" in PARALLEL_SAFE_TOOLS
        assert "module_list" in PARALLEL_SAFE_TOOLS
        assert is_parallel_safe("plugin_inspect") is True
        for name in ("plugin_inspect", "module_list"):
            assert name not in DESTRUCTIVE_TOOLS
            assert is_destructive_tool(name) is False
            assert name in PlanInterceptor.READ_ONLY_TOOLS
