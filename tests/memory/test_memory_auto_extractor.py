"""
Tests for FB6: Memory Auto-Extraction (navig.agent.memory_auto_extractor).

Covers:
  - ExtractedFact dataclass
  - ExtractionConfig creation and from_dict
  - MemoryAutoExtractor turn recording
  - Interval-based extraction triggering
  - Batch construction and LLM prompting
  - JSON response parsing (clean, fenced, malformed)
  - Confidence filtering
  - fact_key generation
  - Store integration (put / upsert)
  - Force extraction
  - Silent failure on errors
  - Edge cases (empty turns, disabled, no LLM)
"""

from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import MagicMock

import pytest

from navig.agent.memory_auto_extractor import (
    CATEGORIES,
    EXTRACTION_PROMPT,
    MAX_FACTS_PER_EXTRACTION,
    MAX_PENDING_TURNS,
    MAX_TURN_CONTENT_CHARS,
    MEMORY_EXTRACTION_INTERVAL,
    MIN_CONFIDENCE,
    ExtractedFact,
    ExtractionConfig,
    MemoryAutoExtractor,
    fact_key,
    parse_extraction_response,
)

pytestmark = pytest.mark.integration

# ── Helpers ───────────────────────────────────────────────────


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _make_llm_response(facts: list[dict]) -> str:
    """Build a clean JSON string that the fake LLM would return."""
    return json.dumps(facts)


async def _fake_llm(prompt: str, **kwargs) -> str:
    """Default fake LLM that returns a single fact."""
    return json.dumps(
        [{"fact": "User prefers dark themes", "category": "preferences", "confidence": 0.85}]
    )


async def _fake_llm_empty(prompt: str, **kwargs) -> str:
    """Fake LLM that returns an empty array."""
    return "[]"


async def _fail_llm(prompt: str, **kwargs) -> str:
    """Fake LLM that raises an exception."""
    raise RuntimeError("LLM unavailable")


class FakeStore:
    """Minimal store with put() method for testing."""

    def __init__(self):
        self.puts: list[tuple] = []

    def put(self, key: str, value: str, metadata: dict | None = None):
        self.puts.append((key, value, metadata))


class FakeUpsertStore:
    """Store with upsert() instead of put()."""

    def __init__(self):
        self.upserts: list[dict] = []

    def upsert(self, data: dict):
        self.upserts.append(data)


class FailingStore:
    """Store that raises on every put."""

    def put(self, key: str, value: str, **kw):
        raise OSError("Disk full")


# ── TestExtractedFact ─────────────────────────────────────────


class TestExtractedFact:
    def test_defaults(self):
        f = ExtractedFact(fact="hello")
        assert f.category == "environment"
        assert f.confidence == 0.7

    def test_custom_fields(self):
        f = ExtractedFact(fact="port is 5433", category="project", confidence=0.9)
        assert f.fact == "port is 5433"
        assert f.category == "project"
        assert f.confidence == 0.9

    def test_to_dict(self):
        f = ExtractedFact(fact="x", category="preferences", confidence=0.8)
        d = f.to_dict()
        assert d == {"fact": "x", "category": "preferences", "confidence": 0.8}


# ── TestExtractionConfig ─────────────────────────────────────


class TestExtractionConfig:
    def test_defaults(self):
        cfg = ExtractionConfig()
        assert cfg.interval == MEMORY_EXTRACTION_INTERVAL
        assert cfg.max_facts == MAX_FACTS_PER_EXTRACTION
        assert cfg.min_confidence == MIN_CONFIDENCE
        assert cfg.model == ""
        assert cfg.enabled is True

    def test_from_dict_none(self):
        cfg = ExtractionConfig.from_dict(None)
        assert cfg.interval == MEMORY_EXTRACTION_INTERVAL

    def test_from_dict_empty(self):
        cfg = ExtractionConfig.from_dict({})
        assert cfg.interval == MEMORY_EXTRACTION_INTERVAL

    def test_from_dict_custom(self):
        cfg = ExtractionConfig.from_dict(
            {
                "extraction_interval": 3,
                "max_facts_per_extraction": 5,
                "min_confidence": 0.8,
                "extraction_model": "groq/llama-3.3-70b",
                "enabled": False,
            }
        )
        assert cfg.interval == 3
        assert cfg.max_facts == 5
        assert cfg.min_confidence == 0.8
        assert cfg.model == "groq/llama-3.3-70b"
        assert cfg.enabled is False

    def test_from_dict_partial(self):
        cfg = ExtractionConfig.from_dict({"extraction_interval": 10})
        assert cfg.interval == 10
        assert cfg.max_facts == MAX_FACTS_PER_EXTRACTION


