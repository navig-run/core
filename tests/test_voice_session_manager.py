import asyncio

import pytest

from navig.voice.session_manager import SessionConfig, VoiceSessionManager


@pytest.fixture
async def manager():
    m = VoiceSessionManager(
        config=SessionConfig(
            silence_timeout_seconds=0.01, max_listen_seconds=0.1, min_audio_ms=0
        )
    )
    await m.start()
    yield m
    await m.stop()


@pytest.mark.asyncio
async def test_session_lifecycle(manager):
    # Initialize session
    session = await manager.activate(keyword="echo", score=0.9)
    assert session is not None
    assert manager.active_session is not None
    await asyncio.sleep(0.02)

    # Feed audio
    await manager.feed_audio(b"audio_bytes", session_id=session.id)
    await asyncio.sleep(0.05)

    # Transition to processing by stopping listening
    await manager.stop_listening(session_id=session.id)
    await asyncio.sleep(0.05)
    assert manager.active_session is None


@pytest.mark.asyncio
async def test_timeout_handling(manager):
    session = await manager.activate(keyword="echo", score=0.9)
    # Let the background loop process timeout
    await asyncio.sleep(0.2)
    # Manager will transition it to IDLE since max timeout reached
    assert manager.active_session is None
