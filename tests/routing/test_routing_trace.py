"""Tests for navig.routing.trace — RouteTrace, log_trace, recent_traces."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import navig.routing.trace as trace_mod
from navig.routing.trace import RouteTrace, log_trace, recent_traces


class TestRouteTrace:
    def test_defaults(self):
        r = RouteTrace()
        assert r.trace_id == ""
        assert r.provider == ""
        assert r.input_tokens == 0
        assert r.reasons == []
        assert r.tools_used == []

    def test_to_dict_returns_dict(self):
        r = RouteTrace(trace_id="abc", provider="openai", model="gpt-4o")
        d = r.to_dict()
        assert isinstance(d, dict)
        assert d["trace_id"] == "abc"
        assert d["provider"] == "openai"

    def test_to_dict_includes_all_fields(self):
        r = RouteTrace()
        d = r.to_dict()
        for key in ("trace_id", "timestamp", "mode", "confidence", "provider",
                    "model", "input_tokens", "output_tokens", "latency_ms",
                    "audit_result", "entrypoint"):
            assert key in d

    def test_defaults_independent_per_instance(self):
        r1 = RouteTrace()
        r2 = RouteTrace()
        r1.reasons.append("test")
        assert r2.reasons == []


class TestLogTrace:
    def test_writes_jsonl_line(self, tmp_path):
        log_path = tmp_path / "traces.jsonl"
        with patch.object(trace_mod, "TRACE_LOG_PATH", log_path):
            r = RouteTrace(trace_id="t1", provider="anthropic")
            log_trace(r)
        content = log_path.read_text()
        data = json.loads(content.strip())
        assert data["trace_id"] == "t1"
        assert data["provider"] == "anthropic"

    def test_appends_multiple_traces(self, tmp_path):
        log_path = tmp_path / "traces.jsonl"
        with patch.object(trace_mod, "TRACE_LOG_PATH", log_path):
            log_trace(RouteTrace(trace_id="a"))
            log_trace(RouteTrace(trace_id="b"))
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_never_raises(self, tmp_path):
        # Even if directory creation fails, log_trace must not raise
        bad_path = tmp_path / "no" / "such" / "dir" / "traces.jsonl"
        with patch.object(trace_mod, "TRACE_LOG_PATH", bad_path):
            with patch("pathlib.Path.mkdir", side_effect=OSError("no perm")):
                log_trace(RouteTrace())  # Should not raise


class TestRecentTraces:
    def test_returns_empty_when_no_file(self, tmp_path):
        missing = tmp_path / "nonexistent.jsonl"
        with patch.object(trace_mod, "TRACE_LOG_PATH", missing):
            result = recent_traces()
        assert result == []

    def test_returns_traces_from_file(self, tmp_path):
        log_path = tmp_path / "traces.jsonl"
        records = [json.dumps({"trace_id": str(i)}) for i in range(5)]
        log_path.write_text("\n".join(records) + "\n")
        with patch.object(trace_mod, "TRACE_LOG_PATH", log_path):
            result = recent_traces(limit=10)
        assert len(result) == 5
        assert result[0]["trace_id"] == "0"

    def test_limit_respected(self, tmp_path):
        log_path = tmp_path / "traces.jsonl"
        records = [json.dumps({"i": n}) for n in range(10)]
        log_path.write_text("\n".join(records) + "\n")
        with patch.object(trace_mod, "TRACE_LOG_PATH", log_path):
            result = recent_traces(limit=3)
        assert len(result) == 3

    def test_returns_empty_on_corrupt_file(self, tmp_path):
        log_path = tmp_path / "traces.jsonl"
        log_path.write_text("{invalid json content\n")
        with patch.object(trace_mod, "TRACE_LOG_PATH", log_path):
            result = recent_traces()
        assert result == []
