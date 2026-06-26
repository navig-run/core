"""
tests/agent/test_chat_agentic_routing.py

Unit tests for ConversationalAgent.chat() → run_agentic() routing logic.

Verifies that:
- chat() delegates to run_agentic() when tools are successfully registered
- chat() falls back to single-shot when tool registration fails
- tool registration is lazy (first call only) and idempotent
- run_agentic() is NOT called when registration errors out
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.agent.conv.agent import ConversationalAgent


def _make_agent() -> ConversationalAgent:
    """Construct a ConversationalAgent with minimal real dependencies."""
    ai_client = MagicMock()
    ai_client.chat = AsyncMock(return_value="single-shot response")
    ai_client.chat_routed = AsyncMock(return_value="single-shot response")
    ai_client.model_router = MagicMock()

    agent = ConversationalAgent(
        ai_client=ai_client,
        soul_content="You are NAVIG.",  # skip filesystem soul load
    )
    return agent


class TestChatAgenticRouting:
    @pytest.mark.asyncio
    async def test_delegates_to_run_agentic_when_tools_registered(self):
        """chat() must call run_agentic() when register_all_tools() succeeds."""
        agent = _make_agent()
        assert not agent._agentic_tools_registered

        with (
            patch("navig.agent.tools.register_all_tools") as mock_reg,
            patch.object(agent, "run_agentic", new=AsyncMock(return_value="agentic reply")) as mock_agentic,
        ):
            mock_reg.return_value = None  # successful registration
            result = await agent.chat("ssh into my server and check disk")

        mock_agentic.assert_awaited_once_with(
            "ssh into my server and check disk",
            on_partial=None,
            tier_override="",
            effort="",
        )
        assert result == "agentic reply"

    @pytest.mark.asyncio
    async def test_falls_back_to_single_shot_when_registration_fails(self):
        """chat() must use _get_ai_response() fallback when register_all_tools() raises."""
        agent = _make_agent()

        with (
            patch("navig.agent.tools.register_all_tools", side_effect=ImportError("tools missing")),
            patch.object(agent, "run_agentic", new=AsyncMock(return_value="agentic")) as mock_agentic,
            patch.object(agent, "_get_ai_response", new=AsyncMock(return_value="fallback reply")) as mock_single,
        ):
            result = await agent.chat("hello")

        mock_agentic.assert_not_awaited()
        mock_single.assert_awaited_once()
        assert result == "fallback reply"
        assert not agent._agentic_tools_registered

    @pytest.mark.asyncio
    async def test_lazy_registration_only_runs_once(self):
        """register_all_tools() is called exactly once even across multiple chat() calls."""
        agent = _make_agent()

        with (
            patch("navig.agent.tools.register_all_tools") as mock_reg,
            patch.object(agent, "run_agentic", new=AsyncMock(return_value="ok")),
        ):
            await agent.chat("first message")
            await agent.chat("second message")
            await agent.chat("third message")

        mock_reg.assert_called_once()

    @pytest.mark.asyncio
    async def test_agentic_flag_set_after_successful_registration(self):
        """_agentic_tools_registered becomes True after first successful registration."""
        agent = _make_agent()
        assert not agent._agentic_tools_registered

        with (
            patch("navig.agent.tools.register_all_tools"),
            patch.object(agent, "run_agentic", new=AsyncMock(return_value="ok")),
        ):
            await agent.chat("test")

        assert agent._agentic_tools_registered

    @pytest.mark.asyncio
    async def test_agentic_flag_stays_false_on_registration_error(self):
        """_agentic_tools_registered stays False when registration raises."""
        agent = _make_agent()

        with (
            patch("navig.agent.tools.register_all_tools", side_effect=RuntimeError("crash")),
            patch.object(agent, "_get_ai_response", new=AsyncMock(return_value="ok")),
        ):
            await agent.chat("test")

        assert not agent._agentic_tools_registered

    @pytest.mark.asyncio
    async def test_already_registered_skips_import(self):
        """If _agentic_tools_registered is already True, register_all_tools is not imported."""
        agent = _make_agent()
        agent._agentic_tools_registered = True  # simulate prior registration

        with (
            patch("navig.agent.tools.register_all_tools") as mock_reg,
            patch.object(agent, "run_agentic", new=AsyncMock(return_value="ok")),
        ):
            await agent.chat("follow-up message")

        mock_reg.assert_not_called()
