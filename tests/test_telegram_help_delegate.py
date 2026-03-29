from navig.gateway.channels.telegram import TelegramChannel


def test_telegram_channel_exposes_generate_help_delegate():
    text = TelegramChannel._generate_help_text(deck_enabled=False)
    assert isinstance(text, str)
    assert "things I respond to" in text
