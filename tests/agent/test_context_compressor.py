"""
Tests for navig.agent.context_compressor — pure helper functions.
"""

from __future__ import annotations

import pytest

from navig.agent.context_compressor import (
    _BASH_HEAD,
    _BASH_TAIL,
    _MODEL_CONTEXT_WINDOWS,
    _estimate_messages_tokens,
    _estimate_tokens,
    _get_context_window,
)


# ─── _MODEL_CONTEXT_WINDOWS ───────────────────────────────────────────────────


def test_model_context_windows_gpt4o():
    assert _MODEL_CONTEXT_WINDOWS["gpt-4o"] == 128_000


def test_model_context_windows_claude():
    assert _MODEL_CONTEXT_WINDOWS["claude-sonnet-4-20250514"] == 200_000


def test_model_context_windows_gemini():
    assert _MODEL_CONTEXT_WINDOWS["gemini-2.5-flash"] == 1_000_000


# ─── _estimate_tokens ─────────────────────────────────────────────────────────


def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 0


def test_estimate_tokens_positive():
    result = _estimate_tokens("hello world")
    assert result > 0


def test_estimate_tokens_proportional():
    short = _estimate_tokens("hi")
    long = _estimate_tokens("hello world this is a longer sentence")
    assert long > short


def test_estimate_tokens_rough_ratio():
    # 3.5 chars per token → "hello" (5 chars) ≈ 1-2 tokens
    result = _estimate_tokens("hello")
    assert 1 <= result <= 3


# ─── _estimate_messages_tokens ────────────────────────────────────────────────


def test_estimate_messages_tokens_empty():
    assert _estimate_messages_tokens([]) == 0


def test_estimate_messages_tokens_single():
    msgs = [{"role": "user", "content": "Hello"}]
    result = _estimate_messages_tokens(msgs)
    assert result > 0


def test_estimate_messages_tokens_overhead():
    # Each message has overhead (4 tokens per message)
    msgs = [{"role": "user", "content": ""}]
    result = _estimate_messages_tokens(msgs)
    assert result >= 4  # at least overhead


def test_estimate_messages_tokens_grows_with_content():
    short = _estimate_messages_tokens([{"role": "user", "content": "hi"}])
    long = _estimate_messages_tokens([{"role": "user", "content": "hi " * 100}])
    assert long > short


def test_estimate_messages_tokens_multiple():
    msgs = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "Python is a programming language."},
    ]
    result = _estimate_messages_tokens(msgs)
    assert result > 10


def test_estimate_messages_tokens_with_tool_calls():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"arguments": '{"param": "value"}'}}
            ],
        }
    ]
    result = _estimate_messages_tokens(msgs)
    assert result > 0


# ─── _get_context_window ──────────────────────────────────────────────────────


def test_get_context_window_exact_match():
    assert _get_context_window("gpt-4o") == 128_000


def test_get_context_window_claude():
    assert _get_context_window("claude-opus-4-20250514") == 200_000


def test_get_context_window_prefix_match():
    result = _get_context_window("claude-3-5-sonnet-20241022")
    assert result == 200_000


def test_get_context_window_provider_slash_model():
    result = _get_context_window("openai/gpt-4o")
    assert result == 128_000


def test_get_context_window_unknown_defaults():
    result = _get_context_window("unknown-model-xyz")
    assert result == 128_000  # safe default


def test_get_context_window_gemini():
    result = _get_context_window("gemini-2.5-pro")
    assert result == 1_000_000


def test_get_context_window_deepseek():
    assert _get_context_window("deepseek-chat") == 128_000


# ─── Constants ────────────────────────────────────────────────────────────────


def test_bash_head_constant():
    assert _BASH_HEAD == 500


def test_bash_tail_constant():
    assert _BASH_TAIL == 200
