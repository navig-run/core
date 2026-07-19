from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from navig.gateway.channel_router import ChannelRouter

pytestmark = pytest.mark.integration


class _FakeAgent:
    def __init__(self):
        self.current_task = None
        self.identity_calls = []
        self.persona_calls = []
        self.language_calls = []
        self.on_status_update = None
        # Real ConversationalAgent exposes this; set it to simulate a rotation.
        self._last_account_fallback = None

    def set_user_identity(self, user_id="", username=""):
        self.identity_calls.append((user_id, username))

    def set_runtime_persona(self, persona=""):
        self.persona_calls.append(persona)

    def set_active_persona(self, persona=""):
        self.persona_calls.append(persona)

    def set_language_preferences(self, detected_language="", last_detected_language=""):
        self.language_calls.append((detected_language, last_detected_language))

    async def chat(self, message, tier_override="", *, on_partial=None, effort=""):
        # Mirror the real ConversationalAgent.chat signature (on_partial/effort).
        return "ok"


async def test_handle_message_applies_runtime_persona_from_metadata(monkeypatch):
    gateway = SimpleNamespace(
        config_manager=SimpleNamespace(global_config={}),
        config=SimpleNamespace(default_agent="default"),
        run_agent_turn=AsyncMock(return_value="fallback"),
    )
    router = ChannelRouter(gateway)

    fake_agent = _FakeAgent()
    monkeypatch.setattr(router, "_get_conversational_agent", lambda _key: fake_agent)
    monkeypatch.setattr(router, "_check_quick_commands", AsyncMock(return_value=None))

    response = await router._handle_message(
        agent_id="default",
        session_key="telegram:dm:42",
        message="hello",
        metadata={
            "user_id": 42,
            "username": "operator",
            "auto_reply_persona": "teacher",
        },
    )

    assert response == "ok"
    assert fake_agent.identity_calls[-1] == ("42", "operator")
    assert fake_agent.persona_calls[-1] == "teacher"


async def test_handle_message_clears_runtime_persona_when_not_present(monkeypatch):
    gateway = SimpleNamespace(
        config_manager=SimpleNamespace(global_config={}),
        config=SimpleNamespace(default_agent="default"),
        run_agent_turn=AsyncMock(return_value="fallback"),
    )
    router = ChannelRouter(gateway)

    fake_agent = _FakeAgent()
    monkeypatch.setattr(router, "_get_conversational_agent", lambda _key: fake_agent)
    monkeypatch.setattr(router, "_check_quick_commands", AsyncMock(return_value=None))

    response = await router._handle_message(
        agent_id="default",
        session_key="telegram:dm:42",
        message="hello again",
        metadata={"user_id": 42, "username": "operator"},
    )

    assert response == "ok"
    assert fake_agent.persona_calls[-1] == ""


async def test_handle_message_passes_language_hints(monkeypatch):
    gateway = SimpleNamespace(
        config_manager=SimpleNamespace(global_config={}),
        config=SimpleNamespace(default_agent="default"),
        run_agent_turn=AsyncMock(return_value="fallback"),
    )
    router = ChannelRouter(gateway)

    fake_agent = _FakeAgent()
    monkeypatch.setattr(router, "_get_conversational_agent", lambda _key: fake_agent)
    monkeypatch.setattr(router, "_check_quick_commands", AsyncMock(return_value=None))

    response = await router._handle_message(
        agent_id="default",
        session_key="telegram:dm:42",
        message="bonjour",
        metadata={
            "user_id": 42,
            "username": "operator",
            "detected_language": "fr",
            "last_detected_language": "en",
        },
    )

    assert response == "ok"
    assert fake_agent.language_calls[-1] == ("fr", "en")