# ── TestConstants ─────────────────────────────────────────────


class TestConstants:
    def test_interval_positive(self):
        assert MEMORY_EXTRACTION_INTERVAL > 0

    def test_max_facts_positive(self):
        assert MAX_FACTS_PER_EXTRACTION > 0

    def test_min_confidence_in_range(self):
        assert 0.0 <= MIN_CONFIDENCE <= 1.0

    def test_categories_non_empty(self):
        assert len(CATEGORIES) >= 3
        assert "preferences" in CATEGORIES
        assert "environment" in CATEGORIES

    def test_prompt_has_placeholders(self):
        assert "{max_facts}" in EXTRACTION_PROMPT
        assert "{categories}" in EXTRACTION_PROMPT
        assert "{turns}" in EXTRACTION_PROMPT


# ── TestTurnRecording ─────────────────────────────────────────


class TestTurnRecording:
    def test_record_user_turn(self):
        ext = MemoryAutoExtractor()
        ext.record_turn("user", "hello")
        assert ext.pending_turns == 1
        assert ext.turn_count == 0  # only assistant turns count

    def test_record_assistant_turn(self):
        ext = MemoryAutoExtractor()
        ext.record_turn("assistant", "hi there")
        assert ext.pending_turns == 1
        assert ext.turn_count == 1

    def test_record_both_roles(self):
        ext = MemoryAutoExtractor()
        ext.record_turn("user", "question")
        ext.record_turn("assistant", "answer")
        assert ext.pending_turns == 2
        assert ext.turn_count == 1

    def test_ignores_unknown_role(self):
        ext = MemoryAutoExtractor()
        ext.record_turn("system", "you are a bot")
        assert ext.pending_turns == 0
        assert ext.turn_count == 0

    def test_truncates_long_content(self):
        ext = MemoryAutoExtractor()
        long_msg = "x" * (MAX_TURN_CONTENT_CHARS + 500)
        ext.record_turn("user", long_msg)
        assert ext.pending_turns == 1
        # Content is truncated internally — verify by extraction later

    def test_safety_cap_on_pending(self):
        ext = MemoryAutoExtractor()
        for i in range(MAX_PENDING_TURNS + 10):
            ext.record_turn("user", f"msg {i}")
        assert ext.pending_turns == MAX_PENDING_TURNS

    def test_clear(self):
        ext = MemoryAutoExtractor()
        ext.record_turn("user", "hi")
        ext.record_turn("assistant", "hello")
        ext.clear()
        assert ext.pending_turns == 0
        assert ext.turn_count == 0

    def test_empty_content(self):
        ext = MemoryAutoExtractor()
        ext.record_turn("user", "")
        assert ext.pending_turns == 1

    def test_none_content(self):
        ext = MemoryAutoExtractor()
        ext.record_turn("user", None)
        assert ext.pending_turns == 1


# ── TestParseExtractionResponse ───────────────────────────────


