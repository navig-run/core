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
    """"Not initialised" now means *resolution failed*, not "no context was injected".

    The tool used to hold a module-global context set once at registration; with none set
    it reported "not initialised". It now resolves the ACTIVE SPACE's shared context per
    call — which is what lets one daemon serve many spaces, and what makes a
    force-activation land on the instance the prompt is built from.

    So these tests must break resolution to reach the branch. Patching is also what keeps
    them hermetic: without it the handler reads the operator's real ``~/.navig/skills``.
    """

    def setup_method(self):
        _clear_ctx()

    @staticmethod
    def _break_resolution(monkeypatch):
        def _boom(*_a, **_kw):
            raise RuntimeError("no active space")

        monkeypatch.setattr(
            "navig.agent.skills_context.get_skills_context", _boom, raising=False
        )

    def test_returns_not_initialised(self, monkeypatch):
        self._break_resolution(monkeypatch)
        result = handle_manage_skills("list")
        assert "not initialised" in result.lower()

    def test_activate_not_initialised(self, monkeypatch):
        self._break_resolution(monkeypatch)
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

    def test_the_tool_actually_lands_in_the_registry(self):
        """The assertion the old test was missing entirely.

        It patched `_AGENT_REGISTRY` with a `MagicMock` — which **has every attribute** —
        so the call to `register_function` (a method that has never existed on the real
        registry) "succeeded" against the mock, and the test then asserted only that a
        module global had been set. The tool was absent from every live agent for as long
        as it existed, with a green test next to it.

        Assert against the REAL registry. A mock cannot fail this way.
        """
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY

        try:
            register_skill_tools()
            assert "manage_skills" in _AGENT_REGISTRY
            entry = _AGENT_REGISTRY.get_entry("manage_skills")
            assert entry is not None and entry.toolset == "skills"
        finally:
            _AGENT_REGISTRY.deregister_toolset("skills")

    def test_an_explicit_context_still_pins_the_tool_to_it(self):
        """The optional override kept working for embedders that inject their own."""
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY

        ctx = _ctx()
        try:
            register_skill_tools(ctx)
            assert skill_tools_mod._skills_ctx is ctx
        finally:
            _AGENT_REGISTRY.deregister_toolset("skills")

    def test_a_broken_registry_is_not_swallowed(self):
        """Registration failure must reach the caller.

        This used to be `test_registry_failure_silently_ignored`, and that swallow is
        precisely what hid the bug: the AttributeError from the nonexistent method went to
        `logger.debug` and the tool quietly never registered. `register_all_tools` already
        isolates each group, so the group function does not need a second net — it needs
        to be honest.
        """
        with (
            patch.dict("sys.modules", {"navig.agent.agent_tool_registry": None}),
            pytest.raises(ImportError),
        ):
            register_skill_tools()


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
