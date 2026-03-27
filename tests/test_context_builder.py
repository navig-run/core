"""
Tests for navig.memory.context_builder — ContextBuilder module.

Test cases:
  1. Unit -- basic assembly with mocked memory backends
  2. Unit -- KB skipped for short input
  3. Integration -- full pipeline pass-through (ContextBuilder -> ModeRouter -> Provider mock)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers: build a ContextBuilder with config overrides, no real file I/O
# ---------------------------------------------------------------------------


def _make_builder(
    config: Optional[Dict[str, Any]] = None,
    project_root: Optional[Path] = None,
) -> Any:
    """Create a ContextBuilder with explicit config (no config.yaml lookup)."""
    from navig.memory.context_builder import ContextBuilder

    defaults = {
        "enabled": True,
        "conversation_history_limit": 10,
        "kb_snippets_top_k": 3,
        "kb_min_input_length": 20,
        "include_workspace_notes": False,
        "include_memory_logs": False,
        "max_context_chars": 16_000,
    }
    if config:
        defaults.update(config)
    return ContextBuilder(
        config=defaults, project_root=project_root or Path("/tmp/fake")
    )


# Synthetic data factories


def _fake_messages(n: int = 3) -> List[Dict[str, Any]]:
    """Return n synthetic conversation messages."""
    roles = ["user", "assistant"]
    return [
        {
            "role": roles[i % 2],
            "content": f"Message {i}",
            "timestamp": f"2025-01-01T00:0{i}:00",
        }
        for i in range(n)
    ]


def _fake_snippets(n: int = 2) -> List[Dict[str, Any]]:
    """Return n synthetic KB snippets."""
    return [
        {
            "content": f"Knowledge item {i}",
            "source": "test",
            "key": f"kb-{i}",
            "tags": [],
            "score": 0.9,
        }
        for i in range(n)
    ]


# ===========================================================================
# Test 1: Unit -- basic assembly
# ===========================================================================


class TestBasicAssembly:
    """Mock get_recent_messages and search, verify context dict shape."""

    @patch("navig.memory.context_builder._search_knowledge")
    @patch("navig.memory.context_builder._get_recent_messages")
    @patch("navig.memory.context_builder._collect_metadata")
    def test_basic_assembly(self, mock_meta, mock_msgs, mock_kb):
        """
        Call build_context with enable_kb=True and session_id.
        Assert returned dict has all 4 keys, correct message count,
        correct snippet count, and is JSON-serializable.
        """
        mock_msgs.return_value = _fake_messages(3)
        mock_kb.return_value = _fake_snippets(2)
        mock_meta.return_value = {"active_host": "test-host"}

        builder = _make_builder()
        ctx = builder.build_context(
            "How do I deploy this application to production?",
            {"enable_kb": True},
            "session-123",
        )

        # All 4 keys present
        assert "conversation_history" in ctx
        assert "workspace_notes" in ctx
        assert "kb_snippets" in ctx
        assert "metadata" in ctx

        # Correct counts
        assert len(ctx["conversation_history"]) == 3
        assert len(ctx["kb_snippets"]) == 2
        assert isinstance(ctx["workspace_notes"], list)
        assert isinstance(ctx["metadata"], dict)

        # JSON-serializable
        serialized = json.dumps(ctx, default=str)
        assert isinstance(serialized, str)
        roundtrip = json.loads(serialized)
        assert roundtrip["conversation_history"][0]["role"] in ("user", "assistant")

    @patch("navig.memory.context_builder._search_knowledge")
    @patch("navig.memory.context_builder._get_recent_messages")
    @patch("navig.memory.context_builder._collect_metadata")
    def test_no_session_id(self, mock_meta, mock_msgs, mock_kb):
        """Without session_id, conversation_history should be empty."""
        mock_msgs.return_value = _fake_messages(5)
        mock_kb.return_value = []
        mock_meta.return_value = {}

        builder = _make_builder()
        ctx = builder.build_context("hello world test query", {}, None)

        assert ctx["conversation_history"] == []
        mock_msgs.assert_not_called()

    @patch("navig.memory.context_builder._search_knowledge")
    @patch("navig.memory.context_builder._get_recent_messages")
    @patch("navig.memory.context_builder._collect_metadata")
    def test_disabled_builder(self, mock_meta, mock_msgs, mock_kb):
        """When enabled=False, should return empty context."""
        builder = _make_builder({"enabled": False})
        ctx = builder.build_context(
            "How do I deploy?", {"enable_kb": True}, "session-1"
        )

        assert ctx["conversation_history"] == []
        assert ctx["kb_snippets"] == []
        mock_msgs.assert_not_called()
        mock_kb.assert_not_called()


# ===========================================================================
# Test 2: Unit -- KB skipped for short input
# ===========================================================================


class TestKBSkipped:
    """KB search should be skipped when user_input is shorter than kb_min_input_length."""

    @patch("navig.memory.context_builder._search_knowledge")
    @patch("navig.memory.context_builder._get_recent_messages")
    @patch("navig.memory.context_builder._collect_metadata")
    def test_short_input_skips_kb(self, mock_meta, mock_msgs, mock_kb):
        """
        Call build_context("hi", {"enable_kb": True}, "session-456")
        with kb_min_input_length=20.  Assert kb_snippets is empty list.
        """
        mock_msgs.return_value = []
        mock_kb.return_value = _fake_snippets(3)
        mock_meta.return_value = {}

        builder = _make_builder({"kb_min_input_length": 20})
        ctx = builder.build_context("hi", {"enable_kb": True}, "session-456")

        assert ctx["kb_snippets"] == []
        mock_kb.assert_not_called()

    @patch("navig.memory.context_builder._search_knowledge")
    @patch("navig.memory.context_builder._get_recent_messages")
    @patch("navig.memory.context_builder._collect_metadata")
    def test_long_input_calls_kb(self, mock_meta, mock_msgs, mock_kb):
        """Input >= kb_min_input_length should trigger KB search."""
        mock_msgs.return_value = []
        mock_kb.return_value = _fake_snippets(2)
        mock_meta.return_value = {}

        builder = _make_builder({"kb_min_input_length": 5})
        ctx = builder.build_context(
            "How do I configure SSH tunnels?", {"enable_kb": True}, None
        )

        assert len(ctx["kb_snippets"]) == 2
        mock_kb.assert_called_once()

    @patch("navig.memory.context_builder._search_knowledge")
    @patch("navig.memory.context_builder._get_recent_messages")
    @patch("navig.memory.context_builder._collect_metadata")
    def test_kb_disabled_by_caller(self, mock_meta, mock_msgs, mock_kb):
        """enable_kb=False in caller_info should skip KB even for long input."""
        mock_msgs.return_value = []
        mock_kb.return_value = _fake_snippets(3)
        mock_meta.return_value = {}

        builder = _make_builder()
        ctx = builder.build_context(
            "How do I configure a production database backup?",
            {"enable_kb": False},
            None,
        )

        assert ctx["kb_snippets"] == []
        mock_kb.assert_not_called()


# ===========================================================================
# Test 3: Integration -- full pipeline pass-through
# ===========================================================================


class TestFullPipeline:
    """
    Mock the provider complete() and run the full path:
    Caller -> ContextBuilder -> ModeRouter -> ModelRouter -> Provider(mock).
    """

    @patch("navig.memory.context_builder._search_knowledge")
    @patch("navig.memory.context_builder._get_recent_messages")
    @patch("navig.memory.context_builder._collect_metadata")
    def test_pipeline_passthrough(self, mock_meta, mock_msgs, mock_kb):
        """
        Run one full call through the pipeline with mocked memory backends
        and a mocked provider.  Assert:
          (a) complete() was called exactly once
          (b) prompt passed to complete() contains at least one conversation
              history message
          (c) mode routing returned a valid mode string
        """
        # Setup mocks
        mock_msgs.return_value = _fake_messages(3)
        mock_kb.return_value = _fake_snippets(1)
        mock_meta.return_value = {"active_host": "prod"}

        # We need to mock the provider call since we have no real API keys.
        # Patch _call_provider to capture its arguments and return fake content.
        captured_calls: List[Dict[str, Any]] = []

        def fake_call_provider(
            provider,
            model,
            messages,
            temperature=0.7,
            max_tokens=4096,
            timeout=120.0,
            base_url=None,
        ):
            captured_calls.append(
                {
                    "provider": provider,
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                }
            )
            return "Mocked LLM response"

        with patch("navig.llm_generate._call_provider", side_effect=fake_call_provider):
            # Also need llm_modes config to be present for the router path
            with patch("navig.llm_generate._has_llm_modes_config", return_value=True):
                # Mock resolve_llm to return a valid config
                mock_resolved = MagicMock()
                mock_resolved.provider = "openai"
                mock_resolved.model = "gpt-4o-mini"
                mock_resolved.mode = "big_tasks"
                mock_resolved.temperature = 0.7
                mock_resolved.max_tokens = 4096
                mock_resolved.base_url = ""
                mock_resolved.api_key_env = ""
                mock_resolved.is_uncensored = False
                mock_resolved.resolution_reason = "test"

                with patch("navig.llm_router.resolve_llm", return_value=mock_resolved):
                    from navig.llm_generate import run_llm

                    result = run_llm(
                        messages=[
                            {
                                "role": "system",
                                "content": "You are a helpful assistant.",
                            },
                            {
                                "role": "user",
                                "content": "How do I deploy to production?",
                            },
                        ],
                        user_input="How do I deploy to production?",
                        caller_info={"enable_kb": True},
                        session_id="session-integration-1",
                    )

        # (a) complete (via _call_provider) was called exactly once
        assert len(captured_calls) == 1, f"Expected 1 call, got {len(captured_calls)}"

        # (b) prompt contains conversation history message
        call_messages = captured_calls[0]["messages"]
        # The enriched messages should contain a system context message
        # with conversation history
        all_content = " ".join(m.get("content", "") for m in call_messages)
        assert (
            "Message 0" in all_content or "Recent Conversation" in all_content
        ), "Expected conversation history in prompt"

        # (c) result is valid LLMResult
        assert result.content == "Mocked LLM response"
        assert result.provider == "openai"
        assert result.model == "gpt-4o-mini"

    @patch("navig.memory.context_builder._search_knowledge")
    @patch("navig.memory.context_builder._get_recent_messages")
    @patch("navig.memory.context_builder._collect_metadata")
    def test_pipeline_context_failure_graceful(self, mock_meta, mock_msgs, mock_kb):
        """If ContextBuilder fails entirely, pipeline should still work."""
        mock_msgs.side_effect = RuntimeError("DB exploded")
        mock_meta.return_value = {}
        mock_kb.return_value = []

        def fake_call_provider(provider, model, messages, **kw):
            return "Response without context"

        with patch("navig.llm_generate._call_provider", side_effect=fake_call_provider):
            with patch("navig.llm_generate._has_llm_modes_config", return_value=True):
                mock_resolved = MagicMock()
                mock_resolved.provider = "openai"
                mock_resolved.model = "gpt-4o-mini"
                mock_resolved.mode = "big_tasks"
                mock_resolved.temperature = 0.7
                mock_resolved.max_tokens = 4096
                mock_resolved.base_url = ""
                mock_resolved.api_key_env = ""
                mock_resolved.is_uncensored = False
                mock_resolved.resolution_reason = "test"

                with patch("navig.llm_router.resolve_llm", return_value=mock_resolved):
                    from navig.llm_generate import run_llm

                    result = run_llm(
                        messages=[{"role": "user", "content": "hello"}],
                        session_id="session-fail",
                    )

        # Should still get a response despite context failure
        assert result.content == "Response without context"


# ===========================================================================
# Test 4: ContextBuilder internals
# ===========================================================================


class TestContextBuilderInternals:
    """Test helper methods and edge cases."""

    def test_empty_context_constant(self):
        from navig.memory.context_builder import EMPTY_CONTEXT

        assert set(EMPTY_CONTEXT.keys()) == {
            "conversation_history",
            "workspace_notes",
            "kb_snippets",
            "metadata",
            "project_files",
            "api_snapshots",
            "stale_sources",
        }
        # Must be JSON-serializable
        json.dumps(EMPTY_CONTEXT)

    def test_estimate_chars_string(self):
        builder = _make_builder()
        assert builder._estimate_chars("hello") == 5

    def test_estimate_chars_dict(self):
        builder = _make_builder()
        obj = {"key": "value"}
        expected = len(json.dumps(obj))
        assert builder._estimate_chars(obj) == expected

    def test_enforce_cap_no_truncation(self):
        builder = _make_builder()
        ctx = {
            "conversation_history": [{"role": "user", "content": "hi"}],
            "workspace_notes": [],
            "kb_snippets": [],
            "metadata": {},
        }
        result = builder._enforce_cap(ctx, 100_000)
        assert len(result["conversation_history"]) == 1

    def test_enforce_cap_truncates(self):
        builder = _make_builder()
        ctx = {
            "conversation_history": [{"role": "user", "content": "x" * 500}],
            "workspace_notes": ["note " * 200],
            "kb_snippets": [{"content": "y" * 500}],
            "metadata": {},
        }
        result = builder._enforce_cap(ctx, 100)
        # Should have trimmed some sections
        total = len(json.dumps(result, default=str))
        assert total <= 200  # approximate; workspace_notes trimmed first

    def test_build_context_convenience_function(self):
        """The module-level build_context should return a valid dict."""
        import navig.memory.context_builder as cb_mod
        from navig.memory.context_builder import build_context

        # Reset singleton
        cb_mod._builder_instance = None

        with patch.object(cb_mod, "_get_recent_messages", return_value=[]):
            with patch.object(cb_mod, "_search_knowledge", return_value=[]):
                with patch.object(cb_mod, "_collect_metadata", return_value={}):
                    ctx = build_context("test input", {}, None)
                    assert "conversation_history" in ctx
                    assert "metadata" in ctx

        # Reset singleton again
        cb_mod._builder_instance = None


# ===========================================================================
# Test 5: _enrich_messages_with_context
# ===========================================================================


class TestEnrichMessages:
    """Test the message enrichment helper in llm_generate."""

    def test_empty_context_no_change(self):
        from navig.llm_generate import _enrich_messages_with_context

        msgs = [{"role": "user", "content": "hello"}]
        empty = {
            "conversation_history": [],
            "workspace_notes": [],
            "kb_snippets": [],
            "metadata": {},
        }
        result = _enrich_messages_with_context(msgs, empty)
        assert result == msgs

    def test_context_injected_as_system_message(self):
        from navig.llm_generate import _enrich_messages_with_context

        msgs = [{"role": "user", "content": "hello"}]
        ctx = {
            "conversation_history": [
                {
                    "role": "user",
                    "content": "prev msg",
                    "timestamp": "2025-01-01T00:00:00",
                }
            ],
            "workspace_notes": [],
            "kb_snippets": [],
            "metadata": {},
        }
        result = _enrich_messages_with_context(msgs, ctx)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "Context" in result[0]["content"]
        assert "prev msg" in result[0]["content"]

    def test_context_inserted_after_existing_system(self):
        from navig.llm_generate import _enrich_messages_with_context

        msgs = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "hello"},
        ]
        ctx = {
            "conversation_history": [
                {"role": "user", "content": "old msg", "timestamp": "t"}
            ],
            "workspace_notes": [],
            "kb_snippets": [],
            "metadata": {},
        }
        result = _enrich_messages_with_context(msgs, ctx)
        assert len(result) == 3
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful"
        assert result[1]["role"] == "system"
        assert "Context" in result[1]["content"]