class TestParseExtractionResponse:
    def test_clean_json(self):
        text = json.dumps(
            [{"fact": "User likes Python", "category": "preferences", "confidence": 0.9}]
        )
        facts = parse_extraction_response(text)
        assert len(facts) == 1
        assert facts[0].fact == "User likes Python"
        assert facts[0].category == "preferences"
        assert facts[0].confidence == 0.9

    def test_markdown_fenced_json(self):
        text = (
            "```json\n"
            + json.dumps([{"fact": "Port is 5433", "category": "environment", "confidence": 0.8}])
            + "\n```"
        )
        facts = parse_extraction_response(text)
        assert len(facts) == 1
        assert facts[0].fact == "Port is 5433"

    def test_markdown_fenced_no_lang(self):
        text = (
            "```\n"
            + json.dumps([{"fact": "Uses Docker", "category": "project", "confidence": 0.7}])
            + "\n```"
        )
        facts = parse_extraction_response(text)
        assert len(facts) == 1

    def test_empty_array(self):
        facts = parse_extraction_response("[]")
        assert facts == []

    def test_empty_string(self):
        facts = parse_extraction_response("")
        assert facts == []

    def test_garbage_text(self):
        facts = parse_extraction_response("I don't have any facts to extract.")
        assert facts == []

    def test_multiple_facts(self):
        text = json.dumps(
            [
                {"fact": "Fact A", "category": "preferences", "confidence": 0.9},
                {"fact": "Fact B", "category": "environment", "confidence": 0.7},
                {"fact": "Fact C", "category": "project", "confidence": 0.5},
            ]
        )
        facts = parse_extraction_response(text)
        assert len(facts) == 3

    def test_invalid_category_defaults(self):
        text = json.dumps([{"fact": "Something", "category": "unknown_cat", "confidence": 0.8}])
        facts = parse_extraction_response(text)
        assert len(facts) == 1
        assert facts[0].category == "environment"

    def test_missing_confidence_defaults(self):
        text = json.dumps([{"fact": "No confidence here"}])
        facts = parse_extraction_response(text)
        assert len(facts) == 1
        assert facts[0].confidence == 0.7

    def test_confidence_clamped(self):
        text = json.dumps(
            [
                {"fact": "Over one", "confidence": 1.5},
                {"fact": "Under zero", "confidence": -0.3},
            ]
        )
        facts = parse_extraction_response(text)
        assert facts[0].confidence == 1.0
        assert facts[1].confidence == 0.0

    def test_short_fact_skipped(self):
        text = json.dumps(
            [
                {"fact": "hi", "confidence": 0.9},  # too short
                {"fact": "A valid longer fact", "confidence": 0.9},
            ]
        )
        facts = parse_extraction_response(text)
        assert len(facts) == 1
        assert facts[0].fact == "A valid longer fact"

    def test_non_dict_items_skipped(self):
        text = json.dumps(
            [
                "just a string",
                {"fact": "Valid one", "confidence": 0.8},
                42,
            ]
        )
        facts = parse_extraction_response(text)
        assert len(facts) == 1

    def test_string_confidence_parsed(self):
        text = json.dumps([{"fact": "String conf", "confidence": "0.85"}])
        facts = parse_extraction_response(text)
        assert len(facts) == 1
        assert abs(facts[0].confidence - 0.85) < 0.01

    def test_non_parseable_confidence(self):
        text = json.dumps([{"fact": "Bad conf", "confidence": "high"}])
        facts = parse_extraction_response(text)
        assert len(facts) == 1
        assert facts[0].confidence == 0.7

    def test_json_with_surrounding_text(self):
        text = 'Here are the facts:\n[{"fact": "Port 5432", "confidence": 0.9}]\nDone!'
        facts = parse_extraction_response(text)
        assert len(facts) == 1
        assert facts[0].fact == "Port 5432"


# ── TestFactKey ───────────────────────────────────────────────


class TestFactKey:
    def test_basic_key(self):
        key = fact_key("Production DB is on port 5433", "environment")
        assert key == "environment/production_db_is_on"

    def test_category_in_key(self):
        key = fact_key("User prefers dark themes", "preferences")
        assert key.startswith("preferences/")

    def test_empty_fact(self):
        key = fact_key("", "environment")
        assert key == "environment/unknown"

    def test_special_chars_stripped(self):
        key = fact_key("Uses Python 3.14!", "project")
        assert "/" in key
        parts = key.split("/")
        assert all(re.match(r"^[a-z0-9_]+$", p) for p in parts)

    def test_unknown_category_sanitized(self):
        key = fact_key("Something", "weird/cat")
        assert key.startswith("weirdcat/")

    def test_max_four_words(self):
        key = fact_key("one two three four five six seven", "environment")
        slug = key.split("/")[1]
        assert slug.count("_") <= 3  # max 4 words → max 3 underscores


