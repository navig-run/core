"""Tests for routing/trace.py and core/yaml_io.py."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# routing/trace.py
# ──────────────────────────────────────────────────────────────────────────────
from navig.routing.trace import RouteTrace, log_trace, recent_traces


class TestRouteTrace:
    def test_defaults(self):
        rt = RouteTrace()
        assert rt.trace_id == ""
        assert rt.mode == ""
        assert rt.confidence == 0.0
        assert rt.reasons == []
        assert rt.fallbacks_tried == []

    def test_to_dict_returns_dict(self):
        rt = RouteTrace(trace_id="abc", mode="coding", confidence=0.9)
        d = rt.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_contains_fields(self):
        rt = RouteTrace(trace_id="t1", mode="research", confidence=0.85, provider="openai")
        d = rt.to_dict()
        assert d["trace_id"] == "t1"
        assert d["mode"] == "research"
        assert d["confidence"] == 0.85
        assert d["provider"] == "openai"

    def test_to_dict_lists_default_empty(self):
        d = RouteTrace().to_dict()
        assert d["reasons"] == []
        assert d["tools_used"] == []

    def test_custom_reasons(self):
        rt = RouteTrace(reasons=["code_patterns(3)"])
        assert rt.reasons == ["code_patterns(3)"]

    def test_all_fields_present_in_dict(self):
        d = RouteTrace().to_dict()
        for key in (
            "trace_id", "timestamp", "mode", "confidence", "reasons",
            "provider", "model", "input_tokens", "output_tokens",
            "latency_ms", "audit_result", "tools_used", "entrypoint",
        ):
            assert key in d, f"{key!r} missing from to_dict()"


class TestLogTrace:
    def test_log_creates_file(self, tmp_path, monkeypatch):
        log_file = tmp_path / "logs" / "router_traces.jsonl"
        monkeypatch.setattr("navig.routing.trace.TRACE_LOG_PATH", log_file)
        rt = RouteTrace(trace_id="t1", mode="coding")
        log_trace(rt)
        assert log_file.exists()

    def test_log_writes_valid_json(self, tmp_path, monkeypatch):
        log_file = tmp_path / "logs" / "router_traces.jsonl"
        monkeypatch.setattr("navig.routing.trace.TRACE_LOG_PATH", log_file)
        rt = RouteTrace(trace_id="abc", mode="summarize", confidence=0.8)
        log_trace(rt)
        line = log_file.read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert parsed["trace_id"] == "abc"

    def test_log_appends_multiple(self, tmp_path, monkeypatch):
        log_file = tmp_path / "logs" / "router_traces.jsonl"
        monkeypatch.setattr("navig.routing.trace.TRACE_LOG_PATH", log_file)
        log_trace(RouteTrace(trace_id="1"))
        log_trace(RouteTrace(trace_id="2"))
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    def test_log_never_raises_on_io_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("navig.routing.trace.TRACE_LOG_PATH", Path("/::invalid::"))
        # Should not raise
        log_trace(RouteTrace(trace_id="x"))


class TestRecentTraces:
    def test_no_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "navig.routing.trace.TRACE_LOG_PATH", tmp_path / "absent.jsonl"
        )
        assert recent_traces() == []

    def test_reads_all_traces(self, tmp_path, monkeypatch):
        log_file = tmp_path / "traces.jsonl"
        for i in range(5):
            log_file.open("a").write(json.dumps({"trace_id": str(i)}) + "\n")
        monkeypatch.setattr("navig.routing.trace.TRACE_LOG_PATH", log_file)
        result = recent_traces(limit=10)
        assert len(result) == 5

    def test_respects_limit(self, tmp_path, monkeypatch):
        log_file = tmp_path / "traces.jsonl"
        for i in range(20):
            log_file.open("a").write(json.dumps({"trace_id": str(i)}) + "\n")
        monkeypatch.setattr("navig.routing.trace.TRACE_LOG_PATH", log_file)
        result = recent_traces(limit=5)
        assert len(result) == 5

    def test_returns_dicts(self, tmp_path, monkeypatch):
        log_file = tmp_path / "traces.jsonl"
        log_file.write_text(json.dumps({"mode": "coding"}) + "\n", encoding="utf-8")
        monkeypatch.setattr("navig.routing.trace.TRACE_LOG_PATH", log_file)
        result = recent_traces()
        assert isinstance(result[0], dict)


# ──────────────────────────────────────────────────────────────────────────────
# core/yaml_io.py
# ──────────────────────────────────────────────────────────────────────────────
from navig.core.yaml_io import atomic_write_text, atomic_write_yaml, safe_load_yaml


class TestSafeLoadYaml:
    def test_returns_none_for_missing_file(self, tmp_path):
        assert safe_load_yaml(tmp_path / "missing.yaml") is None

    def test_parses_valid_yaml(self, tmp_path):
        f = tmp_path / "valid.yaml"
        f.write_text("key: value\nnumber: 42\n", encoding="utf-8")
        result = safe_load_yaml(f)
        assert result == {"key": "value", "number": 42}

    def test_returns_none_for_invalid_yaml(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("key: [\n", encoding="utf-8")
        assert safe_load_yaml(f) is None

    def test_returns_none_for_empty_file(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("", encoding="utf-8")
        assert safe_load_yaml(f) is None

    def test_accepts_string_path(self, tmp_path):
        f = tmp_path / "str.yaml"
        f.write_text("a: 1\n", encoding="utf-8")
        result = safe_load_yaml(str(f))
        assert result == {"a": 1}


class TestAtomicWriteYaml:
    def test_creates_file(self, tmp_path):
        f = tmp_path / "out.yaml"
        atomic_write_yaml({"x": 1}, f)
        assert f.exists()

    def test_roundtrip(self, tmp_path):
        data = {"hello": "world", "nums": [1, 2, 3]}
        f = tmp_path / "rt.yaml"
        atomic_write_yaml(data, f)
        loaded = safe_load_yaml(f)
        assert loaded == data

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "sub" / "dir" / "out.yaml"
        atomic_write_yaml({"k": "v"}, f)
        assert f.exists()

    def test_overwrites_existing(self, tmp_path):
        f = tmp_path / "overwrite.yaml"
        atomic_write_yaml({"v": 1}, f)
        atomic_write_yaml({"v": 2}, f)
        loaded = safe_load_yaml(f)
        assert loaded["v"] == 2


class TestAtomicWriteText:
    def test_creates_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        atomic_write_text(f, "hello world")
        assert f.exists()

    def test_writes_content(self, tmp_path):
        f = tmp_path / "content.txt"
        atomic_write_text(f, "test content abc")
        assert f.read_text(encoding="utf-8") == "test content abc"

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "nested" / "path" / "file.txt"
        atomic_write_text(f, "nested")
        assert f.exists()

    def test_overwrites_existing(self, tmp_path):
        f = tmp_path / "ow.txt"
        atomic_write_text(f, "first")
        atomic_write_text(f, "second")
        assert f.read_text(encoding="utf-8") == "second"

    def test_unicode_content(self, tmp_path):
        f = tmp_path / "uni.txt"
        atomic_write_text(f, "héllo wörld 🚀")
        assert "héllo" in f.read_text(encoding="utf-8")
