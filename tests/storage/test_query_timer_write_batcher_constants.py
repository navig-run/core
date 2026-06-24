"""
Batch 13: Tests for
- navig.storage.query_timer.QueryStats (percentile math, record, to_dict)
- navig.storage.write_batcher._PendingWrite (dataclass)
- Constants: _llm_defaults, vault/_constants, google_oauth_constants, providers/_local_defaults
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# navig.storage.query_timer — QueryStats pure logic
# ---------------------------------------------------------------------------
from navig.storage.query_timer import QueryStats


class TestQueryStatsRecord:
    def test_initial_count_zero(self):
        qs = QueryStats(label="test")
        assert qs.count == 0
        assert qs.total_ms == 0.0

    def test_record_single(self):
        qs = QueryStats(label="q1")
        qs.record(5.0)
        assert qs.count == 1
        assert qs.total_ms == 5.0
        assert qs.min_ms == 5.0
        assert qs.max_ms == 5.0

    def test_record_multiple_updates_min_max(self):
        qs = QueryStats(label="q2")
        qs.record(10.0)
        qs.record(2.0)
        qs.record(7.0)
        assert qs.count == 3
        assert qs.min_ms == 2.0
        assert qs.max_ms == 10.0
        assert qs.total_ms == pytest.approx(19.0)

    def test_avg_ms_correct(self):
        qs = QueryStats(label="avg")
        qs.record(4.0)
        qs.record(6.0)
        assert qs.avg_ms == pytest.approx(5.0)

    def test_avg_ms_zero_when_empty(self):
        qs = QueryStats(label="empty")
        assert qs.avg_ms == 0.0


class TestQueryStatsPercentile:
    def test_percentile_empty_returns_zero(self):
        qs = QueryStats(label="empty")
        assert qs.percentile(50) == 0.0

    def test_percentile_single_sample(self):
        qs = QueryStats(label="single")
        qs.record(42.0)
        assert qs.percentile(50) == 42.0
        assert qs.percentile(99) == 42.0

    def test_p50_median(self):
        qs = QueryStats(label="median")
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            qs.record(v)
        p50 = qs.percentile(50)
        # Median of sorted [1,2,3,4,5] at idx=2 → 3.0
        assert p50 == pytest.approx(3.0)

    def test_p99_large_sample(self):
        qs = QueryStats(label="p99")
        for v in range(1, 101):
            qs.record(float(v))
        p99 = qs.percentile(99)
        # Should be near top of range
        assert p99 >= 90.0

    def test_percentile_zero_returns_min(self):
        qs = QueryStats(label="zerop")
        for v in [10.0, 20.0, 30.0]:
            qs.record(v)
        p0 = qs.percentile(0)
        assert p0 == 10.0


class TestQueryStatsToDict:
    def test_to_dict_keys(self):
        qs = QueryStats(label="myq")
        qs.record(1.0)
        d = qs.to_dict()
        assert "label" in d
        assert "count" in d
        assert "avg_ms" in d
        assert "min_ms" in d
        assert "max_ms" in d
        assert "p50_ms" in d
        assert "p95_ms" in d
        assert "p99_ms" in d

    def test_to_dict_label(self):
        qs = QueryStats(label="hello")
        qs.record(5.0)
        assert qs.to_dict()["label"] == "hello"

    def test_to_dict_count(self):
        qs = QueryStats(label="cnt")
        for _ in range(7):
            qs.record(1.0)
        assert qs.to_dict()["count"] == 7

    def test_to_dict_min_zero_when_empty(self):
        qs = QueryStats(label="empty")
        d = qs.to_dict()
        # min_ms is float("inf") when empty, to_dict converts it to 0.0
        assert d["min_ms"] == 0.0

    def test_to_dict_rounded(self):
        qs = QueryStats(label="round")
        qs.record(1.23456789)
        d = qs.to_dict()
        # Values should be rounded to 3 decimal places
        assert d["avg_ms"] == round(1.23456789, 3)


# ---------------------------------------------------------------------------
# navig.storage.write_batcher._PendingWrite (dataclass)
# ---------------------------------------------------------------------------
from navig.storage.write_batcher import _PendingWrite


class TestPendingWrite:
    def test_basic_creation(self):
        pw = _PendingWrite(sql="INSERT INTO t VALUES (?)", params=(1,))
        assert pw.sql == "INSERT INTO t VALUES (?)"
        assert pw.params == (1,)

    def test_default_is_many_false(self):
        pw = _PendingWrite(sql="SELECT 1", params=())
        assert pw.is_many is False

    def test_is_many_true(self):
        pw = _PendingWrite(sql="INSERT INTO t VALUES (?)", params=(), is_many=True, seq_params=[(1,), (2,)])
        assert pw.is_many is True
        assert pw.seq_params == [(1,), (2,)]

    def test_seq_params_default_none(self):
        pw = _PendingWrite(sql="q", params=())
        assert pw.seq_params is None


# ---------------------------------------------------------------------------
# Constants: single-source-of-truth leaf modules
# ---------------------------------------------------------------------------
from navig._llm_defaults import _DEFAULT_MAX_TOKENS, _DEFAULT_TEMPERATURE


class TestLLMDefaults:
    def test_temperature_range(self):
        assert 0.0 <= _DEFAULT_TEMPERATURE <= 2.0

    def test_temperature_value(self):
        assert _DEFAULT_TEMPERATURE == 0.7

    def test_max_tokens_positive(self):
        assert _DEFAULT_MAX_TOKENS > 0

    def test_max_tokens_value(self):
        assert _DEFAULT_MAX_TOKENS == 4096


from navig.vault._constants import _DEFAULT_TTL


class TestVaultConstants:
    def test_ttl_positive(self):
        assert _DEFAULT_TTL > 0

    def test_ttl_is_30_minutes(self):
        assert _DEFAULT_TTL == 1800  # 30 * 60


from navig.connectors.google_oauth_constants import (
    GOOGLE_AUTH_URL,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
)


class TestGoogleOAuthConstants:
    def test_auth_url_is_https(self):
        assert GOOGLE_AUTH_URL.startswith("https://")

    def test_token_url_is_https(self):
        assert GOOGLE_TOKEN_URL.startswith("https://")

    def test_userinfo_url_is_https(self):
        assert GOOGLE_USERINFO_URL.startswith("https://")

    def test_auth_url_contains_google(self):
        assert "google.com" in GOOGLE_AUTH_URL

    def test_all_urls_distinct(self):
        assert len({GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL, GOOGLE_USERINFO_URL}) == 3


from navig.providers._local_defaults import (
    _LLAMACPP_BASE_URL,
    _LLAMACPP_USER_BASE_URL,
    _OLLAMA_BASE_URL,
    _OLLAMA_USER_BASE_URL,
)


class TestLocalProviderDefaults:
    def test_ollama_base_url_loopback(self):
        assert "127.0.0.1" in _OLLAMA_BASE_URL

    def test_ollama_user_base_url_localhost(self):
        assert "localhost" in _OLLAMA_USER_BASE_URL

    def test_llamacpp_base_url_loopback(self):
        assert "127.0.0.1" in _LLAMACPP_BASE_URL

    def test_llamacpp_user_base_url_localhost(self):
        assert "localhost" in _LLAMACPP_USER_BASE_URL

    def test_ollama_port(self):
        assert "11434" in _OLLAMA_BASE_URL

    def test_llamacpp_port(self):
        assert "8080" in _LLAMACPP_BASE_URL
