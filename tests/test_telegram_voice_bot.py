from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.integrations.telegram_voice_bot import TelegramVoiceBot, VoiceBotConfig


@pytest.fixture
def bot():
    config = VoiceBotConfig(allowed_chat_ids={-123})
    return TelegramVoiceBot(config=config)


@pytest.mark.asyncio
async def test_handle_voice_message(bot):
    with (
        patch(
            "navig.integrations.telegram_voice_bot.TelegramVoiceBot._transcribe"
        ) as mock_stt,
        patch(
            "navig.integrations.telegram_voice_bot.TelegramVoiceBot._call_llm"
        ) as mock_llm,
        patch(
            "navig.integrations.telegram_voice_bot.TelegramVoiceBot._call_tts"
        ) as mock_tts,
    ):

        import tempfile
        from pathlib import Path

        tmp_dir = Path(tempfile.gettempdir())
        dummy_in = tmp_dir / "navig_tg_voice_999.oga"
        dummy_in.write_bytes(b"dummy")
        dummy_out = tmp_dir / "audio_out.ogg"
        dummy_out.write_bytes(b"dummy")

        mock_stt.return_value = "Hello bot"
        mock_llm.return_value = "Hello human"
        mock_tts.return_value = str(dummy_out)

        update = MagicMock()
        update.effective_chat.id = -123
        update.message.message_id = 999
        update.message.from_user.id = -123
        update.message.voice.file_id = "test_file_id"
        update.message.voice.duration = 5
        update.message.video_note = None
        update.message.reply_text = AsyncMock()
        update.message.reply_audio = AsyncMock()

        context = MagicMock()
        context.bot.send_chat_action = AsyncMock()
        context.bot.send_voice = AsyncMock()
        context.bot.get_file = AsyncMock(
            return_value=MagicMock(download_to_drive=AsyncMock(return_value=None))
        )

        await bot._handle_voice(update, context)

        context.bot.send_voice.assert_called_once()
        # print(context.bot.send_voice.call_args)


@pytest.mark.asyncio
async def test_unauthorized_user(bot):
    update = MagicMock()
    update.effective_chat.id = 999
    update.message.from_user.id = 999  # Not allowed
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await bot._handle_voice(update, context)
    update.message.reply_text.assert_not_called()
