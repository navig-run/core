from navig.gateway.channels.telegram import TelegramChannel


def test_telegram_channel_exposes_generate_help_delegate():
    text = TelegramChannel._generate_help_text(deck_enabled=False)
    assert isinstance(text, str)
    assert "NAVIG Command Center" in text
    assert "/settings" in text
    for hidden in ("/kick", "/mute", "/unmute", "/search", "/respect", "/stats_global"):
        assert hidden not in text
