"""
Batch 81: navig/bot/command_registry.py, navig/spaces/kickoff.py,
          navig/spaces/briefing.py
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# bot/command_registry.py
# ---------------------------------------------------------------------------
from navig.bot.command_registry import BotCommand, CommandRegistry, get_command_registry


_SAMPLE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


class TestBotCommand:
    def test_repr(self):
        cmd = BotCommand(name="test_cmd", schema={})
        assert "test_cmd" in repr(cmd)

    def test_tags_default_empty(self):
        cmd = BotCommand(name="x", schema={})
        assert cmd.tags == []


class TestCommandRegistry:
    def setup_method(self):
        self.reg = CommandRegistry()

    def test_empty_registry(self):
        assert len(self.reg) == 0
        assert self.reg.all() == []
        assert self.reg.schemas() == []
        assert self.reg.names() == []

    def test_add_valid_schema(self):
        cmd = self.reg.add(_SAMPLE_SCHEMA)
        assert isinstance(cmd, BotCommand)
        assert cmd.name == "web_search"
        assert len(self.reg) == 1

    def test_get_returns_command(self):
        self.reg.add(_SAMPLE_SCHEMA)
        cmd = self.reg.get("web_search")
        assert cmd is not None
        assert cmd.name == "web_search"

    def test_get_returns_none_for_missing(self):
        assert self.reg.get("nonexistent") is None

    def test_contains_operator(self):
        self.reg.add(_SAMPLE_SCHEMA)
        assert "web_search" in self.reg
        assert "missing" not in self.reg

    def test_invalid_schema_raises(self):
        with pytest.raises(ValueError):
            self.reg.add({"invalid": "schema"})

    def test_schema_without_function_key_raises(self):
        with pytest.raises(ValueError):
            self.reg.add({"type": "function"})

    def test_schemas_returns_list(self):
        self.reg.add(_SAMPLE_SCHEMA)
        schemas = self.reg.schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "web_search"

    def test_names_returns_list(self):
        self.reg.add(_SAMPLE_SCHEMA)
        assert "web_search" in self.reg.names()

    def test_bulk_load(self):
        schemas = [_SAMPLE_SCHEMA, {
            "type": "function",
            "function": {"name": "image_gen", "description": "gen", "parameters": {}},
        }]
        self.reg.bulk_load(schemas, tags=["test"])
        assert len(self.reg) == 2
        assert "image_gen" in self.reg

    def test_register_decorator(self):
        @self.reg.register
        def my_tool_schema():
            return {
                "type": "function",
                "function": {"name": "my_tool", "description": "x", "parameters": {}},
            }

        assert "my_tool" in self.reg
        # function is returned unchanged
        assert callable(my_tool_schema)

    def test_overwrite_existing(self):
        schema_v1 = dict(_SAMPLE_SCHEMA)
        schema_v2 = {
            "type": "function",
            "function": {"name": "web_search", "description": "v2", "parameters": {}},
        }
        self.reg.add(schema_v1)
        self.reg.add(schema_v2)
        # Still 1 entry
        assert len(self.reg) == 1
        assert self.reg.get("web_search").schema == schema_v2

    def test_tags_stored(self):
        cmd = self.reg.add(_SAMPLE_SCHEMA, tags=["core", "search"])
        assert "core" in cmd.tags
        assert "search" in cmd.tags

    def test_all_preserves_insertion_order(self):
        for i in range(3):
            self.reg.add({
                "type": "function",
                "function": {"name": f"cmd{i}", "description": "", "parameters": {}},
            })
        names = [c.name for c in self.reg.all()]
        assert names == ["cmd0", "cmd1", "cmd2"]


# ---------------------------------------------------------------------------
# spaces/kickoff.py  (private helpers)
# ---------------------------------------------------------------------------
from navig.spaces.kickoff import (
    _vision_goal,
    _extract_pending_actions,
    SpaceKickoff,
)


class TestVisionGoal:
    def test_frontmatter_goal(self):
        text = "---\ngoal: Deploy to production\n---\n# Title\n"
        result = _vision_goal(text, "fallback")
        assert result == "Deploy to production"

    def test_heading_goal_fallback(self):
        text = "# My Project Goal\nSome description"
        result = _vision_goal(text, "fallback")
        assert result == "My Project Goal"

    def test_empty_text_uses_fallback(self):
        result = _vision_goal("", "default goal")
        assert result == "default goal"

    def test_no_heading_no_frontmatter_uses_fallback(self):
        result = _vision_goal("Just some text without headings", "fallback")
        assert result == "fallback"


class TestExtractPendingActions:
    def test_checkboxes(self):
        md = "- [ ] Deploy backend\n- [ ] Update docs\n- [x] Done task\n"
        actions = _extract_pending_actions(md)
        assert "Deploy backend" in actions
        assert "Update docs" in actions
        # Checked boxes should NOT be included
        assert "Done task" not in actions

    def test_bullet_fallback(self):
        md = "- First action\n- Second action\n"
        actions = _extract_pending_actions(md)
        assert "First action" in actions
        assert "Second action" in actions

    def test_empty_returns_empty(self):
        assert _extract_pending_actions("") == []
        assert _extract_pending_actions(None) == []

    def test_heading_bullets_skipped(self):
        md = "- # This is a heading bullet\n- Normal action\n"
        actions = _extract_pending_actions(md)
        assert "Normal action" in actions
        # Heading bullets should be skipped
        assert not any(a.startswith("#") for a in actions)


class TestSpaceKickoff:
    def test_dataclass_fields(self):
        k = SpaceKickoff(space="devops", goal="deploy", actions=["a", "b"])
        assert k.space == "devops"
        assert k.goal == "deploy"
        assert k.actions == ["a", "b"]

    def test_frozen(self):
        k = SpaceKickoff(space="x", goal="y", actions=[])
        with pytest.raises((AttributeError, TypeError)):
            k.space = "z"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# spaces/briefing.py
# ---------------------------------------------------------------------------
from navig.spaces.briefing import build_spaces_briefing_lines


class TestBuildSpacesBriefingLines:
    def test_no_spaces_returns_no_content_message(self):
        with patch("navig.spaces.briefing.collect_spaces_progress", return_value=[]):
            lines = build_spaces_briefing_lines()
        assert len(lines) == 1
        assert "No spaces" in lines[0]

    def test_with_spaces_includes_header(self):
        row = MagicMock()
        row.name = "devops"
        row.scope = "project"
        row.completion_pct = 75.0
        row.goal = "Deploy infrastructure"

        with patch("navig.spaces.briefing.collect_spaces_progress", return_value=[row]):
            with patch("navig.spaces.briefing.select_best_next_action", return_value=None):
                lines = build_spaces_briefing_lines()

        assert any("Spaces Progress" in line for line in lines)
        assert any("devops" in line for line in lines)

    def test_with_action_includes_action_section(self):
        row = MagicMock()
        row.name = "devops"
        row.scope = "project"
        row.completion_pct = 50.0
        row.goal = "Goal"

        action = MagicMock()
        action.space = "devops"
        action.scope = "project"
        action.next_task = "Fix CI pipeline"

        with patch("navig.spaces.briefing.collect_spaces_progress", return_value=[row]):
            with patch("navig.spaces.briefing.select_best_next_action", return_value=action):
                lines = build_spaces_briefing_lines()

        assert any("Action Focus" in line for line in lines)
        assert any("Fix CI pipeline" in line for line in lines)

    def test_max_items_limits_rows(self):
        rows = []
        for i in range(10):
            r = MagicMock()
            r.name = f"space{i}"
            r.scope = "project"
            r.completion_pct = float(i * 10)
            r.goal = f"Goal {i}"
            rows.append(r)

        with patch("navig.spaces.briefing.collect_spaces_progress", return_value=rows):
            with patch("navig.spaces.briefing.select_best_next_action", return_value=None):
                lines = build_spaces_briefing_lines(max_items=3)

        # Header + 3 items = at most 4 space-related lines
        space_lines = [l for l in lines if "space" in l]
        assert len(space_lines) <= 3
