from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from navig.gateway.channel_router import ChannelRouter


class _FakeAgent:
    def __init__(self):
        self.current_task = None
        self.identity_calls = []
        self.persona_calls = []
        self.on_status_update = None

    def set_user_identity(self, user_id="", username=""):
        self.identity_calls.append((user_id, username))

    def set_runtime_persona(self, persona=""):
        self.persona_calls.append(persona)

    async def chat(self, message, tier_override=""):
        return "ok"


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
