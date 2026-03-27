from unittest.mock import patch

import pytest

from navig.voice.wake_word import WakeWordConfig, WakeWordDetection, WakeWordEngine


@pytest.fixture
def engine():
    config = WakeWordConfig(
        keyword="echo",
        threshold=0.5,
        cooldown_seconds=1.0,
    )
    return WakeWordEngine(config=config)


@pytest.mark.asyncio
async def test_wake_word_notify_bridge(engine):
    with patch("navig.gateway.routes.voice.PENDING_WAKES", []) as mock_queue:
        engine.config.echo_bridge_url = None
        detection = WakeWordDetection(keyword="echo", score=0.9, timestamp=100.0)
        await engine._notify_bridge(detection)

        assert len(mock_queue) == 1
        assert mock_queue[0].keyword == "echo"
        assert mock_queue[0].score == 0.9
