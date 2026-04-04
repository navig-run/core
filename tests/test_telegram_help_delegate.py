import inspect

from navig.gateway.channels.telegram import TelegramChannel


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
