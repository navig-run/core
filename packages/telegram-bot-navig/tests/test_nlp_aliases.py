"""
tests/test_nlp_aliases.py — Tests for the nlp_aliases plugin.

Verifies that the plugin:
  - Correctly detects NLP trigger words and extracts body text
  - Delegates AI calls to navig.llm_generate.llm_generate (not legacy urllib)
  - Shows a helpful vault-oriented error when no provider is available
  - Handles reply-to-message body extraction

No Telegram token needed — all PTB objects are mocked.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Make imports work from tests/ subdirectory
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from plugins.nlp_aliases import NLPAliasPlugin, _call_llm, _detect  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_update(text: str, reply_text: str | None = None) -> MagicMock:
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))
    if reply_text is not None:
        update.message.reply_to_message = MagicMock()
        update.message.reply_to_message.text = reply_text
        update.message.reply_to_message.caption = None
    else:
        update.message.reply_to_message = None
    return update


def _make_context() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# _detect() unit tests
# ---------------------------------------------------------------------------


def test_detect_explain_inline():
    result = _detect("explain quantum computing")
    assert result is not None
    key, body = result
    assert key == "explain"
    assert body == "quantum computing"


def test_detect_translate_colon():
    result = _detect("translate: bonjour le monde")
    assert result is not None
    key, body = result
    assert key == "translate"
    assert body == "bonjour le monde"


def test_detect_russian_trigger():
    result = _detect("объясни HTTP протоколы")
    assert result is not None
    key, body = result
    assert key == "explain"
    assert body == "HTTP протоколы"


def test_detect_summarize_alias():
    result = _detect("tl;dr This is a very long article about nothing.")
    assert result is not None
    key, body = result
    assert key == "summary"


def test_detect_no_match():
    result = _detect("hello world")
    assert result is None


def test_detect_trigger_without_body():
    result = _detect("explain")
    assert result is None


# ---------------------------------------------------------------------------
# _call_llm() unit tests — mocks navig.llm_generate
# ---------------------------------------------------------------------------


def test_call_llm_delegates_to_llm_generate():
    """_call_llm must call navig.llm_generate.llm_generate, not urllib."""
    import plugins.nlp_aliases as mod

    with patch("navig.llm_generate.llm_generate", return_value="Test response") as mock_gen:
        result = mod._call_llm("Explain gravity")

    mock_gen.assert_called_once()
    call_kwargs = mock_gen.call_args
    msgs = call_kwargs.kwargs.get("messages") or call_kwargs.args[0]
    assert any("gravity" in m.get("content", "") for m in msgs)
    assert result == "Test response"


def test_call_llm_returns_none_on_exception():
    """_call_llm must return None (not raise) when llm_generate fails."""
    import plugins.nlp_aliases as mod

    with patch("navig.llm_generate.llm_generate", side_effect=RuntimeError("no provider")):
        result = mod._call_llm("Explain gravity")
    assert result is None


def test_call_llm_uses_big_tasks_mode():
    """_call_llm should use mode='big_tasks' so the router picks the right tier."""
    import plugins.nlp_aliases as mod

    with patch("navig.llm_generate.llm_generate", return_value="ok") as mock_gen:
        mod._call_llm("some prompt")
    kwargs = mock_gen.call_args.kwargs
    assert kwargs.get("mode") == "big_tasks"


# ---------------------------------------------------------------------------
# NLPAliasPlugin.handle_message integration tests
# ---------------------------------------------------------------------------


def test_handle_message_sends_llm_response():
    """When llm_generate succeeds the plugin edits the status with the response."""
    plugin = NLPAliasPlugin()
    update = _make_update("explain black holes")
    status_mock = MagicMock()
    status_mock.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=status_mock)

    import plugins.nlp_aliases as mod

    with patch.object(mod, "_call_llm", return_value="Black holes are very dense objects."):
        asyncio.run(
            plugin.handle_message(update, _make_context())
        )

    status_mock.edit_text.assert_awaited_once()
    call_text = status_mock.edit_text.call_args.args[0]
    assert "Black holes" in call_text
    assert "Explanation" in call_text


def test_handle_message_no_provider_shows_vault_hint():
    """When _call_llm returns None the error message must mention vault, not config.yaml."""
    plugin = NLPAliasPlugin()
    update = _make_update("explain black holes")
    status_mock = MagicMock()
    status_mock.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=status_mock)

    import plugins.nlp_aliases as mod

    with patch.object(mod, "_call_llm", return_value=None):
        asyncio.run(
            plugin.handle_message(update, _make_context())
        )

    status_mock.edit_text.assert_awaited_once()
    error_text = status_mock.edit_text.call_args.args[0]
    assert "vault" in error_text.lower()
    # Must NOT point users to the old flat config approach
    assert "config.yaml" not in error_text


def test_handle_message_no_body_prompts_user():
    """When there is no inline body and no reply_to_message, prompt the user."""
    plugin = NLPAliasPlugin()
    update = _make_update("explain,")
    update.message.reply_to_message = None

    import plugins.nlp_aliases as mod

    asyncio.run(
        plugin.handle_message(update, _make_context())
    )
    update.message.reply_text.assert_awaited_once()
    prompt_text = update.message.reply_text.call_args.args[0]
    assert "explain" in prompt_text.lower()


def test_handle_message_uses_reply_to_message_body():
    """Body should be taken from reply_to_message when no inline text."""
    plugin = NLPAliasPlugin()
    # "translate" with no body, but reply_to_message has text
    update = _make_update("translate,", reply_text="Bonjour le monde")
    status_mock = MagicMock()
    status_mock.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=status_mock)

    import plugins.nlp_aliases as mod

    with patch.object(mod, "_call_llm", return_value="Hello the world") as mock_llm:
        asyncio.run(
            plugin.handle_message(update, _make_context())
        )

    mock_llm.assert_called_once()
    prompt_sent = mock_llm.call_args.args[0]
    assert "Bonjour le monde" in prompt_sent


def test_handle_message_ignores_non_trigger():
    """Messages that don't match any trigger must not call _call_llm."""
    plugin = NLPAliasPlugin()
    update = _make_update("hello world")

    import plugins.nlp_aliases as mod

    with patch.object(mod, "_call_llm") as mock_llm:
        asyncio.run(
            plugin.handle_message(update, _make_context())
        )

    mock_llm.assert_not_called()
    update.message.reply_text.assert_not_called()