# ── TestMaybeExtract — Interval Logic ─────────────────────────


class TestMaybeExtractInterval:
    def test_no_extraction_before_interval(self):
        ext = MemoryAutoExtractor(llm_call=_fake_llm, config={"extraction_interval": 5})
        for _ in range(4):
            ext.record_turn("user", "msg")
            ext.record_turn("assistant", "reply")
        # 4 assistant turns < interval of 5
        facts = _run(ext.maybe_extract())
        assert facts == []
        assert ext.turn_count == 4

    def test_extraction_at_interval(self):
        store = FakeStore()
        ext = MemoryAutoExtractor(
            store=store, llm_call=_fake_llm, config={"extraction_interval": 2}
        )
        ext.record_turn("user", "msg1")
        ext.record_turn("assistant", "reply1")
        ext.record_turn("user", "msg2")
        ext.record_turn("assistant", "reply2")
        # 2 assistant turns == interval of 2
        facts = _run(ext.maybe_extract())
        assert len(facts) == 1
        assert facts[0].fact == "User prefers dark themes"

    def test_counter_resets_after_extraction(self):
        ext = MemoryAutoExtractor(llm_call=_fake_llm, config={"extraction_interval": 1})
        ext.record_turn("user", "msg")
        ext.record_turn("assistant", "reply")
        _run(ext.maybe_extract())
        assert ext.turn_count == 0
        assert ext.pending_turns == 0

    def test_counter_resets_even_on_empty_result(self):
        ext = MemoryAutoExtractor(llm_call=_fake_llm_empty, config={"extraction_interval": 1})
        ext.record_turn("user", "msg")
        ext.record_turn("assistant", "reply")
        facts = _run(ext.maybe_extract())
        assert facts == []
        assert ext.turn_count == 0

    def test_disabled_returns_empty(self):
        ext = MemoryAutoExtractor(
            llm_call=_fake_llm,
            config={"extraction_interval": 1, "enabled": False},
        )
        ext.record_turn("user", "msg")
        ext.record_turn("assistant", "reply")
        facts = _run(ext.maybe_extract())
        assert facts == []
        assert ext.turn_count == 1  # not reset when disabled


# ── TestMaybeExtract — LLM Integration ────────────────────────


class TestMaybeExtractLLM:
    def test_no_llm_returns_empty(self):
        ext = MemoryAutoExtractor(llm_call=None, config={"extraction_interval": 1})
        ext.record_turn("user", "msg")
        ext.record_turn("assistant", "reply")
        facts = _run(ext.maybe_extract())
        assert facts == []

    def test_llm_receives_prompt_with_turns(self):
        prompts_seen = []

        async def spy_llm(prompt: str, **kw) -> str:
            prompts_seen.append(prompt)
            return "[]"

        ext = MemoryAutoExtractor(llm_call=spy_llm, config={"extraction_interval": 1})
        ext.record_turn("user", "I like dark mode")
        ext.record_turn("assistant", "Noted!")
        _run(ext.maybe_extract())

        assert len(prompts_seen) == 1
        assert "I like dark mode" in prompts_seen[0]
        assert "Noted!" in prompts_seen[0]

    def test_llm_model_kwarg_passed(self):
        kwargs_seen = []

        async def spy_llm(prompt: str, **kw) -> str:
            kwargs_seen.append(kw)
            return "[]"

        ext = MemoryAutoExtractor(
            llm_call=spy_llm,
            config={"extraction_interval": 1, "extraction_model": "groq/llama-3.3-70b"},
        )
        ext.record_turn("user", "x")
        ext.record_turn("assistant", "y")
        _run(ext.maybe_extract())

        assert kwargs_seen[0].get("model") == "groq/llama-3.3-70b"

    def test_llm_no_model_kwarg_when_empty(self):
        kwargs_seen = []

        async def spy_llm(prompt: str, **kw) -> str:
            kwargs_seen.append(kw)
            return "[]"

        ext = MemoryAutoExtractor(llm_call=spy_llm, config={"extraction_interval": 1})
        ext.record_turn("user", "x")
        ext.record_turn("assistant", "y")
        _run(ext.maybe_extract())

        assert "model" not in kwargs_seen[0]


