"""
Tests for the mock LLM server and streaming provider integration.

Covers:
  - MockLLMServer lifecycle (start/stop/context manager)
  - OpenAI-compatible non-streaming endpoint
  - OpenAI-compatible streaming endpoint (SSE)
  - Anthropic-compatible non-streaming endpoint
  - Anthropic-compatible streaming endpoint (SSE)
  - Error status overrides
"""

from __future__ import annotations

import json

import pytest

from tests.fixtures.mock_llm_server import MockLLMServer

# Skip the whole module if httpx is not installed
httpx = pytest.importorskip("httpx")


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture()
def llm_server():
    """Start a mock LLM server for the test."""
    with MockLLMServer(canned_content="Hello from mock") as srv:
        yield srv


# ── Server lifecycle ─────────────────────────────────────────


def test_server_starts_and_stops():
    srv = MockLLMServer()
    srv.start()
    assert srv.port > 0
    assert srv.base_url.startswith("http://")
    srv.stop()


def test_server_context_manager():
    with MockLLMServer() as srv:
        assert srv.port > 0
    # After exit, server should be stopped (no assertion needed, just no error)


# ── OpenAI non-streaming ────────────────────────────────────


def test_openai_completion(llm_server: MockLLMServer):
    response = httpx.post(
        f"{llm_server.base_url}/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "Hello from mock"
    assert data["model"] == "test-model"
    assert data["usage"]["prompt_tokens"] == 10


def test_openai_request_recorded(llm_server: MockLLMServer):
    httpx.post(
        f"{llm_server.base_url}/chat/completions",
        json={"model": "m", "messages": [{"role": "user", "content": "test"}]},
    )
    assert llm_server.request_count == 1
    assert llm_server.last_request["model"] == "m"


# ── OpenAI streaming ────────────────────────────────────────


def test_openai_streaming(llm_server: MockLLMServer):
    with httpx.stream(
        "POST",
        f"{llm_server.base_url}/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        chunks = []
        for line in response.iter_lines():
            line = line.strip()
            if not line or not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            data = json.loads(payload)
            delta = data.get("choices", [{}])[0].get("delta", {})
            if "content" in delta:
                chunks.append(delta["content"])

        reassembled = "".join(chunks)
        assert reassembled == "Hello from mock"


# ── Anthropic non-streaming ─────────────────────────────────


def test_anthropic_completion(llm_server: MockLLMServer):
    response = httpx.post(
        f"{llm_server.base_url}/v1/messages",
        json={
            "model": "claude-mock",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["content"][0]["text"] == "Hello from mock"
    assert data["model"] == "claude-mock"
    assert data["usage"]["input_tokens"] == 10


# ── Anthropic streaming ─────────────────────────────────────


def test_anthropic_streaming(llm_server: MockLLMServer):
    with httpx.stream(
        "POST",
        f"{llm_server.base_url}/v1/messages",
        json={
            "model": "claude-mock",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        text_chunks = []
        got_message_start = False
        got_message_delta = False

        for line in response.iter_lines():
            line = line.strip()
            if not line or not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            event_type = event.get("type", "")

            if event_type == "message_start":
                got_message_start = True
            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text_chunks.append(delta["text"])
            elif event_type == "message_delta":
                got_message_delta = True

        assert got_message_start
        assert got_message_delta
        reassembled = "".join(text_chunks)
        assert reassembled == "Hello from mock"


# ── Error overrides ──────────────────────────────────────────


def test_status_override():
    with MockLLMServer(status_override=429) as srv:
        response = httpx.post(
            f"{srv.base_url}/chat/completions",
            json={"model": "m", "messages": []},
        )
        assert response.status_code == 429
        assert "error" in response.json()


def test_dynamic_content_change(llm_server: MockLLMServer):
    """Verify set_canned_content updates responses on the fly."""
    llm_server.set_canned_content("Updated response")

    response = httpx.post(
        f"{llm_server.base_url}/chat/completions",
        json={"model": "m", "messages": [{"role": "user", "content": "test"}]},
    )
    assert response.json()["choices"][0]["message"]["content"] == "Updated response"


def test_404_on_unknown_path(llm_server: MockLLMServer):
    response = httpx.post(
        f"{llm_server.base_url}/v1/unknown",
        json={},
    )
    assert response.status_code == 404
