"""Tests for navig.agent.tools.skill_tools."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import navig.agent.tools.skill_tools as skill_tools_mod
from navig.agent.tools.skill_tools import (
    MANAGE_SKILLS_SCHEMA,
    get_skill_schemas,
    handle_manage_skills,
    register_skill_tools,
)


# ── helpers ──────────────────────────────────────────────────


def _skill(name: str) -> MagicMock:
    s = MagicMock()
    s.name = name
    s.source = "project"
    s.priority = 1
    s.activation_paths = ["*.py"]
    s.activation_keywords = ["python"]
    s.summary = MagicMock(return_value="A test skill")
    return s


def _ctx(skills: list | None = None, force_activated: set | None = None, force_deactivated: set | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.all_skills = skills or []
    ctx._force_activated = force_activated or set()
    ctx._force_deactivated = force_deactivated or set()
    ctx.force_activate = MagicMock(return_value=True)
    ctx.force_deactivate = MagicMock(return_value=True)
    return ctx


def _set_ctx(ctx) -> None:
    skill_tools_mod._skills_ctx = ctx


def _clear_ctx() -> None:
    skill_tools_mod._skills_ctx = None


# ── handle_manage_skills — not initialised ────────────────────


class TestNotInitialised:
    def setup_method(self):
        _clear_ctx()

    def test_returns_not_initialised(self):
        result = handle_manage_skills("list")
        assert "not initialised" in result.lower()

    def test_activate_not_initialised(self):
        result = handle_manage_skills("activate", skill_name="test")
        assert "not initialised" in result.lower()


# ── handle_manage_skills — list ───────────────────────────────


class TestListAction:
    def setup_method(self):
        _set_ctx(_ctx(skills=[_skill("skill-a"), _skill("skill-b")]))

    def teardown_method(self):
        _clear_ctx()

    def test_list_returns_json(self):
        result = handle_manage_skills("list")
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    def test_list_includes_skill_names(self):
        result = handle_manage_skills("list")
        parsed = json.loads(result)
        names = [e["name"] for e in parsed]
        assert "skill-a" in names
        assert "skill-b" in names

    def test_list_no_skills_message(self):
        _set_ctx(_ctx(skills=[]))
        result = handle_manage_skills("list")
        assert "no skills found" in result.lower()

    def test_list_includes_force_activated_status(self):
        _set_ctx(_ctx(skills=[_skill("skill-a")], force_activated={"skill-a"}))
        result = handle_manage_skills("list")
        parsed = json.loads(result)
        assert parsed[0]["force_activated"] is True

    def test_list_includes_force_deactivated_status(self):
        _set_ctx(_ctx(skills=[_skill("skill-x")], force_deactivated={"skill-x"}))
        result = handle_manage_skills("list")
        parsed = json.loads(result)
        assert parsed[0]["force_deactivated"] is True


# ── handle_manage_skills — activate ──────────────────────────


class TestActivateAction:
    def setup_method(self):
        self.ctx = _ctx(skills=[_skill("my-skill")])
        _set_ctx(self.ctx)

    def teardown_method(self):
        _clear_ctx()

    def test_activate_success_message(self):
        result = handle_manage_skills("activate", skill_name="my-skill")
        assert "force-activated" in result

    def test_activate_calls_ctx(self):
        handle_manage_skills("activate", skill_name="my-skill")
        self.ctx.force_activate.assert_called_once_with("my-skill")

    def test_activate_empty_name_returns_error(self):
        result = handle_manage_skills("activate", skill_name="")
        assert "required" in result.lower()

    def test_activate_skill_not_found(self):
        self.ctx.force_activate.return_value = False
        result = handle_manage_skills("activate", skill_name="ghost")
        assert "not found" in result

    def test_activate_mentions_skill_name(self):
        result = handle_manage_skills("activate", skill_name="my-skill")
        assert "my-skill" in result


# ── handle_manage_skills — deactivate ────────────────────────


class TestDeactivateAction:
    def setup_method(self):
        self.ctx = _ctx(skills=[_skill("my-skill")])
        _set_ctx(self.ctx)

    def teardown_method(self):
        _clear_ctx()

    def test_deactivate_success_message(self):
        result = handle_manage_skills("deactivate", skill_name="my-skill")
        assert "force-deactivated" in result

    def test_deactivate_calls_ctx(self):
        handle_manage_skills("deactivate", skill_name="my-skill")
        self.ctx.force_deactivate.assert_called_once_with("my-skill")

    def test_deactivate_empty_name_returns_error(self):
        result = handle_manage_skills("deactivate", skill_name="")
        assert "required" in result.lower()

    def test_deactivate_skill_not_found(self):
        self.ctx.force_deactivate.return_value = False
        result = handle_manage_skills("deactivate", skill_name="ghost")
        assert "not found" in result


# ── handle_manage_skills — unknown action ────────────────────


class TestUnknownAction:
    def setup_method(self):
        _set_ctx(_ctx())

    def teardown_method(self):
        _clear_ctx()

    def test_unknown_action_returns_error(self):
        result = handle_manage_skills("explode")
        assert "unknown action" in result.lower()

    def test_unknown_action_mentions_valid_options(self):
        result = handle_manage_skills("flying")
        assert "list" in result


# ── register_skill_tools ──────────────────────────────────────


class TestRegister:
    def teardown_method(self):
        _clear_ctx()

    def test_sets_skills_ctx(self):
        ctx = _ctx()
        with patch("navig.agent.tools.skill_tools._AGENT_REGISTRY" if False else "navig.agent.agent_tool_registry._AGENT_REGISTRY", create=True):
            register_skill_tools(ctx)
        assert skill_tools_mod._skills_ctx is ctx

    def test_registry_failure_silently_ignored(self):
        ctx = _ctx()
        with patch("navig.agent.tools.skill_tools.logger") as mock_log:
            # If registry import fails, it should just log debug
            with patch.dict("sys.modules", {"navig.agent.agent_tool_registry": None}):
                register_skill_tools(ctx)
        assert skill_tools_mod._skills_ctx is ctx  # context still set


# ── get_skill_schemas ─────────────────────────────────────────


class TestGetSkillSchemas:
    def test_returns_list(self):
        schemas = get_skill_schemas()
        assert isinstance(schemas, list)
        assert len(schemas) == 1

    def test_schema_type_function(self):
        schemas = get_skill_schemas()
        assert schemas[0]["type"] == "function"

    def test_schema_has_manage_skills(self):
        schemas = get_skill_schemas()
        assert schemas[0]["function"]["name"] == "manage_skills"


# ── MANAGE_SKILLS_SCHEMA ──────────────────────────────────────


class TestManageSkillsSchema:
    def test_name(self):
        assert MANAGE_SKILLS_SCHEMA["name"] == "manage_skills"

    def test_required_action(self):
        assert "action" in MANAGE_SKILLS_SCHEMA["parameters"]["required"]

    def test_action_enum_values(self):
        enum = MANAGE_SKILLS_SCHEMA["parameters"]["properties"]["action"]["enum"]
        assert "list" in enum
        assert "activate" in enum
        assert "deactivate" in enum
