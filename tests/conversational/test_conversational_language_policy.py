import logging

import pytest

from navig.agent.conversational import ConversationalAgent
from navig.memory.key_facts import KeyFact, KeyFactStore

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hello, can you help me?", "en"),
        ("bonjour merci beaucoup", "fr"),
        ("привет как дела", "ru"),
        ("مرحبا كيف الحال", "ar"),
        ("你好，请帮我", "zh"),
        ("hola gracias por favor", "es"),
        ("नमस्ते कृपया मदद करें", "hi"),
        ("こんにちは、手伝って", "ja"),
    ],
)
def test_detect_language_code_matrix(text, expected):
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    assert agent._detect_language_code(text) == expected


def test_mixed_language_falls_back_to_session_language():
    """When the message has mixed scripts, fall back to the session language."""
    agent = ConversationalAgent(ai_client=None, soul_content="test soul")
    agent.set_language_preferences(last_detected_language="fr")

    instruction = agent._build_language_instruction("hello مرحبا")

    # Mixed Latin + Arabic → "mixed" → falls back to session language (fr)
    assert "ABSOLUTE LANGUAGE RULE" in instruction
    assert "French" in instruction


async def test_explicit_override_persists_after_session_reset(tmp_path, monkeypatch):
    store = KeyFactStore(db_path=tmp_path / "kf_override.db")
    try:
        monkeypatch.setattr("navig.agent.conversational.get_key_fact_store", lambda: store)

        agent1 = ConversationalAgent(ai_client=None, soul_content="test soul")
        agent1.set_user_identity(user_id="42", username="operator")
        await agent1.chat("reply in French from now on")

        agent2 = ConversationalAgent(ai_client=None, soul_content="test soul")
        agent2.set_user_identity(user_id="42", username="operator")

        assert agent2._get_pinned_language_override() == "fr"
        instruction = agent2._build_language_instruction("hello")
        # Pinned override forces French regardless of text language
        assert "ABSOLUTE LANGUAGE RULE" in instruction
        assert "French" in instruction
    finally:
        store.close()


async def test_explicit_override_cancel_clears_persistence(tmp_path, monkeypatch):
    store = KeyFactStore(db_path=tmp_path / "kf_override_cancel.db")
    try:
        monkeypatch.setattr("navig.agent.conversational.get_key_fact_store", lambda: store)

        agent1 = ConversationalAgent(ai_client=None, soul_content="test soul")
        agent1.set_user_identity(user_id="42", username="operator")
        await agent1.chat("reply in French from now on")
        await agent1.chat("use auto language")

        agent2 = ConversationalAgent(ai_client=None, soul_content="test soul")
        agent2.set_user_identity(user_id="42", username="operator")

        assert agent2._get_pinned_language_override() == ""
    finally:
        store.close()


def test_invalid_override_code_logged_and_discarded(tmp_path, monkeypatch):
    store = KeyFactStore(db_path=tmp_path / "kf_invalid_override.db")
    try:
        monkeypatch.setattr("navig.agent.conversational.get_key_fact_store", lambda: store)

        fact = KeyFact(
            content="Pinned language override: xx-invalid",
            category="preference",
            confidence=1.0,
            metadata={
                "type": "language_override",
                "language": "xx-invalid",
                "source": "explicit_user_instruction",
                "pinned": True,
                "scope": "user_id:42",
            },
        )
        store.upsert(fact)

        agent = ConversationalAgent(ai_client=None, soul_content="test soul")
        agent.set_user_identity(user_id="42", username="operator")

        warnings = []
        monkeypatch.setattr(
            "navig.agent.conversational.logger.warning",
            lambda *args, **kwargs: warnings.append(args),
        )

        resolved = agent._get_pinned_language_override()

        assert resolved == ""
        assert any("Unrecognized pinned language override" in str(args[0]) for args in warnings)
        assert store.get(fact.id) is not None
        assert store.get(fact.id).deleted is True
    finally:
        store.close()


async def test_no_llm_provider_uses_simple_response_when_client_unavailable(monkeypatch):
    class _NoLLMClient:
        def is_available(self):
            return False

    agent = ConversationalAgent(ai_client=_NoLLMClient(), soul_content="test soul")

    async def _simple(_message: str) -> str:
        return "SIMPLE-FALLBACK"

    monkeypatch.setattr(agent, "_simple_response", _simple)
    result = await agent._get_ai_response("hello")
    assert result == "SIMPLE-FALLBACK"


async def test_no_llm_provider_error_uses_simple_response(monkeypatch):
    class _NoProviderClient:
        def is_available(self):
            return True

        async def chat_routed(self, *args, **kwargs):
            raise RuntimeError("No AI provider available")

    agent = ConversationalAgent(ai_client=_NoProviderClient(), soul_content="test soul")

    async def _simple(_message: str) -> str:
        return "SIMPLE-FALLBACK"

    monkeypatch.setattr(agent, "_simple_response", _simple)
    monkeypatch.setattr(
        "navig.routing.router.get_router",
        lambda: (_ for _ in ()).throw(RuntimeError("router down")),
    )
    result = await agent._get_ai_response("hello")
    assert result == "SIMPLE-FALLBACK"
