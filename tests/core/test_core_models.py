"""Tests for navig.core.models — Pydantic domain models."""

from __future__ import annotations

import pytest

from navig.core.models import (
    CommandParameter,
    NavigCommand,
    NavigPack,
    PackStep,
    SkillExample,
    SkillManifest,
)


# ──────────────────────────────────────────────────────────────
# CommandParameter
# ──────────────────────────────────────────────────────────────


class TestCommandParameter:
    def test_required_fields(self):
        p = CommandParameter(type="string", description="A name")
        assert p.type == "string"
        assert p.description == "A name"

    def test_defaults(self):
        p = CommandParameter(type="int", description="count")
        assert p.required is False
        assert p.default is None
        assert p.options is None

    def test_with_options(self):
        p = CommandParameter(type="choice", description="env", options=["dev", "prod"])
        assert "dev" in p.options
        assert "prod" in p.options

    def test_with_default(self):
        p = CommandParameter(type="int", description="limit", default=100)
        assert p.default == 100


# ──────────────────────────────────────────────────────────────
# NavigCommand
# ──────────────────────────────────────────────────────────────


class TestNavigCommand:
    def test_required_fields(self):
        cmd = NavigCommand(name="list", syntax="navig list", description="List things")
        assert cmd.name == "list"
        assert cmd.syntax == "navig list"

    def test_defaults(self):
        cmd = NavigCommand(name="ls", syntax="navig ls", description="d")
        assert cmd.risk == "safe"
        assert cmd.confirmation_required is False
        assert cmd.confirmation_msg is None
        assert cmd.parameters is None
        assert cmd.source_skill is None

    def test_with_parameters(self):
        cmd = NavigCommand(
            name="run",
            syntax="navig run",
            description="run command",
            parameters={"host": CommandParameter(type="string", description="host name")},
        )
        assert "host" in cmd.parameters

    def test_destructive_risk(self):
        cmd = NavigCommand(name="rm", syntax="navig rm", description="delete", risk="destructive")
        assert cmd.risk == "destructive"


# ──────────────────────────────────────────────────────────────
# SkillExample
# ──────────────────────────────────────────────────────────────


class TestSkillExample:
    def test_fields(self):
        ex = SkillExample(user="show disk", thought="check space", command="navig host monitor show --disk")
        assert ex.user == "show disk"
        assert ex.thought == "check space"
        assert ex.command == "navig host monitor show --disk"


# ──────────────────────────────────────────────────────────────
# SkillManifest
# ──────────────────────────────────────────────────────────────


class TestSkillManifest:
    def test_minimal_manifest(self):
        m = SkillManifest(name="ops", description="Ops skill", version="1.0.0")
        assert m.name == "ops"

    def test_defaults(self):
        m = SkillManifest(name="x", description="d", version="1.0.0")
        assert m.author is None
        assert m.category == "uncategorized"
        assert m.risk_level == "safe"
        assert m.user_invocable is True
        assert m.requires == []
        assert m.tags == []
        assert m.navig_commands == []
        assert m.examples == []

    def test_risk_level_alias(self):
        m = SkillManifest.model_validate(
            {"name": "x", "description": "d", "version": "1.0", "risk-level": "moderate"}
        )
        assert m.risk_level == "moderate"

    def test_user_invocable_alias(self):
        m = SkillManifest.model_validate(
            {"name": "x", "description": "d", "version": "1.0", "user-invocable": False}
        )
        assert m.user_invocable is False

    def test_navig_commands_alias(self):
        m = SkillManifest.model_validate(
            {
                "name": "x",
                "description": "d",
                "version": "1.0",
                "navig-commands": [{"name": "ls", "syntax": "navig ls", "description": "list"}],
            }
        )
        assert len(m.navig_commands) == 1
        assert m.navig_commands[0].name == "ls"

    def test_with_examples(self):
        m = SkillManifest.model_validate(
            {
                "name": "x",
                "description": "d",
                "version": "1.0",
                "examples": [{"user": "show disk", "thought": "check", "command": "navig run df"}],
            }
        )
        assert len(m.examples) == 1


# ──────────────────────────────────────────────────────────────
# PackStep
# ──────────────────────────────────────────────────────────────


class TestPackStep:
    def test_required_command(self):
        step = PackStep(command="navig host list")
        assert step.command == "navig host list"

    def test_defaults(self):
        step = PackStep(command="x")
        assert step.name == "unnamed-step"
        assert step.description is None
        assert step.continue_on_error is False

    def test_with_all_fields(self):
        step = PackStep(name="my-step", command="navig run ls", description="ls cmd", continue_on_error=True)
        assert step.name == "my-step"
        assert step.continue_on_error is True


# ──────────────────────────────────────────────────────────────
# NavigPack
# ──────────────────────────────────────────────────────────────


class TestNavigPack:
    def test_minimal_pack(self):
        p = NavigPack(name="deploy", description="Deploy steps")
        assert p.name == "deploy"

    def test_defaults(self):
        p = NavigPack(name="p", description="d")
        assert p.version == "1.0.0"
        assert p.author == "unknown"
        assert p.type == "runbook"
        assert p.tags == []
        assert p.steps == []

    def test_with_steps(self):
        p = NavigPack(
            name="setup",
            description="setup",
            steps=[PackStep(command="navig host list"), PackStep(command="navig db list")],
        )
        assert len(p.steps) == 2
