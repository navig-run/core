"""Tests for navig.core.yaml_io — atomic YAML/text I/O and shadow logging."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from navig.core.yaml_io import (
    YamlDocument,
    atomic_write_text,
    atomic_write_yaml,
    load_yaml_with_lines,
    log_shadow_anomaly,
    safe_load_yaml,
)


# ──────────────────────────────────────────────────────────────
# safe_load_yaml
# ──────────────────────────────────────────────────────────────


class TestSafeLoadYaml:
    def test_returns_none_for_missing_file(self, tmp_path):
        result = safe_load_yaml(tmp_path / "does_not_exist.yaml")
        assert result is None

    def test_returns_none_for_invalid_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("key: [unclosed bracket", encoding="utf-8")
        result = safe_load_yaml(bad)
        assert result is None

    def test_returns_none_for_empty_file(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        assert safe_load_yaml(empty) is None

    def test_returns_dict_for_valid_yaml(self, tmp_path):
        f = tmp_path / "config.yaml"
        f.write_text("name: navig\nversion: 1\n", encoding="utf-8")
        result = safe_load_yaml(f)
        assert result == {"name": "navig", "version": 1}

    def test_returns_list_for_yaml_list(self, tmp_path):
        f = tmp_path / "list.yaml"
        f.write_text("- a\n- b\n- c\n", encoding="utf-8")
        result = safe_load_yaml(f)
        assert result == ["a", "b", "c"]

    def test_accepts_path_object(self, tmp_path):
        f = tmp_path / "p.yaml"
        f.write_text("x: 1\n", encoding="utf-8")
        assert safe_load_yaml(Path(f)) == {"x": 1}

    def test_accepts_string_path(self, tmp_path):
        f = tmp_path / "s.yaml"
        f.write_text("x: 2\n", encoding="utf-8")
        assert safe_load_yaml(str(f)) == {"x": 2}


# ──────────────────────────────────────────────────────────────
# atomic_write_yaml
# ──────────────────────────────────────────────────────────────


class TestAtomicWriteYaml:
    def test_writes_dict(self, tmp_path):
        dest = tmp_path / "out.yaml"
        atomic_write_yaml({"hello": "world"}, dest)
        loaded = yaml.safe_load(dest.read_text(encoding="utf-8"))
        assert loaded == {"hello": "world"}

    def test_writes_list(self, tmp_path):
        dest = tmp_path / "list.yaml"
        atomic_write_yaml(["a", "b", "c"], dest)
        loaded = yaml.safe_load(dest.read_text(encoding="utf-8"))
        assert loaded == ["a", "b", "c"]

    def test_creates_parent_directories(self, tmp_path):
        dest = tmp_path / "subdir" / "deep" / "out.yaml"
        atomic_write_yaml({"key": "val"}, dest)
        assert dest.exists()

    def test_overwrites_existing_file(self, tmp_path):
        dest = tmp_path / "overwrite.yaml"
        dest.write_text("old: content\n", encoding="utf-8")
        atomic_write_yaml({"new": "content"}, dest)
        loaded = yaml.safe_load(dest.read_text(encoding="utf-8"))
        assert loaded == {"new": "content"}

    def test_preserves_key_insertion_order(self, tmp_path):
        dest = tmp_path / "ordered.yaml"
        data = {"z": 1, "a": 2, "m": 3}
        atomic_write_yaml(data, dest)
        content = dest.read_text(encoding="utf-8")
        keys_in_order = [line.split(":")[0].strip() for line in content.splitlines() if ":" in line]
        assert keys_in_order == ["z", "a", "m"]


# ──────────────────────────────────────────────────────────────
# atomic_write_text
# ──────────────────────────────────────────────────────────────


class TestAtomicWriteText:
    def test_writes_content(self, tmp_path):
        dest = tmp_path / "hello.txt"
        atomic_write_text(dest, "hello world")
        assert dest.read_text(encoding="utf-8") == "hello world"

    def test_creates_parent_directories(self, tmp_path):
        dest = tmp_path / "a" / "b" / "c.txt"
        atomic_write_text(dest, "deep")
        assert dest.read_text(encoding="utf-8") == "deep"

    def test_overwrites_existing(self, tmp_path):
        dest = tmp_path / "file.txt"
        dest.write_text("old", encoding="utf-8")
        atomic_write_text(dest, "new")
        assert dest.read_text(encoding="utf-8") == "new"

    def test_empty_content(self, tmp_path):
        dest = tmp_path / "empty.txt"
        atomic_write_text(dest, "")
        assert dest.read_text(encoding="utf-8") == ""

    def test_multiline_content(self, tmp_path):
        dest = tmp_path / "multi.txt"
        content = "line1\nline2\nline3\n"
        atomic_write_text(dest, content)
        assert dest.read_text(encoding="utf-8") == content


# ──────────────────────────────────────────────────────────────
# load_yaml_with_lines
# ──────────────────────────────────────────────────────────────


class TestLoadYamlWithLines:
    def test_returns_yaml_document(self, tmp_path):
        f = tmp_path / "doc.yaml"
        f.write_text("key: value\n", encoding="utf-8")
        doc = load_yaml_with_lines(f)
        assert isinstance(doc, YamlDocument)

    def test_data_parsed_correctly(self, tmp_path):
        f = tmp_path / "doc.yaml"
        f.write_text("name: navig\nversion: 2\n", encoding="utf-8")
        doc = load_yaml_with_lines(f)
        assert doc.data["name"] == "navig"
        assert doc.data["version"] == "2"

    def test_line_map_populated(self, tmp_path):
        f = tmp_path / "doc.yaml"
        f.write_text("alpha: 1\nbeta: 2\n", encoding="utf-8")
        doc = load_yaml_with_lines(f)
        assert len(doc.line_map) > 0

    def test_keys_have_line_numbers(self, tmp_path):
        f = tmp_path / "keys.yaml"
        f.write_text("first: a\nsecond: b\nthird: c\n", encoding="utf-8")
        doc = load_yaml_with_lines(f)
        # Check that at least one key path has a positive line number
        assert any(v >= 1 for v in doc.line_map.values())

    def test_empty_file_returns_none_data(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("", encoding="utf-8")
        doc = load_yaml_with_lines(f)
        assert doc.data is None

    def test_list_data(self, tmp_path):
        f = tmp_path / "list.yaml"
        f.write_text("- x\n- y\n- z\n", encoding="utf-8")
        doc = load_yaml_with_lines(f)
        assert doc.data == ["x", "y", "z"]

    def test_nested_dict(self, tmp_path):
        f = tmp_path / "nested.yaml"
        f.write_text("parent:\n  child: deep\n", encoding="utf-8")
        doc = load_yaml_with_lines(f)
        assert doc.data["parent"]["child"] == "deep"


# ──────────────────────────────────────────────────────────────
# log_shadow_anomaly
# ──────────────────────────────────────────────────────────────


class TestLogShadowAnomaly:
    def test_does_not_raise(self, tmp_path, monkeypatch):
        from navig.core import yaml_io
        monkeypatch.setattr(yaml_io, "_PERF_DIR", tmp_path / "perf")
        # Must not raise
        log_shadow_anomaly("test_log", "divergence", {"key": "value"})

    def test_writes_jsonl_file(self, tmp_path, monkeypatch):
        import json
        from navig.core import yaml_io
        perf_dir = tmp_path / "perf"
        monkeypatch.setattr(yaml_io, "_PERF_DIR", perf_dir)
        log_shadow_anomaly("mylog", "test_event", {"detail": 42})
        log_file = perf_dir / "mylog.jsonl"
        assert log_file.exists()
        entry = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert entry["event"] == "test_event"
        assert entry["data"] == {"detail": 42}

    def test_appends_multiple_events(self, tmp_path, monkeypatch):
        from navig.core import yaml_io
        perf_dir = tmp_path / "perf"
        monkeypatch.setattr(yaml_io, "_PERF_DIR", perf_dir)
        log_shadow_anomaly("multi", "evt1", {})
        log_shadow_anomaly("multi", "evt2", {})
        lines = (perf_dir / "multi.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2

    def test_silently_ignores_write_failure(self, tmp_path, monkeypatch):
        # Make _PERF_DIR a path inside a file (impossible to mkdir)
        file_not_dir = tmp_path / "a_file"
        file_not_dir.write_text("block")
        from navig.core import yaml_io
        monkeypatch.setattr(yaml_io, "_PERF_DIR", file_not_dir / "subdir")
        # Should not raise
        log_shadow_anomaly("fail", "event", {})
