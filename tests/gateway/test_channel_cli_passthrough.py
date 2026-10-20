"""A channel without ``allow_cli`` must never reach the ungated CLI subprocess.

``ChannelRouter._execute_navig_command`` runs ``navig <anything>`` with no approval
gate. That is safe for Telegram (``allowed_users``) and the bearer-gated HTTP routes,
and it becomes remote command execution the moment a weaker-identity channel (an SMS
From number, a Slack member) is routed through the same entry point. These tests pin
both directions: the gate closes when asked, and the default leaves today's callers
exactly as they were.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from navig.gateway.channel_router import ChannelRouter


class _FakeAgent:
    def __init__(self):
        self.current_task = None
        self.chats: list[str] = []
        self.on_status_update = None
        self._last_account_fallback = None

    def set_user_identity(self, user_id="", username=""):
        pass

    def set_runtime_persona(self, persona=""):
        pass

    def set_active_persona(self, persona="", soul_content=None, *, space="", cwd=None):
        pass

    def set_language_preferences(self, detected_language="", last_detected_language=""):
        pass

    async def chat(self, message, tier_override="", *, on_partial=None, effort=""):
        self.chats.append(message)
        return "agent-reply"


@pytest.fixture
def router(monkeypatch):
    gateway = SimpleNamespace(
        config_manager=SimpleNamespace(global_config={}),
        config=SimpleNamespace(default_agent="default"),
        run_agent_turn=AsyncMock(return_value="fallback"),
    )
    r = ChannelRouter(gateway)
    agent = _FakeAgent()
    monkeypatch.setattr(r, "_get_conversational_agent", lambda _key: agent)
    monkeypatch.setattr(r, "_record_engagement_interaction", lambda _msg: None)
    execute = AsyncMock(return_value="ran")
    monkeypatch.setattr(r, "_execute_navig_command", execute)
    r._test_agent = agent
    r._test_execute = execute
    return r


@pytest.mark.parametrize("text", ["navig run 'rm -rf /'", "/navig vault list"])
async def test_cli_prefix_is_not_executed_without_allow_cli(router, text):
    reply = await router.route_message("sms", "+15550100", text, allow_cli=False)

    router._test_execute.assert_not_awaited()
    assert reply == "agent-reply"
    assert router._test_agent.chats == [text]


@pytest.mark.parametrize("text", ["clipboard", "/windows", "auto", "workflows", "scripts"])
async def test_shelling_quick_commands_are_not_run_without_allow_cli(router, text):
    await router.route_message("sms", "+15550100", text, allow_cli=False)

    router._test_execute.assert_not_awaited()


async def test_in_process_quick_commands_still_answer_without_allow_cli(router):
    reply = await router.route_message("sms", "+15550100", "ping", allow_cli=False)

    assert "Pong" in reply
    router._test_execute.assert_not_awaited()


async def test_default_keeps_cli_passthrough_for_existing_callers(router):
    reply = await router.route_message("telegram", "42", "navig host list")

    router._test_execute.assert_awaited_once()
    assert reply == "ran"
    assert router._test_agent.chats == []
