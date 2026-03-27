import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.voice.streaming_stt import (
    StreamingProvider,
    StreamingSTT,
    StreamingSTTConfig,
)


@pytest.fixture
def stt_engine():
    config = StreamingSTTConfig(primary=StreamingProvider.WHISPER_API, language="en-US")
    return StreamingSTT(config=config)


@pytest.mark.asyncio
async def test_fallback_whisper(stt_engine):
    with patch("navig.voice.stt.STT.transcribe") as mock_transcribe:
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.text = "Hello world"
        mock_transcribe.return_value = mock_result

        res = await stt_engine._fallback([b"wav_data"])
        assert res.transcript == "Hello world"
