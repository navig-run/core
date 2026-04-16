import inspect

from navig.gateway.channels.telegram import TelegramChannel
import pytest

pytestmark = pytest.mark.integration


def test_telegram_channel_has_handle_ping_delegate():
    """TelegramChannel must define _handle_ping so /ping fast-path doesn't crash."""
    assert hasattr(TelegramChannel, "_handle_ping"), (
        "TelegramChannel is missing _handle_ping; /ping will raise AttributeError"
    )
    assert inspect.iscoroutinefunction(TelegramChannel._handle_ping)


def test_telegram_channel_exposes_generate_help_delegate():
    text = TelegramChannel._generate_help_text(deck_enabled=False)
    assert isinstance(text, str)
    assert "NAVIG Command Center" in text
    assert "/settings" in text
    for hidden in ("/kick", "/mute", "/unmute", "/search", "/respect", "/stats_global"):
        assert hidden not in text


def test_telegram_channel_has_pending_api_key_delegate():
    """TelegramChannel must define _handle_pending_api_key_input delegate."""
    assert hasattr(TelegramChannel, "_handle_pending_api_key_input"), (
        "TelegramChannel is missing _handle_pending_api_key_input; "
        "pending API-key flow will raise AttributeError"
    )
    assert inspect.iscoroutinefunction(TelegramChannel._handle_pending_api_key_input)


def test_telegram_channel_has_infer_nl_space_intent_delegate():
    """TelegramChannel must define _infer_nl_space_intent delegate."""
    assert hasattr(TelegramChannel, "_infer_nl_space_intent"), (
        "TelegramChannel is missing _infer_nl_space_intent; "
        "NL routing will raise AttributeError"
    )


def test_telegram_channel_has_detect_space_from_text_delegate():
    """TelegramChannel must define _detect_space_from_text delegate."""
    assert hasattr(TelegramChannel, "_detect_space_from_text"), (
        "TelegramChannel is missing _detect_space_from_text; "
        "space detection in NL routing will raise AttributeError"
    )


def test_telegram_channel_has_nl_helper_delegates():
    """TelegramChannel must expose NL helper delegates used by mixin call-chain."""
    required = (
        "_sniff_reminder_intent",
        "_handle_remindme",
        "_nl_phrase_aliases",
        "_extract_nl_args",
        "_resolve_nl_command_intent",
        "_suggest_nl_commands",
        "_nl_command_keyboard",
        "_queue_nl_risky_command_confirmation",
        "_execute_nl_registry_command",
        "_execute_nl_pending_after_delay",
    )
    missing = [name for name in required if not hasattr(TelegramChannel, name)]
    assert not missing, f"TelegramChannel missing NL delegates: {missing}"


def test_telegram_channel_delegated_nl_space_intent_works_without_nl_attrs():
    """Delegated NL intent parsing must work on TelegramChannel without _NL_* attrs."""
    channel = TelegramChannel(bot_token="123456:ABCDEF")
    intent, space = channel._infer_nl_space_intent("switch to finance space")

    assert intent == "space"
    assert space == "finance"
