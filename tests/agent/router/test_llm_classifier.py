"""
Tests for navig.agent.router.llm_classifier
"""

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import navig.agent.router.llm_classifier as llm_cls
from navig.agent.router.llm_classifier import (
    CACHE_MAX_ENTRIES,
    CACHE_TTL_SECONDS,
    DEFAULT_CONFIDENCE_THRESHOLD,
    _cache,
    _evict_oldest,
    _get_cache_key,
    _parse_tier,
    _read_cache,
    _write_cache,
    classify_by_llm,
    should_use_llm_classifier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_cache():
    """Wipe the module-level cache between tests."""
    _cache.clear()


# ---------------------------------------------------------------------------
# _get_cache_key
# ---------------------------------------------------------------------------


class TestGetCacheKey:
    def setup_method(self):
        _clear_cache()

    def test_returns_hex_string(self):
        key = _get_cache_key("hello")
        assert isinstance(key, str)
        assert len(key) == 64  # SHA-256 hex = 64 chars

    def test_deterministic(self):
        assert _get_cache_key("abc") == _get_cache_key("abc")

    def test_different_prompts_differ(self):
        assert _get_cache_key("foo") != _get_cache_key("bar")

    def test_truncates_at_500(self):
        # prompts that differ only after char 500 should have the same key
        base = "x" * 500
        assert _get_cache_key(base + "A") == _get_cache_key(base + "B")

    def test_empty_string(self):
        key = _get_cache_key("")
        assert len(key) == 64


# ---------------------------------------------------------------------------
# _evict_oldest
# ---------------------------------------------------------------------------


class TestEvictOldest:
    def setup_method(self):
        _clear_cache()

    def test_noop_on_empty(self):
        _evict_oldest()  # should not raise

    def test_removes_first_inserted(self):
        _cache["k1"] = ("SIMPLE", datetime.datetime.utcnow())
        _cache["k2"] = ("MEDIUM", datetime.datetime.utcnow())
        _evict_oldest()
        assert "k1" not in _cache
        assert "k2" in _cache


# ---------------------------------------------------------------------------
# _read_cache / _write_cache
# ---------------------------------------------------------------------------


class TestReadWriteCache:
    def setup_method(self):
        _clear_cache()

    def test_miss_returns_none(self):
        assert _read_cache("nonexistent") is None

    def test_hit_returns_tier(self):
        _write_cache("mykey", "COMPLEX")
        assert _read_cache("mykey") == "COMPLEX"

    def test_expired_returns_none(self):
        old_time = datetime.datetime.utcnow() - datetime.timedelta(seconds=CACHE_TTL_SECONDS + 1)
        _cache["stale"] = ("SIMPLE", old_time)
        assert _read_cache("stale") is None

    def test_within_ttl_returns_tier(self):
        fresh_time = datetime.datetime.utcnow() - datetime.timedelta(seconds=CACHE_TTL_SECONDS - 60)
        _cache["fresh"] = ("AGENTIC", fresh_time)
        assert _read_cache("fresh") == "AGENTIC"

    def test_write_respects_max_entries(self):
        for i in range(CACHE_MAX_ENTRIES + 5):
            _write_cache(f"key_{i}", "SIMPLE")
        assert len(_cache) <= CACHE_MAX_ENTRIES


# ---------------------------------------------------------------------------
# _parse_tier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("SIMPLE", "SIMPLE"),
        ("simple", "SIMPLE"),
        ("  MEDIUM  ", "MEDIUM"),
        ("COMPLEX", "COMPLEX"),
        ("REASONING", "REASONING"),
        ("AGENTIC", "AGENTIC"),
        # Word boundary – tier embedded in sentence
        ("This is a MEDIUM tier request", "MEDIUM"),
        # Priority: REASONING comes before MEDIUM
        ("This needs REASONING but also MEDIUM", "REASONING"),
        # Unknown
        ("totally unknown", None),
        ("", None),
    ],
)
def test_parse_tier(raw, expected):
    assert _parse_tier(raw) == expected


# ---------------------------------------------------------------------------
# should_use_llm_classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "confidence, threshold, expected",
    [
        (0.5, DEFAULT_CONFIDENCE_THRESHOLD, True),   # below default → use LLM
        (0.7, DEFAULT_CONFIDENCE_THRESHOLD, False),  # at default → do NOT use LLM
        (0.9, DEFAULT_CONFIDENCE_THRESHOLD, False),  # above default → do NOT use LLM
        (0.0, 0.5, True),
        (0.5, 0.5, False),
        (1.0, 0.5, False),
    ],
)
def test_should_use_llm_classifier(confidence, threshold, expected):
    assert should_use_llm_classifier(confidence, threshold) is expected


# ---------------------------------------------------------------------------
# classify_by_llm — fully mocked async tests
# ---------------------------------------------------------------------------


class TestClassifyByLlm:
    def setup_method(self):
        _clear_cache()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_returns_cached_tier(self):
        key = _get_cache_key("what is 2+2?")
        _cache[key] = ("SIMPLE", datetime.datetime.utcnow())
        result = self._run(classify_by_llm("what is 2+2?"))
        assert result == "SIMPLE"

    def test_calls_llm_on_cache_miss(self):
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value="MEDIUM")

        with patch("navig.agent.router.llm_classifier.get_ai_client", return_value=mock_client):
            result = self._run(classify_by_llm("complex analysis task"))

        assert result == "MEDIUM"
        mock_client.complete.assert_awaited_once()

    def test_writes_to_cache_after_llm_call(self):
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value="COMPLEX")

        with patch("navig.agent.router.llm_classifier.get_ai_client", return_value=mock_client):
            self._run(classify_by_llm("multi-step analysis"))

        key = _get_cache_key("multi-step analysis")
        assert key in _cache
        assert _cache[key][0] == "COMPLEX"

    def test_llm_exception_defaults_to_medium(self):
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(side_effect=RuntimeError("network failure"))

        with patch("navig.agent.router.llm_classifier.get_ai_client", return_value=mock_client):
            result = self._run(classify_by_llm("some prompt"))

        assert result == "MEDIUM"

    def test_unparseable_response_defaults_to_medium(self):
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value="totally garbage response xyz")

        with patch("navig.agent.router.llm_classifier.get_ai_client", return_value=mock_client):
            result = self._run(classify_by_llm("some prompt"))

        assert result == "MEDIUM"

    def test_agentic_returned_correctly(self):
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value="AGENTIC")

        with patch("navig.agent.router.llm_classifier.get_ai_client", return_value=mock_client):
            result = self._run(classify_by_llm("execute a multi-step workflow"))

        assert result == "AGENTIC"

    def test_second_call_uses_cache(self):
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value="REASONING")
        prompt = "prove the halting problem"

        with patch("navig.agent.router.llm_classifier.get_ai_client", return_value=mock_client):
            r1 = self._run(classify_by_llm(prompt))
            r2 = self._run(classify_by_llm(prompt))

        assert r1 == r2 == "REASONING"
        # LLM called only once
        assert mock_client.complete.call_count == 1
