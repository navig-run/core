"""
Tests for navig.core.config_schema — enums, Pydantic models, and validation helpers.

Pydantic models are only tested when Pydantic is installed (same guard as the module).
"""

from __future__ import annotations

import pytest

from navig.core.config_schema import (
    PYDANTIC_AVAILABLE,
    AuthMethod,
    ConfirmationLevel,
    ExecutionMode,
    LogLevel,
)


# ---------------------------------------------------------------------------
# LogLevel enum
# ---------------------------------------------------------------------------


class TestLogLevel:
    def test_all_values_are_strings(self):
        for member in LogLevel:
            assert isinstance(member.value, str)

    def test_expected_members(self):
        assert LogLevel.DEBUG.value == "DEBUG"
        assert LogLevel.INFO.value == "INFO"
        assert LogLevel.WARNING.value == "WARNING"
        assert LogLevel.ERROR.value == "ERROR"
        assert LogLevel.CRITICAL.value == "CRITICAL"

    def test_count(self):
        assert len(list(LogLevel)) == 5

    def test_constructible_from_value(self):
        assert LogLevel("INFO") is LogLevel.INFO


# ---------------------------------------------------------------------------
# ExecutionMode enum
# ---------------------------------------------------------------------------


class TestExecutionMode:
    def test_interactive(self):
        assert ExecutionMode.INTERACTIVE.value == "interactive"

    def test_auto(self):
        assert ExecutionMode.AUTO.value == "auto"

    def test_two_members(self):
        assert len(list(ExecutionMode)) == 2


# ---------------------------------------------------------------------------
# ConfirmationLevel enum
# ---------------------------------------------------------------------------


class TestConfirmationLevel:
    def test_critical_value(self):
        assert ConfirmationLevel.CRITICAL.value == "critical"

    def test_standard_value(self):
        assert ConfirmationLevel.STANDARD.value == "standard"

    def test_verbose_value(self):
        assert ConfirmationLevel.VERBOSE.value == "verbose"

    def test_three_members(self):
        assert len(list(ConfirmationLevel)) == 3


# ---------------------------------------------------------------------------
# AuthMethod enum
# ---------------------------------------------------------------------------


class TestAuthMethod:
    def test_key_value(self):
        assert AuthMethod.KEY.value == "key"

    def test_password_value(self):
        assert AuthMethod.PASSWORD.value == "password"

    def test_agent_value(self):
        assert AuthMethod.AGENT.value == "agent"


# ---------------------------------------------------------------------------
# Pydantic-guarded tests
# ---------------------------------------------------------------------------

pytestmark_pydantic = pytest.mark.skipif(
    not PYDANTIC_AVAILABLE, reason="pydantic not installed"
)


@pytest.mark.skipif(not PYDANTIC_AVAILABLE, reason="pydantic not installed")
class TestExecutionConfig:
    def test_default_construction(self):
        from navig.core.config_schema import ExecutionConfig

        cfg = ExecutionConfig()
        assert cfg.mode is ExecutionMode.INTERACTIVE
        assert cfg.confirmation_level is ConfirmationLevel.STANDARD
        assert cfg.auto_confirm_safe is False
        assert cfg.timeout_seconds == 60

    def test_custom_values(self):
        from navig.core.config_schema import ExecutionConfig

        cfg = ExecutionConfig(mode=ExecutionMode.AUTO, timeout_seconds=120)
        assert cfg.mode is ExecutionMode.AUTO
        assert cfg.timeout_seconds == 120

    def test_timeout_min_boundary(self):
        from navig.core.config_schema import ExecutionConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExecutionConfig(timeout_seconds=0)  # ge=1 violated

    def test_timeout_max_boundary(self):
        from navig.core.config_schema import ExecutionConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExecutionConfig(timeout_seconds=9999)  # le=3600 violated


@pytest.mark.skipif(not PYDANTIC_AVAILABLE, reason="pydantic not installed")
class TestGatewayConfig:
    def test_default_enabled_false(self):
        from navig.core.config_schema import GatewayConfig

        cfg = GatewayConfig()
        assert cfg.enabled is False

    def test_default_port(self):
        from navig.core.config_schema import GatewayConfig

        cfg = GatewayConfig()
        assert 1024 <= cfg.port <= 65535

    def test_default_require_auth(self):
        from navig.core.config_schema import GatewayConfig

        cfg = GatewayConfig()
        assert cfg.require_auth is True


@pytest.mark.skipif(not PYDANTIC_AVAILABLE, reason="pydantic not installed")
class TestLoggedConfig:
    def test_default_log_level_info(self):
        from navig.core.config_schema import LoggingConfig

        cfg = LoggingConfig()
        assert cfg.level is LogLevel.INFO

    def test_custom_level(self):
        from navig.core.config_schema import LoggingConfig

        cfg = LoggingConfig(level=LogLevel.DEBUG)
        assert cfg.level is LogLevel.DEBUG

    def test_max_file_size_default_positive(self):
        from navig.core.config_schema import LoggingConfig

        cfg = LoggingConfig()
        assert cfg.max_file_size_mb > 0

    def test_file_size_min_boundary(self):
        from navig.core.config_schema import LoggingConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LoggingConfig(max_file_size_mb=0)


@pytest.mark.skipif(not PYDANTIC_AVAILABLE, reason="pydantic not installed")
class TestTunnelConfig:
    def test_default_port_range(self):
        from navig.core.config_schema import TunnelConfig

        cfg = TunnelConfig()
        start, end = cfg.port_range
        assert 1024 <= start < end <= 65535

    def test_invalid_port_range_raises(self):
        from navig.core.config_schema import TunnelConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TunnelConfig(port_range=(100, 200))  # below 1024 boundary

    def test_auto_cleanup_default_true(self):
        from navig.core.config_schema import TunnelConfig

        assert TunnelConfig().auto_cleanup is True


@pytest.mark.skipif(not PYDANTIC_AVAILABLE, reason="pydantic not installed")
class TestToolsConfig:
    def test_safety_mode_valid(self):
        from navig.core.config_schema import ToolsConfig

        cfg = ToolsConfig(safety_mode="strict")
        assert cfg.safety_mode == "strict"

    def test_safety_mode_invalid_raises(self):
        from navig.core.config_schema import ToolsConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ToolsConfig(safety_mode="insane")

    def test_max_calls_default_positive(self):
        from navig.core.config_schema import ToolsConfig

        cfg = ToolsConfig()
        assert cfg.max_calls_per_turn > 0