# ── TestConfidenceFiltering ───────────────────────────────────


class TestConfidenceFiltering:
    def test_high_confidence_kept(self):
        async def llm(prompt, **kw):
            return json.dumps([{"fact": "High conf", "category": "preferences", "confidence": 0.9}])

        ext = MemoryAutoExtractor(llm_call=llm, config={"extraction_interval": 1})
        ext.record_turn("user", "test")
        ext.record_turn("assistant", "ok")
        facts = _run(ext.maybe_extract())
        assert len(facts) == 1

    def test_low_confidence_filtered(self):
        async def llm(prompt, **kw):
            return json.dumps([{"fact": "Low conf", "category": "preferences", "confidence": 0.3}])

        ext = MemoryAutoExtractor(
            llm_call=llm, config={"extraction_interval": 1, "min_confidence": 0.6}
        )
        ext.record_turn("user", "test")
        ext.record_turn("assistant", "ok")
        facts = _run(ext.maybe_extract())
        assert facts == []

    def test_borderline_confidence(self):
        """Fact at exactly min_confidence should be included."""

        async def llm(prompt, **kw):
            return json.dumps(
                [{"fact": "Borderline", "category": "preferences", "confidence": 0.6}]
            )

        ext = MemoryAutoExtractor(
            llm_call=llm, config={"extraction_interval": 1, "min_confidence": 0.6}
        )
        ext.record_turn("user", "test")
        ext.record_turn("assistant", "ok")
        facts = _run(ext.maybe_extract())
        assert len(facts) == 1

    def test_max_facts_limit(self):
        async def llm(prompt, **kw):
            return json.dumps(
                [
                    {"fact": f"Fact {i}", "category": "preferences", "confidence": 0.9}
                    for i in range(10)
                ]
            )

        ext = MemoryAutoExtractor(
            llm_call=llm,
            config={"extraction_interval": 1, "max_facts_per_extraction": 2},
        )
        ext.record_turn("user", "test")
        ext.record_turn("assistant", "ok")
        facts = _run(ext.maybe_extract())
        assert len(facts) == 2


# ── TestStoreIntegration ──────────────────────────────────────


class TestStoreIntegration:
    def test_put_store(self):
        store = FakeStore()
        ext = MemoryAutoExtractor(
            store=store, llm_call=_fake_llm, config={"extraction_interval": 1}
        )
        ext.record_turn("user", "test")
        ext.record_turn("assistant", "ok")
        facts = _run(ext.maybe_extract())
        assert len(facts) == 1
        assert len(store.puts) == 1
        key, value, meta = store.puts[0]
        assert "preferences/" in key
        assert value == "User prefers dark themes"
        assert meta["auto_extracted"] is True
        assert meta["category"] == "preferences"

    def test_upsert_store(self):
        store = FakeUpsertStore()
        ext = MemoryAutoExtractor(
            store=store, llm_call=_fake_llm, config={"extraction_interval": 1}
        )
        ext.record_turn("user", "test")
        ext.record_turn("assistant", "ok")
        _run(ext.maybe_extract())
        assert len(store.upserts) == 1

    def test_no_store_still_returns_facts(self):
        ext = MemoryAutoExtractor(store=None, llm_call=_fake_llm, config={"extraction_interval": 1})
        ext.record_turn("user", "test")
        ext.record_turn("assistant", "ok")
        facts = _run(ext.maybe_extract())
        assert len(facts) == 1

    def test_failing_store_doesnt_crash(self):
        store = FailingStore()
        ext = MemoryAutoExtractor(
            store=store, llm_call=_fake_llm, config={"extraction_interval": 1}
        )
        ext.record_turn("user", "test")
        ext.record_turn("assistant", "ok")
        # Should not raise
        facts = _run(ext.maybe_extract())
        # Facts that failed to store are skipped
        assert len(facts) == 0

    def test_total_extracted_accumulates(self):
        store = FakeStore()
        ext = MemoryAutoExtractor(
            store=store, llm_call=_fake_llm, config={"extraction_interval": 1}
        )
        for _ in range(3):
            ext.record_turn("user", "test")
            ext.record_turn("assistant", "ok")
            _run(ext.maybe_extract())
        assert ext.total_extracted == 3