async def test_handle_message_surfaces_account_rotation(monkeypatch):
    gateway = SimpleNamespace(
        config_manager=SimpleNamespace(global_config={}),
        config=SimpleNamespace(default_agent="default"),
        run_agent_turn=AsyncMock(return_value="fallback"),
    )
    router = ChannelRouter(gateway)

    fake_agent = _FakeAgent()
    fake_agent._last_account_fallback = {"reason": "rate_limited", "to": "Claude B"}
    monkeypatch.setattr(router, "_get_conversational_agent", lambda _key: fake_agent)
    monkeypatch.setattr(router, "_check_quick_commands", AsyncMock(return_value=None))

    response = await router._handle_message(
        agent_id="default",
        session_key="telegram:dm:42",
        message="do a thing",
        metadata={"user_id": 42, "username": "operator"},
    )

    assert response.startswith("ok")
    assert "answered with Claude B" in response      # rotation surfaced to chat
    assert "rate-limited" in response


def test_rotation_notice_uses_shared_humanizer():
    # The chat notice now shares the canonical describe_category, so it covers
    # categories the old local map missed (e.g. a pre-emptive "cooldown" skip,
    # which used to fall back to a generic "was unavailable").
    from navig.gateway.channel_router import _format_account_rotation_notice
    from navig.llm.fallback_policy import describe_category

    line = _format_account_rotation_notice({"reason": "cooldown", "to": "Claude C"})
    assert line == "↻ Primary account was cooling down from a recent failure — answered with Claude C."
    assert describe_category("cooldown") in line
    # blank reason still reads cleanly
    assert "was unavailable" in _format_account_rotation_notice({"to": "Claude C"})


async def test_handle_message_no_rotation_no_notice(monkeypatch):
    gateway = SimpleNamespace(
        config_manager=SimpleNamespace(global_config={}),
        config=SimpleNamespace(default_agent="default"),
        run_agent_turn=AsyncMock(return_value="fallback"),
    )
    router = ChannelRouter(gateway)

    fake_agent = _FakeAgent()  # _last_account_fallback stays None
    monkeypatch.setattr(router, "_get_conversational_agent", lambda _key: fake_agent)
    monkeypatch.setattr(router, "_check_quick_commands", AsyncMock(return_value=None))

    response = await router._handle_message(
        agent_id="default",
        session_key="telegram:dm:42",
        message="hi",
        metadata={"user_id": 42, "username": "operator"},
    )
    assert response == "ok"        # no rotation → clean reply, no footer
    assert "↻" not in response


def test_format_command_failure_for_unknown_command_is_friendly():
    gateway = SimpleNamespace(
        config_manager=SimpleNamespace(global_config={}),
        config=SimpleNamespace(default_agent="default"),
        run_agent_turn=AsyncMock(return_value="fallback"),
    )
    router = ChannelRouter(gateway)

    output = """
Usage: navig [OPTIONS] COMMAND [ARGS]...
Error: No such command 'hello'. Did you mean 'help', 'db-shell'?
"""

    text = router._format_command_failure("hello", output, 2)
    assert "Unknown command" in text
    assert "`help`" in text
    assert "`db-shell`" in text


async def test_quick_workflows_uses_flow_list_command(monkeypatch):
    gateway = SimpleNamespace(
        config_manager=SimpleNamespace(global_config={}),
        config=SimpleNamespace(default_agent="default"),
        run_agent_turn=AsyncMock(return_value="fallback"),
    )
    router = ChannelRouter(gateway)

    calls = []

    async def _fake_exec(message: str, metadata: dict):
        calls.append(message)
        return "ok"

    monkeypatch.setattr(router, "_execute_navig_command", _fake_exec)
    result = await router._check_quick_commands("workflows", {})

    assert result == "ok"
    assert calls == ["navig flow list --plain"]


def test_format_command_success_for_empty_clipboard_is_clear():
    gateway = SimpleNamespace(
        config_manager=SimpleNamespace(global_config={}),
        config=SimpleNamespace(default_agent="default"),
        run_agent_turn=AsyncMock(return_value="fallback"),
    )
    router = ChannelRouter(gateway)

    text = router._format_command_success("auto clipboard --plain", "\n\n")
    assert text == "📋 Clipboard is empty."
