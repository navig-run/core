import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.agent.llm_probe import (
    TIER_GUIDE,
    ProbeResult,
    _pick_chat_model,
    _probe_bridge,
    _probe_free_cloud,
    _probe_ollama,
    _probe_paid,
    _read_navig_config_key,
    probe_llm,
    probe_llm_sync,
)

# ---------------------------------------------------------------------------
# Test _pick_chat_model
# ---------------------------------------------------------------------------


def test_pick_chat_model():
    models1 = ["random", "llama3.2:3b", "llama3:8b"]
    assert _pick_chat_model(models1) == "llama3.2:3b"

    models2 = ["random", "qwen2.5"]
    assert _pick_chat_model(models2) == "qwen2.5"

    models3 = ["unknown_first", "unknown_second"]
    assert _pick_chat_model(models3) == "unknown_first"


# ---------------------------------------------------------------------------
# Test _probe_ollama
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_ollama_success():
    class DummyResponse:
        status_code = 200

        def json(self):
            return {"models": [{"name": "llama3:8b"}, {"name": "mistral"}]}

    class DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            return DummyResponse()

    with patch("httpx.AsyncClient", return_value=DummyClient()):
        res = await _probe_ollama()
        assert res is not None
        assert res.reachable is True
        assert res.tier == "T0"
        assert res.model == "llama3:8b"


@pytest.mark.asyncio
async def test_probe_ollama_no_models():
    class DummyResponse:
        status_code = 200

        def json(self):
            return {"models": []}

    class DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            return DummyResponse()

    with patch("httpx.AsyncClient", return_value=DummyClient()):
        res = await _probe_ollama()
        assert res is not None
        assert res.reachable is True
        assert res.model == "(no models pulled yet)"


@pytest.mark.asyncio
async def test_probe_ollama_failure():
    import httpx

    class DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("Connection failed")

    with patch("httpx.AsyncClient", return_value=DummyClient()):
        assert await _probe_ollama() is None


@pytest.mark.asyncio
async def test_probe_ollama_404():
    class DummyResponse:
        status_code = 404

    class DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            return DummyResponse()

    with patch("httpx.AsyncClient", return_value=DummyClient()):
        assert await _probe_ollama() is None


# ---------------------------------------------------------------------------
# Test Config & Key checks
# ---------------------------------------------------------------------------


def test_read_navig_config_key():
    # Calling it directly will hit the ImportError since navig.config doesn't have `get`.
    # It should cleanly swallow it and return "".
    assert _read_navig_config_key("test") == ""


def test_probe_free_cloud():
    # Only env var
    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_123456"}):
        res = _probe_free_cloud()
        assert res is not None
        assert res.tier == "T1"
        assert "Groq" in res.note

    # Config var
    with (
        patch.dict(os.environ, clear=True),
        patch(
            "navig.agent.llm_probe._read_navig_config_key", return_value="ghp_123456"
        ),
    ):
        res = _probe_free_cloud()
        assert res is not None
        assert res.tier == "T1"

    # None
    with (
        patch.dict(os.environ, clear=True),
        patch("navig.agent.llm_probe._read_navig_config_key", return_value=""),
    ):
        assert _probe_free_cloud() is None


def test_probe_paid():
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-1234567890"}):
        res = _probe_paid()
        assert res is not None
        assert res.tier == "T3"
        assert "OpenAI" in res.note

    with (
        patch.dict(os.environ, clear=True),
        patch("navig.agent.llm_probe._read_navig_config_key", return_value=""),
    ):
        assert _probe_paid() is None


# ---------------------------------------------------------------------------
# Test _probe_bridge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_bridge_success():
    class DummyResponse:
        status_code = 200

    class DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            return DummyResponse()

    with patch("httpx.AsyncClient", return_value=DummyClient()):
        res = await _probe_bridge()
        assert res is not None
        assert res.tier == "T2"


@pytest.mark.asyncio
async def test_probe_bridge_failure():
    import httpx

    class DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("Connection failed")

    with patch("httpx.AsyncClient", return_value=DummyClient()):
        assert await _probe_bridge() is None


# ---------------------------------------------------------------------------
# Test probe_llm wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("navig.agent.llm_probe._probe_ollama", new_callable=AsyncMock)
@patch("navig.agent.llm_probe._probe_bridge", new_callable=AsyncMock)
@patch("navig.agent.llm_probe._probe_free_cloud")
@patch("navig.agent.llm_probe._probe_paid")
async def test_probe_llm_priority(paid_mock, free_mock, bridge_mock, ollama_mock):
    ollama_mock.return_value = ProbeResult(True, "T0", "llama3", "ollama")
    bridge_mock.return_value = None
    free_mock.return_value = None
    paid_mock.return_value = ProbeResult(True, "T3", "gpt-4", "paid")

    # prefer_local=True means T0 over T3
    res1 = await probe_llm(prefer_local=True)
    assert res1.tier == "T0"

    # If prefer_local=False, T0 still over T3, but T1/T2 might come first.
    # Oh wait, the order in code:
    # prefer_local=True -> [T0, T2, T1, T3]
    # prefer_local=False -> [T2, T1, T0, T3]
    # If only T0 and T3 are available, T0 beats T3 either way.

    free_mock.return_value = ProbeResult(True, "T1", "free", "free")
    res2 = await probe_llm(prefer_local=False)
    assert res2.tier == "T1"

    # None available
    ollama_mock.return_value = None
    bridge_mock.return_value = None
    free_mock.return_value = None
    paid_mock.return_value = None
    res3 = await probe_llm()
    assert res3.reachable is False
    assert res3.tier == "none"
    assert TIER_GUIDE in res3.note


# ---------------------------------------------------------------------------
# Test probe_llm_sync wrapper
# ---------------------------------------------------------------------------


def test_probe_llm_sync():
    # Should work in a non-async environment normally
    with (
        patch(
            "navig.agent.llm_probe._probe_paid",
            return_value=ProbeResult(True, "T3", "m", "n"),
        ),
        patch("navig.agent.llm_probe._probe_free_cloud", return_value=None),
        patch(
            "navig.agent.llm_probe._probe_ollama",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "navig.agent.llm_probe._probe_bridge",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):

        res = probe_llm_sync()
        assert res.tier == "T3"


@pytest.mark.asyncio
async def test_probe_llm_sync_in_loop():
    # Calling sync wrapper inside an active event loop
    with (
        patch(
            "navig.agent.llm_probe._probe_paid",
            return_value=ProbeResult(True, "T3", "m", "n"),
        ),
        patch("navig.agent.llm_probe._probe_free_cloud", return_value=None),
        patch(
            "navig.agent.llm_probe._probe_ollama",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "navig.agent.llm_probe._probe_bridge",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):

        res = probe_llm_sync()
        assert res.tier == "T3"