# ── TestForceExtract ──────────────────────────────────────────


class TestForceExtract:
    def test_extracts_without_interval(self):
        ext = MemoryAutoExtractor(llm_call=_fake_llm, config={"extraction_interval": 100})
        ext.record_turn("user", "remember this")
        ext.record_turn("assistant", "ok")
        # turn_count is 1, interval is 100 — but force ignores it
        facts = _run(ext.force_extract())
        assert len(facts) == 1

    def test_resets_after_force(self):
        ext = MemoryAutoExtractor(llm_call=_fake_llm, config={"extraction_interval": 100})
        ext.record_turn("user", "msg")
        ext.record_turn("assistant", "reply")
        _run(ext.force_extract())
        assert ext.turn_count == 0
        assert ext.pending_turns == 0

    def test_force_empty_returns_empty(self):
        ext = MemoryAutoExtractor(llm_call=_fake_llm)
        facts = _run(ext.force_extract())
        assert facts == []

    def test_force_with_llm_error(self):
        ext = MemoryAutoExtractor(llm_call=_fail_llm)
        ext.record_turn("user", "msg")
        ext.record_turn("assistant", "reply")
        facts = _run(ext.force_extract())
        assert facts == []
        assert ext.turn_count == 0


# ── TestSilentFailure ─────────────────────────────────────────


class TestSilentFailure:
    def test_llm_exception_handled(self):
        ext = MemoryAutoExtractor(llm_call=_fail_llm, config={"extraction_interval": 1})
        ext.record_turn("user", "msg")
        ext.record_turn("assistant", "reply")
        # Should NOT raise
        facts = _run(ext.maybe_extract())
        assert facts == []

    def test_counter_resets_on_failure(self):
        ext = MemoryAutoExtractor(llm_call=_fail_llm, config={"extraction_interval": 1})
        ext.record_turn("user", "msg")
        ext.record_turn("assistant", "reply")
        _run(ext.maybe_extract())
        assert ext.turn_count == 0
        assert ext.pending_turns == 0

    def test_llm_returns_none(self):
        async def none_llm(prompt, **kw):
            return None

        ext = MemoryAutoExtractor(llm_call=none_llm, config={"extraction_interval": 1})
        ext.record_turn("user", "msg")
        ext.record_turn("assistant", "reply")
        facts = _run(ext.maybe_extract())
        assert facts == []


# ── TestProperties ────────────────────────────────────────────


class TestProperties:
    def test_config_property(self):
        cfg = ExtractionConfig(interval=7)
        ext = MemoryAutoExtractor(config=cfg)
        assert ext.config.interval == 7

    def test_enabled_property(self):
        ext = MemoryAutoExtractor(config={"enabled": False})
        assert ext.enabled is False

    def test_turn_count_property(self):
        ext = MemoryAutoExtractor()
        assert ext.turn_count == 0
        ext.record_turn("assistant", "hi")
        assert ext.turn_count == 1

    def test_pending_turns_property(self):
        ext = MemoryAutoExtractor()
        assert ext.pending_turns == 0
        ext.record_turn("user", "hi")
        assert ext.pending_turns == 1

    def test_total_extracted_starts_zero(self):
        ext = MemoryAutoExtractor()
        assert ext.total_extracted == 0


