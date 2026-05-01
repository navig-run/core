"""
Batch 36 — navig/llm_routing_types.py + navig/output_styles._parse_style_file

Covers:
  ModelSelection: defaults, repr
  LLMResult: defaults, total_tokens property
  LLMChunk: defaults
  RoutingContext: defaults
  _parse_style_file: valid frontmatter, body-only, no-body skipped,
                     keep-coding-instructions flag, unreadable returns None
"""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.llm_routing_types import (
    LLMChunk,
    LLMResult,
    ModelSelection,
    RoutingContext,
)
from navig.output_styles import _parse_style_file


# ---------------------------------------------------------------------------
# ModelSelection
# ---------------------------------------------------------------------------

class TestModelSelection:
    def test_required_fields(self):
        ms = ModelSelection(provider_name="openai", model_name="gpt-4o")
        assert ms.provider_name == "openai"
        assert ms.model_name == "gpt-4o"

    def test_defaults(self):
        ms = ModelSelection(provider_name="openai", model_name="gpt-4o")
        assert ms.base_url == ""
        assert ms.api_key_env == ""
        assert ms.tier == ""
        assert ms.strategy_name == ""
        assert ms.is_uncensored is False
        assert ms.metadata == {}

    def test_repr_contains_provider_and_model(self):
        ms = ModelSelection(provider_name="anthropic", model_name="claude-3-5")
        r = repr(ms)
        assert "anthropic" in r
        assert "claude-3-5" in r

    def test_repr_contains_tier(self):
        ms = ModelSelection(provider_name="p", model_name="m", tier="fast")
        assert "fast" in repr(ms)

    def test_custom_temperature(self):
        ms = ModelSelection(provider_name="p", model_name="m", temperature=0.0)
        assert ms.temperature == 0.0

    def test_metadata_custom(self):
        ms = ModelSelection(provider_name="p", model_name="m", metadata={"k": "v"})
        assert ms.metadata["k"] == "v"


# ---------------------------------------------------------------------------
# LLMResult
# ---------------------------------------------------------------------------

class TestLLMResult:
    def test_content_required(self):
        r = LLMResult(content="Hello!")
        assert r.content == "Hello!"

    def test_defaults(self):
        r = LLMResult(content="x")
        assert r.model == ""
        assert r.provider == ""
        assert r.latency_ms == 0
        assert r.prompt_tokens == 0
        assert r.completion_tokens == 0
        assert r.finish_reason == ""
        assert r.is_fallback is False
        assert r.attempts == 1
        assert r.selection is None
        assert r.raw == {}

    def test_total_tokens_sum(self):
        r = LLMResult(content="x", prompt_tokens=100, completion_tokens=50)
        assert r.total_tokens == 150

    def test_total_tokens_zero(self):
        r = LLMResult(content="x")
        assert r.total_tokens == 0

    def test_is_fallback(self):
        r = LLMResult(content="x", is_fallback=True)
        assert r.is_fallback is True

    def test_with_selection(self):
        ms = ModelSelection(provider_name="p", model_name="m")
        r = LLMResult(content="x", selection=ms)
        assert r.selection is ms


# ---------------------------------------------------------------------------
# LLMChunk
# ---------------------------------------------------------------------------

class TestLLMChunk:
    def test_content_required(self):
        c = LLMChunk(content="chunk")
        assert c.content == "chunk"

    def test_defaults(self):
        c = LLMChunk(content="x")
        assert c.model == ""
        assert c.provider == ""
        assert c.finish_reason == ""
        assert c.raw == {}

    def test_finish_reason_set(self):
        c = LLMChunk(content="", finish_reason="stop")
        assert c.finish_reason == "stop"


# ---------------------------------------------------------------------------
# RoutingContext
# ---------------------------------------------------------------------------

class TestRoutingContext:
    def test_all_defaults(self):
        rc = RoutingContext()
        assert rc.user_input == ""
        assert rc.messages == []
        assert rc.mode_hint is None
        assert rc.tier_override is None
        assert rc.model_override is None
        assert rc.provider_override is None
        assert rc.prefer_uncensored is None
        assert rc.temperature is None
        assert rc.max_tokens is None
        assert rc.stream is False
        assert rc.timeout == 120.0
        assert rc.metadata == {}

    def test_custom_fields(self):
        rc = RoutingContext(user_input="hello", stream=True, timeout=30.0)
        assert rc.user_input == "hello"
        assert rc.stream is True
        assert rc.timeout == 30.0


# ---------------------------------------------------------------------------
# _parse_style_file
# ---------------------------------------------------------------------------

class TestParseStyleFile:
    def test_valid_frontmatter_and_body(self, tmp_path):
        f = tmp_path / "concise.md"
        f.write_text(
            "---\nname: concise\ndescription: Short replies\n---\nBe brief.",
            encoding="utf-8",
        )
        style = _parse_style_file(f, source="user")
        assert style is not None
        assert style.name == "concise"
        assert style.description == "Short replies"
        assert style.prompt == "Be brief."
        assert style.source == "user"

    def test_body_only_no_frontmatter(self, tmp_path):
        f = tmp_path / "verbose.md"
        f.write_text("Always explain in detail.", encoding="utf-8")
        style = _parse_style_file(f, source="project")
        assert style is not None
        # name falls back to stem
        assert style.name == "verbose"
        assert "detail" in style.prompt

    def test_empty_body_returns_none(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("---\nname: empty\n---\n", encoding="utf-8")
        style = _parse_style_file(f, source="builtin")
        assert style is None

    def test_keep_coding_instructions_false(self, tmp_path):
        f = tmp_path / "nocoding.md"
        f.write_text(
            "---\nname: nocoding\nkeep-coding-instructions: false\n---\nNo code.",
            encoding="utf-8",
        )
        style = _parse_style_file(f, source="user")
        assert style is not None
        assert style.keep_coding_instructions is False

    def test_keep_coding_instructions_true_default(self, tmp_path):
        f = tmp_path / "withcode.md"
        f.write_text(
            "---\nname: withcode\n---\nAlways show code.",
            encoding="utf-8",
        )
        style = _parse_style_file(f, source="user")
        assert style is not None
        assert style.keep_coding_instructions is True

    def test_nonexistent_file_returns_none(self, tmp_path):
        f = tmp_path / "doesnotexist.md"
        style = _parse_style_file(f, source="user")
        assert style is None

    def test_source_preserved(self, tmp_path):
        f = tmp_path / "mysrc.md"
        f.write_text("---\nname: mysrc\n---\nPrompt content here.", encoding="utf-8")
        style = _parse_style_file(f, source="builtin")
        assert style is not None
        assert style.source == "builtin"
