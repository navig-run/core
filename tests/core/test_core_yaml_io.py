"""
Tests for navig/core/yaml_io.py — atomic I/O and YAML helpers.
Batch 92.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from navig.core.yaml_io import (
    ATOMIC_REPLACE_BACKOFF_BASE_SECONDS,
    ATOMIC_REPLACE_RETRIES,
    YamlDocument,
    atomic_write_text,
    atomic_write_yaml,
    log_shadow_anomaly,
    safe_load_yaml,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_retries_is_int(self):
        assert isinstance(ATOMIC_REPLACE_RETRIES, int)

    def test_retries_positive(self):
        assert ATOMIC_REPLACE_RETRIES > 0

    def test_backoff_is_float_or_int(self):
        assert isinstance(ATOMIC_REPLACE_BACKOFF_BASE_SECONDS, (int, float))

    def test_backoff_positive(self):
        assert ATOMIC_REPLACE_BACKOFF_BASE_SECONDS > 0


# ---------------------------------------------------------------------------
# safe_load_yaml
# ---------------------------------------------------------------------------

class TestSafeLoadYaml:
    def test_returns_none_when_file_missing(self, tmp_path):
        result = safe_load_yaml(tmp_path / "nonexistent.yaml")
        assert result is None

    def test_returns_none_on_empty_file(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("", encoding="utf-8")
        result = safe_load_yaml(p)
        assert result is None

    def test_returns_data_for_valid_yaml(self, tmp_path):
        p = tmp_path / "data.yaml"
        p.write_text("key: value\n", encoding="utf-8")
        result = safe_load_yaml(p)
        assert result == {"key": "value"}

    def test_returns_none_for_invalid_yaml(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("key: [\n  broken", encoding="utf-8")
        result = safe_load_yaml(p)
        assert result is None

    def test_accepts_string_path(self, tmp_path):
        p = tmp_path / "str.yaml"
        p.write_text("x: 1\n", encoding="utf-8")
        result = safe_load_yaml(str(p))
        assert result == {"x": 1}

    def test_returns_list_yaml(self, tmp_path):
        p = tmp_path / "list.yaml"
        p.write_text("- a\n- b\n- c\n", encoding="utf-8")
        result = safe_load_yaml(p)
        assert result == ["a", "b", "c"]

    def test_returns_nested_dict(self, tmp_path):
        p = tmp_path / "nested.yaml"
        p.write_text("outer:\n  inner: 42\n", encoding="utf-8")
        result = safe_load_yaml(p)
        assert result["outer"]["inner"] == 42


# ---------------------------------------------------------------------------
# atomic_write_yaml
# ---------------------------------------------------------------------------

class TestAtomicWriteYaml:
    def test_creates_file(self, tmp_path):
        dest = tmp_path / "output.yaml"
        atomic_write_yaml({"a": 1}, dest)
        assert dest.exists()

    def test_content_is_valid_yaml(self, tmp_path):
        dest = tmp_path / "output.yaml"
        atomic_write_yaml({"key": "val"}, dest)
        loaded = yaml.safe_load(dest.read_text(encoding="utf-8"))
        assert loaded == {"key": "val"}

    def test_creates_parent_dirs(self, tmp_path):
        dest = tmp_path / "nested" / "deep" / "output.yaml"
        atomic_write_yaml({"x": 1}, dest)
        assert dest.exists()

    def test_overwrites_existing_file(self, tmp_path):
        dest = tmp_path / "output.yaml"
        atomic_write_yaml({"old": True}, dest)
        atomic_write_yaml({"new": True}, dest)
        loaded = yaml.safe_load(dest.read_text(encoding="utf-8"))
        assert loaded == {"new": True}

    def test_list_data(self, tmp_path):
        dest = tmp_path / "list.yaml"
        atomic_write_yaml(["a", "b", "c"], dest)
        loaded = yaml.safe_load(dest.read_text(encoding="utf-8"))
        assert loaded == ["a", "b", "c"]

    def test_accepts_string_path(self, tmp_path):
        dest = tmp_path / "str_path.yaml"
        atomic_write_yaml({"via": "string"}, str(dest))
        assert dest.exists()

    def test_allow_unicode_flag(self, tmp_path):
        dest = tmp_path / "unicode.yaml"
        atomic_write_yaml({"greeting": "héllo"}, dest, allow_unicode=True)
        loaded = yaml.safe_load(dest.read_text(encoding="utf-8"))
        assert loaded["greeting"] == "héllo"

    def test_no_remaining_tmp_files(self, tmp_path):
        dest = tmp_path / "clean.yaml"
        atomic_write_yaml({"clean": True}, dest)
        tmp_files = list(tmp_path.glob(".tmp_yaml_*.yaml"))
        assert tmp_files == []

    def test_nested_data_roundtrip(self, tmp_path):
        data = {"level1": {"level2": {"level3": [1, 2, 3]}}}
        dest = tmp_path / "nested.yaml"
        atomic_write_yaml(data, dest)
        loaded = yaml.safe_load(dest.read_text(encoding="utf-8"))
        assert loaded == data


# ---------------------------------------------------------------------------
# atomic_write_text
# ---------------------------------------------------------------------------

class TestAtomicWriteText:
    def test_creates_file(self, tmp_path):
        dest = tmp_path / "output.txt"
        atomic_write_text(dest, "hello world")
        assert dest.exists()

    def test_content_matches(self, tmp_path):
        dest = tmp_path / "output.txt"
        atomic_write_text(dest, "hello world")
        assert dest.read_text(encoding="utf-8") == "hello world"

    def test_creates_parent_dirs(self, tmp_path):
        dest = tmp_path / "deep" / "dir" / "file.txt"
        atomic_write_text(dest, "content")
        assert dest.exists()

    def test_overwrites_existing(self, tmp_path):
        dest = tmp_path / "file.txt"
        atomic_write_text(dest, "first")
        atomic_write_text(dest, "second")
        assert dest.read_text(encoding="utf-8") == "second"

    def test_empty_content(self, tmp_path):
        dest = tmp_path / "empty.txt"
        atomic_write_text(dest, "")
        assert dest.read_text(encoding="utf-8") == ""

    def test_multiline_content(self, tmp_path):
        dest = tmp_path / "multi.txt"
        content = "line1\nline2\nline3"
        atomic_write_text(dest, content)
        assert dest.read_text(encoding="utf-8") == content

    def test_custom_encoding(self, tmp_path):
        dest = tmp_path / "latin.txt"
        atomic_write_text(dest, "café", encoding="utf-8")
        assert dest.read_text(encoding="utf-8") == "café"

    def test_accepts_string_path(self, tmp_path):
        dest = tmp_path / "str.txt"
        atomic_write_text(str(dest), "via string path")
        assert dest.read_text(encoding="utf-8") == "via string path"

    def test_no_remaining_tmp_files(self, tmp_path):
        dest = tmp_path / "clean.txt"
        atomic_write_text(dest, "data")
        tmp_files = list(tmp_path.glob("*.navig~"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# log_shadow_anomaly
# ---------------------------------------------------------------------------

class TestLogShadowAnomaly:
    def test_never_raises(self, tmp_path, monkeypatch):
        # Redirect _PERF_DIR to tmp so we don't pollute real navig config
        import navig.core.yaml_io as yio
        monkeypatch.setattr(yio, "_PERF_DIR", tmp_path / "perf")
        # Should not raise
        log_shadow_anomaly("test_log", "test_event", {"key": "value"})

    def test_creates_jsonl_file(self, tmp_path, monkeypatch):
        import navig.core.yaml_io as yio
        perf_dir = tmp_path / "perf"
        monkeypatch.setattr(yio, "_PERF_DIR", perf_dir)
        log_shadow_anomaly("mylog", "my_event", {"x": 1})
        assert (perf_dir / "mylog.jsonl").exists()

    def test_jsonl_entry_is_valid_json(self, tmp_path, monkeypatch):
        import navig.core.yaml_io as yio
        perf_dir = tmp_path / "perf"
        monkeypatch.setattr(yio, "_PERF_DIR", perf_dir)
        log_shadow_anomaly("log", "ev", {"data": "value"})
        line = (perf_dir / "log.jsonl").read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert isinstance(parsed, dict)

    def test_jsonl_entry_has_event_field(self, tmp_path, monkeypatch):
        import navig.core.yaml_io as yio
        perf_dir = tmp_path / "perf"
        monkeypatch.setattr(yio, "_PERF_DIR", perf_dir)
        log_shadow_anomaly("log", "my_event", {})
        line = (perf_dir / "log.jsonl").read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert parsed["event"] == "my_event"

    def test_jsonl_entry_has_ts_field(self, tmp_path, monkeypatch):
        import navig.core.yaml_io as yio
        perf_dir = tmp_path / "perf"
        monkeypatch.setattr(yio, "_PERF_DIR", perf_dir)
        log_shadow_anomaly("log", "ev", {})
        line = (perf_dir / "log.jsonl").read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert "ts" in parsed
        assert isinstance(parsed["ts"], (int, float))

    def test_jsonl_entry_has_data_field(self, tmp_path, monkeypatch):
        import navig.core.yaml_io as yio
        perf_dir = tmp_path / "perf"
        monkeypatch.setattr(yio, "_PERF_DIR", perf_dir)
        log_shadow_anomaly("log", "ev", {"my_key": "my_value"})
        line = (perf_dir / "log.jsonl").read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert parsed["data"]["my_key"] == "my_value"

    def test_appends_multiple_entries(self, tmp_path, monkeypatch):
        import navig.core.yaml_io as yio
        perf_dir = tmp_path / "perf"
        monkeypatch.setattr(yio, "_PERF_DIR", perf_dir)
        log_shadow_anomaly("log", "ev1", {})
        log_shadow_anomaly("log", "ev2", {})
        lines = (perf_dir / "log.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# YamlDocument dataclass
# ---------------------------------------------------------------------------

class TestYamlDocument:
    def test_creation(self):
        doc = YamlDocument(data={"key": "val"}, line_map={})
        assert doc.data == {"key": "val"}
        assert doc.line_map == {}

    def test_is_frozen(self):
        doc = YamlDocument(data={"x": 1}, line_map={})
        with pytest.raises((AttributeError, TypeError)):
            doc.data = {"y": 2}  # type: ignore[misc]

    def test_line_map_stores_tuples(self):
        line_map = {("key",): 3}
        doc = YamlDocument(data={}, line_map=line_map)
        assert doc.line_map[("key",)] == 3
