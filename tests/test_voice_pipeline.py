import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from navig.voice.pipeline import VoicePipeline
from navig.voice.session_manager import VoiceSessionManager

@pytest.fixture
def pipeline():
    return VoicePipeline()

@pytest.mark.asyncio
async def test_process_audio_file(pipeline):
    # Mock STT
    pipeline.stt = AsyncMock()
    pipeline.stt.transcribe.return_value = "Hello world"
    
    # Mock Router
    pipeline.router = AsyncMock()
    pipeline.router.route_message.return_value = "Generated response"
    
    # Mock TTS
    pipeline.tts = AsyncMock()
    pipeline.tts.synthesize.return_value = "/path/to/audio.mp3"
    
    with patch("navig.voice.stt.STT.transcribe") as mock_transcribe, \
         patch("navig.voice.pipeline.VoicePipeline._call_llm") as mock_llm, \
         patch("navig.voice.pipeline.VoicePipeline._call_tts") as mock_tts:
         
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.text = "Hello world"
        mock_transcribe.return_value = mock_result
        mock_llm.return_value = "Generated response"
        mock_tts.return_value = "/path/to/audio.mp3"

        res = await pipeline.process_audio_file(Path("test.wav"))
    
    assert res.transcript == "Hello world"
    assert res.response_text == "Generated response"
    assert res.audio_path == "/path/to/audio.mp3"

@pytest.mark.asyncio
async def test_lifecycle(pipeline):
    with patch("navig.voice.wake_word.WakeWordEngine.start") as mock_start:
        pipeline.wake_engine = MagicMock()
        pipeline.wake_engine.start = mock_start
        await pipeline.start()
        mock_start.assert_called_once()
        
    with patch("navig.voice.wake_word.WakeWordEngine.stop") as mock_stop:
        pipeline.wake_engine = MagicMock()
        pipeline.wake_engine.stop = mock_stop
        await pipeline.stop()
        mock_stop.assert_called_once()