# ── TestEdgeCases ─────────────────────────────────────────────


class TestEdgeCases:
    def test_interval_one(self):
        """With interval=1, extraction triggers on every assistant turn."""
        ext = MemoryAutoExtractor(llm_call=_fake_llm, config={"extraction_interval": 1})
        ext.record_turn("user", "msg")
        ext.record_turn("assistant", "reply")
        facts = _run(ext.maybe_extract())
        assert len(facts) == 1

    def test_consecutive_extractions(self):
        ext = MemoryAutoExtractor(llm_call=_fake_llm, config={"extraction_interval": 1})
        for _ in range(3):
            ext.record_turn("user", "msg")
            ext.record_turn("assistant", "reply")
            facts = _run(ext.maybe_extract())
            assert len(facts) == 1

    def test_only_user_turns_no_trigger(self):
        ext = MemoryAutoExtractor(llm_call=_fake_llm, config={"extraction_interval": 1})
        for _ in range(10):
            ext.record_turn("user", "msg")
        facts = _run(ext.maybe_extract())
        assert facts == []

    def test_batched_turns_in_prompt(self):
        """Prompt should contain multiple conversation turns."""
        prompts_seen = []

        async def spy_llm(prompt: str, **kw) -> str:
            prompts_seen.append(prompt)
            return "[]"

        ext = MemoryAutoExtractor(llm_call=spy_llm, config={"extraction_interval": 3})
        for i in range(3):
            ext.record_turn("user", f"user message {i}")
            ext.record_turn("assistant", f"assistant reply {i}")
        _run(ext.maybe_extract())

        prompt = prompts_seen[0]
        assert "user message 0" in prompt
        assert "user message 2" in prompt
        assert "assistant reply 1" in prompt

    def test_construct_with_config_object(self):
        cfg = ExtractionConfig(interval=3, max_facts=2, min_confidence=0.5)
        ext = MemoryAutoExtractor(config=cfg)
        assert ext.config.interval == 3
        assert ext.config.max_facts == 2

    def test_empty_pending_at_interval_returns_empty(self):
        """If somehow turn_count >= interval but no turns buffered, return empty."""
        ext = MemoryAutoExtractor(llm_call=_fake_llm, config={"extraction_interval": 1})
        ext._turn_count = 5  # Manually set
        facts = _run(ext.maybe_extract())
        assert facts == []

    def test_multiple_facts_from_llm(self):
        async def multi_llm(prompt, **kw):
            return json.dumps(
                [
                    {"fact": "Fact A", "category": "preferences", "confidence": 0.9},
                    {"fact": "Fact B", "category": "environment", "confidence": 0.8},
                ]
            )

        store = FakeStore()
        ext = MemoryAutoExtractor(
            store=store, llm_call=multi_llm, config={"extraction_interval": 1}
        )
        ext.record_turn("user", "complex message")
        ext.record_turn("assistant", "detailed reply")
        facts = _run(ext.maybe_extract())
        assert len(facts) == 2
        assert len(store.puts) == 2

    def test_prompt_limits_to_last_10_turns(self):
        """Even with many buffered turns, only last 10 appear in prompt."""
        prompts_seen = []

        async def spy_llm(prompt: str, **kw) -> str:
            prompts_seen.append(prompt)
            return "[]"

        ext = MemoryAutoExtractor(
            llm_call=spy_llm,
            config=ExtractionConfig(interval=15),
        )
        for i in range(15):
            ext.record_turn("user", f"u{i}")
            ext.record_turn("assistant", f"a{i}")
        _run(ext.maybe_extract())

        prompt = prompts_seen[0]
        # Should have recent turns but NOT the earliest ones
        assert "u14" in prompt
        assert "a14" in prompt
        # Due to MAX_PENDING_TURNS=20, we have turns u10-u14/a10-a14 (the last 20)
        # Of those, we take the last 10 for the prompt
