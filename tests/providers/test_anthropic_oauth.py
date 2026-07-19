"""
Claude subscription OAuth request rules — pure helpers + a real request
assertion (via httpx MockTransport) proving the exact headers + system framing,
plus retry-on-429 honoring Retry-After. API-key mode must stay unchanged.
"""

from __future__ import annotations

import json

import httpx
import pytest

from navig.providers.anthropic_oauth import (
    CLAUDE_CODE_SYSTEM_TEXT,
    build_oauth_headers,
    compute_backoff,
    frame_oauth_system,
    is_retryable_status,
    parse_retry_after,
    sse_error,
)
from navig.providers.clients import (
    AnthropicClient,
    CompletionRequest,
    Message,
    get_builtin_provider,
)

# ── pure rules ───────────────────────────────────────────────────────────────


def test_oauth_headers_present_as_cli_no_api_key():
    h = build_oauth_headers("tok-123")
    assert h["authorization"] == "Bearer tok-123"
    assert "x-api-key" not in h
    assert h["anthropic-beta"] == "oauth-2025-04-20"
    assert h["anthropic-version"] == "2023-06-01"
    assert h["user-agent"].startswith("claude-cli/") and h["user-agent"].endswith("(external, cli)")
    assert h["x-app"] == "cli"


def test_frame_system_string_puts_identity_first():
    blocks = frame_oauth_system("my instructions")
    assert blocks[0] == {"type": "text", "text": CLAUDE_CODE_SYSTEM_TEXT}
    assert blocks[1] == {"type": "text", "text": "my instructions"}


def test_frame_system_none_is_just_identity():
    assert frame_oauth_system(None) == [{"type": "text", "text": CLAUDE_CODE_SYSTEM_TEXT}]


def test_frame_system_array_dedupes_identity():
    arr = [{"type": "text", "text": CLAUDE_CODE_SYSTEM_TEXT}, {"type": "text", "text": "x"}]
    blocks = frame_oauth_system(arr)
    assert blocks[0]["text"] == CLAUDE_CODE_SYSTEM_TEXT
    assert [b["text"] for b in blocks].count(CLAUDE_CODE_SYSTEM_TEXT) == 1
    assert blocks[-1]["text"] == "x"


@pytest.mark.parametrize("s,exp", [(429, True), (529, True), (500, True), (503, True),
                                   (200, False), (400, False), (401, False)])
def test_retryable_statuses(s, exp):
    assert is_retryable_status(s) is exp


def test_retry_after_and_backoff_honors_it():
    assert parse_retry_after("2") == 2.0
    assert parse_retry_after(None) is None
    assert compute_backoff(0, retry_after=3.0) == 3.0
    assert compute_backoff(99, retry_after=999.0) == 30.0  # capped
    assert 0.0 <= compute_backoff(0) <= 0.5  # full jitter within expo


def test_sse_error_detection():
    assert sse_error({"type": "error", "error": {"type": "overloaded", "message": "busy"}}) == (
        "overloaded", "busy")
    assert sse_error({"type": "message_delta"}) is None


# ── real request assertion (no network) ──────────────────────────────────────


def _anthropic_client(*, oauth_token=None, api_key=None, handler):
    config = get_builtin_provider("anthropic")
    client = AnthropicClient(config, api_key=api_key, oauth_token=oauth_token)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers=client._build_headers(),
    )
    return client


async def test_oauth_request_headers_and_system_framing():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "model": "claude-3-5-haiku-20241022",
        })

    client = _anthropic_client(oauth_token="tok-xyz", handler=handler)
    await client.complete(CompletionRequest(
        messages=[Message(role="system", content="my instructions"),
                  Message(role="user", content="hi")],
        model="claude-3-5-haiku-20241022", max_tokens=1,
    ))
    h = captured["headers"]
    assert h["authorization"] == "Bearer tok-xyz"
    assert "x-api-key" not in h
    assert h["anthropic-beta"] == "oauth-2025-04-20"
    assert h["x-app"] == "cli"
    assert h["user-agent"].startswith("claude-cli/")

    system = captured["body"]["system"]
    assert isinstance(system, list)
    assert system[0] == {"type": "text", "text": CLAUDE_CODE_SYSTEM_TEXT}
    assert system[1]["text"] == "my instructions"


async def test_api_key_request_is_unchanged():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "model": "claude-3-5-haiku-20241022",
        })

    client = _anthropic_client(api_key="sk-ant-key", handler=handler)
    await client.complete(CompletionRequest(
        messages=[Message(role="system", content="plain"),
                  Message(role="user", content="hi")],
        model="claude-3-5-haiku-20241022", max_tokens=1,
    ))
    h = captured["headers"]
    assert h["x-api-key"] == "sk-ant-key"
    assert "authorization" not in h
    assert "anthropic-beta" not in h
    # api-key mode keeps the plain string system (no identity block)
    assert captured["body"]["system"] == "plain"


async def test_retry_on_429_honoring_retry_after():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"},
                                  json={"error": {"type": "rate_limit_error", "message": "slow down"}})
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "model": "claude-3-5-haiku-20241022",
        })

    client = _anthropic_client(oauth_token="tok", handler=handler)
    resp = await client.complete(CompletionRequest(
        messages=[Message(role="user", content="hi")],
        model="claude-3-5-haiku-20241022", max_tokens=1,
    ))
    assert calls["n"] == 2  # retried once then succeeded
    assert resp.content == "ok"
