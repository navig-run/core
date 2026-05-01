"""Batch 68 — hooks/events, formations/registry, ui/formatters."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.hooks.events — HookEvent, HookContext, HookResult
# ---------------------------------------------------------------------------

class TestHookEvent:
    def test_pre_tool_use_value(self):
        from navig.hooks.events import HookEvent
        assert HookEvent.PRE_TOOL_USE.value == "PreToolUse"

    def test_post_tool_use_value(self):
        from navig.hooks.events import HookEvent
        assert HookEvent.POST_TOOL_USE.value == "PostToolUse"

    def test_is_str_enum(self):
        from navig.hooks.events import HookEvent
        assert isinstance(HookEvent.NOTIFICATION, str)


class TestHookContext:
    def test_to_json_contains_event_value(self):
        from navig.hooks.events import HookContext, HookEvent
        ctx = HookContext(event=HookEvent.PRE_TOOL_USE, tool_name="bash")
        data = json.loads(ctx.to_json())
        assert data["event"] == "PreToolUse"

    def test_to_json_includes_tool_name(self):
        from navig.hooks.events import HookContext, HookEvent
        ctx = HookContext(event=HookEvent.POST_TOOL_USE, tool_name="read_file")
        data = json.loads(ctx.to_json())
        assert data["tool_name"] == "read_file"

    def test_to_json_omits_tool_result_when_none(self):
        from navig.hooks.events import HookContext, HookEvent
        ctx = HookContext(event=HookEvent.NOTIFICATION)
        data = json.loads(ctx.to_json())
        assert "tool_result" not in data

    def test_to_json_includes_tool_result_when_set(self):
        from navig.hooks.events import HookContext, HookEvent
        ctx = HookContext(event=HookEvent.POST_TOOL_USE, tool_result={"output": "ok"})
        data = json.loads(ctx.to_json())
        assert data["tool_result"] == {"output": "ok"}

    def test_to_json_includes_tool_error_when_set(self):
        from navig.hooks.events import HookContext, HookEvent
        ctx = HookContext(event=HookEvent.POST_TOOL_USE_FAILURE, tool_error="boom")
        data = json.loads(ctx.to_json())
        assert data["tool_error"] == "boom"

    def test_to_json_omits_metadata_when_empty(self):
        from navig.hooks.events import HookContext, HookEvent
        ctx = HookContext(event=HookEvent.NOTIFICATION)
        data = json.loads(ctx.to_json())
        assert "metadata" not in data

    def test_to_json_includes_metadata_when_set(self):
        from navig.hooks.events import HookContext, HookEvent
        ctx = HookContext(event=HookEvent.NOTIFICATION, metadata={"key": "val"})
        data = json.loads(ctx.to_json())
        assert data["metadata"] == {"key": "val"}

    def test_to_json_includes_session_and_turn_id(self):
        from navig.hooks.events import HookContext, HookEvent
        ctx = HookContext(event=HookEvent.SESSION_START, session_id="s1", turn_id="t1")
        data = json.loads(ctx.to_json())
        assert data["session_id"] == "s1"
        assert data["turn_id"] == "t1"


class TestHookResult:
    def test_defaults(self):
        from navig.hooks.events import HookResult
        r = HookResult()
        assert r.block is False
        assert r.message == ""
        assert r.executed is False
        assert r.retry is False

    def test_block_true(self):
        from navig.hooks.events import HookResult
        r = HookResult(block=True, message="denied")
        assert r.block is True
        assert r.message == "denied"


# ---------------------------------------------------------------------------
# navig.formations.registry — FormationRegistry singleton
# ---------------------------------------------------------------------------

class TestFormationRegistry:
    def setup_method(self):
        # Reset singleton before each test
        from navig.formations.registry import FormationRegistry
        FormationRegistry._instance = None

    def test_get_instance_returns_same_object(self):
        from navig.formations.registry import FormationRegistry
        a = FormationRegistry.get_instance()
        b = FormationRegistry.get_instance()
        assert a is b

    def test_not_initialized_initially(self):
        from navig.formations.registry import FormationRegistry
        reg = FormationRegistry.get_instance()
        assert reg._initialized is False

    def test_initialize_sets_flag(self, tmp_path):
        from navig.formations.registry import FormationRegistry
        reg = FormationRegistry.get_instance()
        with (
            patch("navig.formations.registry.discover_formations", return_value={}),
            patch("navig.formations.registry.get_active_formation", return_value=None),
        ):
            reg.initialize(tmp_path)
        assert reg._initialized is True

    def test_initialize_idempotent(self, tmp_path):
        from navig.formations.registry import FormationRegistry
        reg = FormationRegistry.get_instance()
        with (
            patch("navig.formations.registry.discover_formations", return_value={}) as mock_discover,
            patch("navig.formations.registry.get_active_formation", return_value=None),
        ):
            reg.initialize(tmp_path)
            reg.initialize(tmp_path)  # second call
        # discover called only once
        assert mock_discover.call_count == 1

    def test_get_active_returns_loaded_formation(self, tmp_path):
        from navig.formations.registry import FormationRegistry
        fake_formation = MagicMock()
        fake_formation.name = "devops"
        reg = FormationRegistry.get_instance()
        with (
            patch("navig.formations.registry.discover_formations", return_value={}),
            patch("navig.formations.registry.get_active_formation", return_value=fake_formation),
        ):
            reg.initialize(tmp_path)
        assert reg.get_active() is fake_formation

    def test_get_formation_map_returns_dict(self, tmp_path):
        from navig.formations.registry import FormationRegistry
        fake_map = {"devops": tmp_path / "devops"}
        reg = FormationRegistry.get_instance()
        with (
            patch("navig.formations.registry.discover_formations", return_value=fake_map),
            patch("navig.formations.registry.get_active_formation", return_value=None),
        ):
            reg.initialize(tmp_path)
        assert reg.get_formation_map() == fake_map

    def test_reload_forces_reinitialize(self, tmp_path):
        from navig.formations.registry import FormationRegistry
        reg = FormationRegistry.get_instance()
        with (
            patch("navig.formations.registry.discover_formations", return_value={}) as mock_d,
            patch("navig.formations.registry.get_active_formation", return_value=None),
        ):
            reg.initialize(tmp_path)
            reg.reload(tmp_path)
        assert mock_d.call_count == 2

    def test_get_registry_returns_same_instance(self):
        from navig.formations.registry import get_registry, FormationRegistry
        reg = get_registry()
        assert reg is FormationRegistry.get_instance()


# ---------------------------------------------------------------------------
# navig.ui.formatters — render_kv_diagnostics, render_command_row, render_section_divider
# ---------------------------------------------------------------------------

class TestRenderKvDiagnostics:
    def test_does_not_raise_on_empty(self):
        from navig.ui.formatters import render_kv_diagnostics
        render_kv_diagnostics([])  # should not raise

    def test_renders_pairs(self, capsys):
        from navig.ui.formatters import render_kv_diagnostics
        with patch("navig.ui.formatters.console") as mock_console:
            render_kv_diagnostics([("key", "val")])
        # console.print called
        assert mock_console.print.called

    def test_renders_title_when_given(self):
        from navig.ui.formatters import render_kv_diagnostics
        with patch("navig.ui.formatters.console") as mock_console:
            render_kv_diagnostics([("k", "v")], title="My Section")
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("My Section" in c for c in calls)

    def test_exception_silenced(self):
        from navig.ui.formatters import render_kv_diagnostics
        with patch("navig.ui.formatters.console") as mock_console:
            mock_console.print.side_effect = RuntimeError("console broken")
            # should not raise
            render_kv_diagnostics([("k", "v")])


class TestRenderCommandRow:
    def test_does_not_raise(self):
        from navig.ui.formatters import render_command_row
        render_command_row("label", "navig run ls")

    def test_calls_console_print(self):
        from navig.ui.formatters import render_command_row
        with patch("navig.ui.formatters.console") as mock_console:
            render_command_row("label", "cmd")
        mock_console.print.assert_called_once()

    def test_exception_silenced(self):
        from navig.ui.formatters import render_command_row
        with patch("navig.ui.formatters.console") as mock_console:
            mock_console.print.side_effect = RuntimeError("broken")
            render_command_row("label", "cmd")  # must not raise


class TestRenderSectionDivider:
    def test_does_not_raise(self):
        from navig.ui.formatters import render_section_divider
        render_section_divider()

    def test_with_title(self):
        from navig.ui.formatters import render_section_divider
        with patch("navig.ui.formatters.console") as mock_console:
            render_section_divider("My Title")
        mock_console.rule.assert_called_once()

    def test_exception_silenced(self):
        from navig.ui.formatters import render_section_divider
        with patch("navig.ui.formatters.console") as mock_c:
            mock_c.rule.side_effect = RuntimeError("boom")
            render_section_divider()  # must not raise
