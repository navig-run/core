"""Tests for _daemon_defaults, _llm_defaults, ai_tool_registry, status_event, core/protocols — batch 44."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# _daemon_defaults
# ---------------------------------------------------------------------------

def test_daemon_port_value():
    from navig._daemon_defaults import _DAEMON_PORT

    assert _DAEMON_PORT == 8765


def test_daemon_port_is_int():
    from navig._daemon_defaults import _DAEMON_PORT

    assert isinstance(_DAEMON_PORT, int)


def test_oauth_redirect_port_value():
    from navig._daemon_defaults import _OAUTH_REDIRECT_PORT

    assert _OAUTH_REDIRECT_PORT == 1455


def test_oauth_redirect_port_is_int():
    from navig._daemon_defaults import _OAUTH_REDIRECT_PORT

    assert isinstance(_OAUTH_REDIRECT_PORT, int)


def test_daemon_port_is_valid_port():
    from navig._daemon_defaults import _DAEMON_PORT

    assert 1024 <= _DAEMON_PORT <= 65535


def test_oauth_port_is_valid_port():
    from navig._daemon_defaults import _OAUTH_REDIRECT_PORT

    assert 1024 <= _OAUTH_REDIRECT_PORT <= 65535


def test_daemon_and_oauth_ports_differ():
    from navig._daemon_defaults import _DAEMON_PORT, _OAUTH_REDIRECT_PORT

    assert _DAEMON_PORT != _OAUTH_REDIRECT_PORT


# ---------------------------------------------------------------------------
# _llm_defaults
# ---------------------------------------------------------------------------

def test_default_temperature_value():
    from navig._llm_defaults import _DEFAULT_TEMPERATURE

    assert _DEFAULT_TEMPERATURE == 0.7


def test_default_temperature_is_float():
    from navig._llm_defaults import _DEFAULT_TEMPERATURE

    assert isinstance(_DEFAULT_TEMPERATURE, float)


def test_default_temperature_in_range():
    from navig._llm_defaults import _DEFAULT_TEMPERATURE

    assert 0.0 <= _DEFAULT_TEMPERATURE <= 2.0


def test_default_max_tokens_value():
    from navig._llm_defaults import _DEFAULT_MAX_TOKENS

    assert _DEFAULT_MAX_TOKENS == 4096


def test_default_max_tokens_is_int():
    from navig._llm_defaults import _DEFAULT_MAX_TOKENS

    assert isinstance(_DEFAULT_MAX_TOKENS, int)


def test_default_max_tokens_positive():
    from navig._llm_defaults import _DEFAULT_MAX_TOKENS

    assert _DEFAULT_MAX_TOKENS > 0


# ---------------------------------------------------------------------------
# ai_tool_registry (re-export module)
# ---------------------------------------------------------------------------

def test_ai_tool_registry_exports_bot_command():
    from navig.bot.ai_tool_registry import BotCommand

    assert BotCommand is not None


def test_ai_tool_registry_exports_command_registry():
    from navig.bot.ai_tool_registry import CommandRegistry

    assert CommandRegistry is not None


def test_ai_tool_registry_exports_get_command_registry():
    from navig.bot.ai_tool_registry import get_command_registry

    assert callable(get_command_registry)


def test_ai_tool_registry_all_contents():
    import navig.bot.ai_tool_registry as mod

    for name in ("BotCommand", "CommandRegistry", "get_command_registry"):
        assert hasattr(mod, name), f"Missing: {name}"


def test_ai_tool_registry_get_returns_registry_instance():
    from navig.bot.ai_tool_registry import CommandRegistry, get_command_registry

    registry = get_command_registry()
    assert isinstance(registry, CommandRegistry)


def test_ai_tool_registry_get_command_registry_singleton():
    from navig.bot.ai_tool_registry import get_command_registry

    r1 = get_command_registry()
    r2 = get_command_registry()
    assert r1 is r2


# ---------------------------------------------------------------------------
# StatusEvent
# ---------------------------------------------------------------------------

def test_status_event_creation():
    from navig.agent.conv.status_event import StatusEvent

    ev = StatusEvent(
        type="task_start",
        task_id="abc123",
        message="Starting task",
        timestamp=datetime.now(),
    )
    assert ev.type == "task_start"
    assert ev.task_id == "abc123"


def test_status_event_message():
    from navig.agent.conv.status_event import StatusEvent

    ev = StatusEvent(
        type="step_done",
        task_id="t1",
        message="Step complete",
        timestamp=datetime.now(),
    )
    assert ev.message == "Step complete"


def test_status_event_step_index_defaults_none():
    from navig.agent.conv.status_event import StatusEvent

    ev = StatusEvent(type="thinking", task_id="t2", message="...", timestamp=datetime.now())
    assert ev.step_index is None


def test_status_event_total_steps_defaults_none():
    from navig.agent.conv.status_event import StatusEvent

    ev = StatusEvent(type="thinking", task_id="t2", message="...", timestamp=datetime.now())
    assert ev.total_steps is None


def test_status_event_metadata_defaults_empty():
    from navig.agent.conv.status_event import StatusEvent

    ev = StatusEvent(type="task_done", task_id="t3", message="done", timestamp=datetime.now())
    assert ev.metadata == {}


def test_status_event_with_step_info():
    from navig.agent.conv.status_event import StatusEvent

    ev = StatusEvent(
        type="step_start",
        task_id="t4",
        message="Step 2 of 5",
        timestamp=datetime.now(),
        step_index=1,
        total_steps=5,
    )
    assert ev.step_index == 1
    assert ev.total_steps == 5


def test_status_event_with_metadata():
    from navig.agent.conv.status_event import StatusEvent

    ev = StatusEvent(
        type="step_failed",
        task_id="t5",
        message="Failed",
        timestamp=datetime.now(),
        metadata={"error": "timeout"},
    )
    assert ev.metadata["error"] == "timeout"


def test_status_event_all_type_literals():
    from navig.agent.conv.status_event import StatusEvent

    valid_types = [
        "task_start", "step_start", "step_done", "step_failed",
        "task_done", "thinking", "streaming_token",
    ]
    for t in valid_types:
        ev = StatusEvent(type=t, task_id="x", message="m", timestamp=datetime.now())  # type: ignore[arg-type]
        assert ev.type == t


def test_status_event_timestamp_stored():
    from navig.agent.conv.status_event import StatusEvent

    ts = datetime(2025, 1, 15, 10, 30, 0)
    ev = StatusEvent(type="task_done", task_id="x", message="done", timestamp=ts)
    assert ev.timestamp == ts


# ---------------------------------------------------------------------------
# core/protocols (structural Protocol checks)
# ---------------------------------------------------------------------------

def test_config_provider_protocol_importable():
    from navig.core.protocols import ConfigProvider

    assert ConfigProvider is not None


def test_host_config_provider_protocol_importable():
    from navig.core.protocols import HostConfigProvider

    assert HostConfigProvider is not None


def test_app_config_provider_protocol_importable():
    from navig.core.protocols import AppConfigProvider

    assert AppConfigProvider is not None


def test_config_provider_is_protocol():
    from typing import get_origin, get_args
    from navig.core.protocols import ConfigProvider
    from typing import Protocol

    # Check it's defined as a Protocol class
    assert issubclass(ConfigProvider, Protocol)


def test_mock_satisfies_config_provider():
    from navig.core.protocols import ConfigProvider

    mock = MagicMock(spec=ConfigProvider)
    mock.global_config_dir = Path("/home/user/.navig")
    mock.base_dir = Path("/home/user/.navig")
    mock.verbose = False
    mock.get_config_directories.return_value = [Path("/home/user/.navig")]

    assert mock.global_config_dir == Path("/home/user/.navig")
    assert not mock.verbose
    assert isinstance(mock.get_config_directories(), list)


def test_app_config_provider_has_list_hosts():
    from navig.core.protocols import AppConfigProvider

    mock = MagicMock(spec=AppConfigProvider)
    mock.list_hosts.return_value = ["host-a", "host-b"]
    assert mock.list_hosts() == ["host-a", "host-b"]


def test_app_config_provider_load_host_config():
    from navig.core.protocols import AppConfigProvider

    mock = MagicMock(spec=AppConfigProvider)
    mock.load_host_config.return_value = {"name": "prod"}
    result = mock.load_host_config("prod")
    assert result["name"] == "prod"


def test_app_config_provider_save_host_config():
    from navig.core.protocols import AppConfigProvider

    mock = MagicMock(spec=AppConfigProvider)
    mock.save_host_config("dev", {"name": "dev"})
    mock.save_host_config.assert_called_once_with("dev", {"name": "dev"})


def test_host_config_provider_directory_accessible():
    from navig.core.protocols import HostConfigProvider

    mock = MagicMock(spec=HostConfigProvider)
    mock._is_directory_accessible.return_value = True
    assert mock._is_directory_accessible(Path("/tmp"))
