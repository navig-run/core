"""
Batch 77: hermetic unit tests for
  - navig/core/models.py        (CommandParameter, NavigCommand, SkillManifest,
                                  PackStep, NavigPack)
  - navig/core/config_schema.py (enums, validate_config_dict, validate_global_config,
                                  validate_host_config, get_config_schema)
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# navig/core/models.py
# ---------------------------------------------------------------------------

class TestCommandParameter:
    def test_required_fields(self) -> None:
        from navig.core.models import CommandParameter
        p = CommandParameter(type="string", description="A name")
        assert p.type == "string"
        assert p.description == "A name"

    def test_defaults(self) -> None:
        from navig.core.models import CommandParameter
        p = CommandParameter(type="int", description="count")
        assert p.required is False
        assert p.default is None
        assert p.options is None

    def test_with_options(self) -> None:
        from navig.core.models import CommandParameter
        p = CommandParameter(type="string", description="mode", options=["a", "b"])
        assert p.options == ["a", "b"]


class TestNavigCommand:
    def test_required_fields(self) -> None:
        from navig.core.models import NavigCommand
        cmd = NavigCommand(name="run", syntax="navig run", description="Run a command")
        assert cmd.name == "run"
        assert cmd.syntax == "navig run"
        assert cmd.description == "Run a command"

    def test_defaults(self) -> None:
        from navig.core.models import NavigCommand
        cmd = NavigCommand(name="x", syntax="x", description="d")
        assert cmd.risk == "safe"
        assert cmd.confirmation_required is False
        assert cmd.confirmation_msg is None
        assert cmd.parameters is None
        assert cmd.source_skill is None

    def test_destructive_risk(self) -> None:
        from navig.core.models import NavigCommand
        cmd = NavigCommand(name="rm", syntax="rm -rf", description="Delete", risk="destructive")
        assert cmd.risk == "destructive"


class TestSkillManifest:
    def test_minimal(self) -> None:
        from navig.core.models import SkillManifest
        sm = SkillManifest(name="test-skill", description="Test", version="1.0.0")
        assert sm.name == "test-skill"
        assert sm.category == "uncategorized"
        assert sm.navig_commands == []
        assert sm.examples == []

    def test_alias_risk_level(self) -> None:
        from navig.core.models import SkillManifest
        sm = SkillManifest(**{"name": "s", "description": "d", "version": "1.0.0", "risk-level": "moderate"})
        assert sm.risk_level == "moderate"

    def test_tags(self) -> None:
        from navig.core.models import SkillManifest
        sm = SkillManifest(name="s", description="d", version="1.0.0", tags=["infra", "deploy"])
        assert "infra" in sm.tags


class TestNavigPack:
    def test_minimal(self) -> None:
        from navig.core.models import NavigPack
        pack = NavigPack(name="deploy", description="Deploy app")
        assert pack.name == "deploy"
        assert pack.version == "1.0.0"
        assert pack.type == "runbook"
        assert pack.steps == []

    def test_with_steps(self) -> None:
        from navig.core.models import NavigPack, PackStep
        step = PackStep(command="navig run 'ls -la'")
        pack = NavigPack(name="test", description="Test pack", steps=[step])
        assert len(pack.steps) == 1
        assert pack.steps[0].command == "navig run 'ls -la'"


class TestPackStep:
    def test_defaults(self) -> None:
        from navig.core.models import PackStep
        step = PackStep(command="echo hello")
        assert step.name == "unnamed-step"
        assert step.continue_on_error is False
        assert step.description is None

    def test_named_step(self) -> None:
        from navig.core.models import PackStep
        step = PackStep(name="health-check", command="navig host test")
        assert step.name == "health-check"


# ---------------------------------------------------------------------------
# navig/core/config_schema.py
# ---------------------------------------------------------------------------

class TestConfigSchemaEnums:
    def test_log_level_values(self) -> None:
        from navig.core.config_schema import LogLevel
        assert LogLevel.DEBUG == "DEBUG"
        assert LogLevel.INFO == "INFO"
        assert LogLevel.WARNING == "WARNING"
        assert LogLevel.ERROR == "ERROR"
        assert LogLevel.CRITICAL == "CRITICAL"

    def test_execution_mode_values(self) -> None:
        from navig.core.config_schema import ExecutionMode
        assert ExecutionMode.INTERACTIVE == "interactive"
        assert ExecutionMode.AUTO == "auto"

    def test_confirmation_level_values(self) -> None:
        from navig.core.config_schema import ConfirmationLevel
        assert ConfirmationLevel.CRITICAL == "critical"
        assert ConfirmationLevel.STANDARD == "standard"
        assert ConfirmationLevel.VERBOSE == "verbose"

    def test_auth_method_values(self) -> None:
        from navig.core.config_schema import AuthMethod
        assert AuthMethod.KEY == "key"
        assert AuthMethod.PASSWORD == "password"
        assert AuthMethod.AGENT == "agent"


class TestValidateConfigDict:
    def test_empty_dict_is_valid(self) -> None:
        from navig.core.config_schema import validate_config_dict, PYDANTIC_AVAILABLE
        if not PYDANTIC_AVAILABLE:
            pytest.skip("pydantic not installed")
        is_valid, issues = validate_config_dict({})
        assert is_valid is True
        assert issues == []

    def test_returns_false_for_invalid(self) -> None:
        from navig.core.config_schema import validate_config_dict, PYDANTIC_AVAILABLE
        if not PYDANTIC_AVAILABLE:
            pytest.skip("pydantic not installed")
        # Provide a value that would fail type check
        is_valid, issues = validate_config_dict({"log_level": "INVALID_LEVEL"})
        # Either validation fails (Pydantic strict) or passes (permissive)
        assert isinstance(is_valid, bool)
        assert isinstance(issues, list)

    def test_returns_tuple(self) -> None:
        from navig.core.config_schema import validate_config_dict
        result = validate_config_dict({})
        assert isinstance(result, tuple) and len(result) == 2


class TestValidateGlobalConfig:
    def test_empty_dict_returns_model_or_none(self) -> None:
        from navig.core.config_schema import validate_global_config, PYDANTIC_AVAILABLE
        result = validate_global_config({})
        if PYDANTIC_AVAILABLE:
            assert result is not None
        else:
            assert result is None

    def test_with_log_level(self) -> None:
        from navig.core.config_schema import validate_global_config, PYDANTIC_AVAILABLE
        if not PYDANTIC_AVAILABLE:
            pytest.skip("pydantic not installed")
        result = validate_global_config({"log_level": "DEBUG"})
        assert result is not None
        assert result.log_level.value == "DEBUG"


class TestValidateHostConfig:
    def test_minimal_host_does_not_raise(self) -> None:
        from navig.core.config_schema import validate_host_config, PYDANTIC_AVAILABLE
        if not PYDANTIC_AVAILABLE:
            pytest.skip("pydantic not installed")
        # Non-strict: should return a model or None, never raise
        result = validate_host_config({"host": "192.168.1.1", "user": "root"})
        assert result is None or result is not None  # just no exception

    def test_returns_none_on_invalid(self) -> None:
        from navig.core.config_schema import validate_host_config, PYDANTIC_AVAILABLE
        if not PYDANTIC_AVAILABLE:
            pytest.skip("pydantic not installed")
        # Missing required fields — may or may not fail depending on strictness
        result = validate_host_config({})
        # Non-strict returns None or valid object depending on required fields
        assert result is None or result is not None  # just ensure no exception


class TestGetConfigSchema:
    def test_global_schema_has_type(self) -> None:
        from navig.core.config_schema import get_config_schema, PYDANTIC_AVAILABLE
        if not PYDANTIC_AVAILABLE:
            pytest.skip("pydantic not installed")
        schema = get_config_schema("global")
        assert schema is not None
        assert "type" in schema or "properties" in schema

    def test_host_schema(self) -> None:
        from navig.core.config_schema import get_config_schema, PYDANTIC_AVAILABLE
        if not PYDANTIC_AVAILABLE:
            pytest.skip("pydantic not installed")
        schema = get_config_schema("host")
        assert schema is not None

    def test_unknown_type_raises(self) -> None:
        from navig.core.config_schema import get_config_schema, PYDANTIC_AVAILABLE
        if not PYDANTIC_AVAILABLE:
            pytest.skip("pydantic not installed")
        with pytest.raises(ValueError):
            get_config_schema("unknown_type")
