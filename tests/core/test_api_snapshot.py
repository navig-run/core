"""
Tests for Section 18 — JSON API & Memory Integration.

Covers:
  - ApiToolResult schema (to_dict, to_snapshot_dict, to_llm_dict, from_error, from_dict)
  - redact_sensitive (nested dicts, lists, key patterns, extra_keys)
  - validate_api_result (valid, invalid status, missing tool, error without message)
  - SnapshotPolicy + _parse_retention
  - should_store decision helper
  - SnapshotWriter (write allowed, write denied, force, redaction)
  - load_snapshots (filter by tool, max_age, limit)
  - is_stale (fresh vs stale)
  - prune_snapshots (removes expired, keeps valid)
  - clear_snapshots (by tool, by age)
  - api_pack registration (6 tools, correct metadata)
  - infra.metrics.node_status handler returns ApiToolResult format
  - Integration: tool → snapshot → ContextBuilder loads it
  - ContextBuilder api_snapshots + stale_sources population
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

# ═══════════════════════════════════════════════════════════════
# 1. ApiToolResult & ApiSource
# ═══════════════════════════════════════════════════════════════


class TestApiSource:
    """Tests for ApiSource dataclass."""

    def test_defaults(self):
        from navig.tools.api_schema import ApiSource

        src = ApiSource(tool="test.tool")
        assert src.tool == "test.tool"
        assert src.endpoint == ""
        assert len(src.timestamp) > 0  # auto-generated ISO timestamp

    def test_custom_values(self):
        from navig.tools.api_schema import ApiSource

        src = ApiSource(
            tool="foo",
            endpoint="https://api.example.com/v1",
            timestamp="2025-01-01T00:00:00Z",
        )
        assert src.tool == "foo"
        assert src.endpoint == "https://api.example.com/v1"
        assert src.timestamp == "2025-01-01T00:00:00Z"


class TestApiToolResult:
    """Tests for the standardized ApiToolResult envelope."""

    def _make_ok(self, **kwargs) -> "ApiToolResult":
        from navig.tools.api_schema import ApiSource, ApiToolResult

        defaults = dict(
            status="ok",
            raw_json={"full": "payload"},
            normalized={"price": 42000.0, "symbol": "BTC/USD"},
            source=ApiSource(tool="trading.fetch.ohlc", endpoint="https://exchange.io/api"),
        )
        defaults.update(kwargs)
        return ApiToolResult(**defaults)

    def test_ok_property_true(self):
        r = self._make_ok()
        assert r.ok is True

    def test_ok_property_false_on_error_status(self):
        r = self._make_ok(status="error", error="timeout")
        assert r.ok is False

    def test_ok_property_false_when_error_set(self):
        r = self._make_ok(error="something went wrong")
        assert r.ok is False

    def test_to_dict_includes_raw_json(self):
        r = self._make_ok()
        d = r.to_dict()
        assert d["raw_json"] == {"full": "payload"}
        assert d["status"] == "ok"
        assert d["normalized"] == {"price": 42000.0, "symbol": "BTC/USD"}
        assert d["source"]["tool"] == "trading.fetch.ohlc"
        assert d["error"] is None

    def test_to_snapshot_dict_excludes_raw_json(self):
        r = self._make_ok()
        d = r.to_snapshot_dict()
        assert "raw_json" not in d
        assert d["status"] == "ok"
        assert d["normalized"]["price"] == 42000.0

    def test_to_snapshot_dict_redacts_sensitive_fields(self):
        from navig.tools.api_schema import ApiSource, ApiToolResult

        r = ApiToolResult(
            status="ok",
            raw_json={},
            normalized={"api_key": "sk-secret-123", "data": "safe"},
            source=ApiSource(tool="test"),
        )
        d = r.to_snapshot_dict()
        assert d["normalized"]["api_key"] == "***REDACTED***"
        assert d["normalized"]["data"] == "safe"

    def test_to_llm_dict_minimal(self):
        r = self._make_ok()
        d = r.to_llm_dict()
        assert d["data"] == {"price": 42000.0, "symbol": "BTC/USD"}
        assert d["tool"] == "trading.fetch.ohlc"
        assert "fetched_at" in d
        assert d["status"] == "ok"
        assert "raw_json" not in d
        assert "error" not in d or d.get("error") is None  # not present in llm dict

    def test_from_error_factory(self):
        from navig.tools.api_schema import ApiToolResult

        r = ApiToolResult.from_error("web.api.get_json", "Connection refused", "https://bad.api")
        assert r.status == "error"
        assert r.error == "Connection refused"
        assert r.source.tool == "web.api.get_json"
        assert r.source.endpoint == "https://bad.api"
        assert r.ok is False

    def test_from_dict_round_trip(self):
        r = self._make_ok()
        d = r.to_dict()
        rebuilt = type(r).from_dict(d)
        assert rebuilt.status == r.status
        assert rebuilt.normalized == r.normalized
        assert rebuilt.source.tool == r.source.tool
        assert rebuilt.source.endpoint == r.source.endpoint
        assert rebuilt.error == r.error

    def test_from_dict_missing_keys(self):
        from navig.tools.api_schema import ApiToolResult

        r = ApiToolResult.from_dict({})
        assert r.status == "ok"
        assert r.normalized == {}
        assert r.source.tool == "unknown"


# ═══════════════════════════════════════════════════════════════
# 2. redact_sensitive
# ═══════════════════════════════════════════════════════════════


class TestRedactSensitive:
    """Tests for the redact_sensitive utility."""

    def test_simple_dict(self):
        from navig.tools.api_schema import redact_sensitive

        data = {"api_key": "sk-123", "name": "alice"}
        result = redact_sensitive(data)
        assert result["api_key"] == "***REDACTED***"
        assert result["name"] == "alice"

    def test_nested_dict(self):
        from navig.tools.api_schema import redact_sensitive

        data = {"config": {"secret": "s3cr3t", "host": "localhost"}}
        result = redact_sensitive(data)
        assert result["config"]["secret"] == "***REDACTED***"
        assert result["config"]["host"] == "localhost"

    def test_list_of_dicts(self):
        from navig.tools.api_schema import redact_sensitive

        data = [{"token": "abc"}, {"value": 42}]
        result = redact_sensitive(data)
        assert result[0]["token"] == "***REDACTED***"
        assert result[1]["value"] == 42

    def test_deeply_nested(self):
        from navig.tools.api_schema import redact_sensitive

        data = {"a": {"b": {"c": [{"password": "pw123"}]}}}
        result = redact_sensitive(data)
        assert result["a"]["b"]["c"][0]["password"] == "***REDACTED***"

    def test_extra_keys(self):
        from navig.tools.api_schema import redact_sensitive

        data = {"custom_field": "sensitive_value", "safe": "ok"}
        result = redact_sensitive(data, extra_keys={"custom_field"})
        assert result["custom_field"] == "***REDACTED***"
        assert result["safe"] == "ok"

    def test_no_mutation(self):
        from navig.tools.api_schema import redact_sensitive

        data = {"api_key": "original"}
        result = redact_sensitive(data)
        assert data["api_key"] == "original"  # original not mutated
        assert result["api_key"] == "***REDACTED***"

    def test_passthrough_scalar(self):
        from navig.tools.api_schema import redact_sensitive

        assert redact_sensitive(42) == 42
        assert redact_sensitive("hello") == "hello"
        assert redact_sensitive(None) is None

    def test_sensitive_key_patterns(self):
        from navig.tools.api_schema import redact_sensitive

        keys_to_test = [
            "api_key",
            "API_KEY",
            "apiKey",
            "secret",
            "Secret_value",
            "token",
            "access_token",
            "auth_token",
            "password",
            "passwd",
            "credential",
            "Credentials",
            "private_key",
            "privateKey",
            "session_id",
            "credit_card",
            "card_number",
            "cvv",
            "email",
            "phone",
        ]
        data = {k: f"val_{k}" for k in keys_to_test}
        result = redact_sensitive(data)
        for k in keys_to_test:
            assert result[k] == "***REDACTED***", f"Expected {k} to be redacted"


# ═══════════════════════════════════════════════════════════════
# 3. validate_api_result
# ═══════════════════════════════════════════════════════════════


class TestValidateApiResult:
    """Tests for validate_api_result."""

    def test_valid_result(self):
        from navig.tools.api_schema import ApiSource, ApiToolResult, validate_api_result

        r = ApiToolResult(
            status="ok",
            normalized={"data": 1},
            source=ApiSource(tool="test.tool"),
        )
        assert validate_api_result(r) == []

    def test_invalid_status(self):
        from navig.tools.api_schema import ApiSource, ApiToolResult, validate_api_result

        r = ApiToolResult(
            status="unknown",
            normalized={},
            source=ApiSource(tool="test"),
        )
        issues = validate_api_result(r)
        assert any("Invalid status" in i for i in issues)

    def test_empty_tool(self):
        from navig.tools.api_schema import ApiSource, ApiToolResult, validate_api_result

        r = ApiToolResult(
            status="ok",
            normalized={},
            source=ApiSource(tool=""),
        )
        issues = validate_api_result(r)
        assert any("source.tool is empty" in i for i in issues)

    def test_error_without_message(self):
        from navig.tools.api_schema import ApiSource, ApiToolResult, validate_api_result

        r = ApiToolResult(
            status="error",
            normalized={},
            source=ApiSource(tool="test"),
            error=None,
        )
        issues = validate_api_result(r)
        assert any("error message is empty" in i for i in issues)

    def test_error_with_message_is_valid(self):
        from navig.tools.api_schema import ApiSource, ApiToolResult, validate_api_result

        r = ApiToolResult(
            status="error",
            normalized={},
            source=ApiSource(tool="test"),
            error="timeout",
        )
        assert validate_api_result(r) == []


# ═══════════════════════════════════════════════════════════════
# 4. SnapshotPolicy & _parse_retention
# ═══════════════════════════════════════════════════════════════


class TestSnapshotPolicy:
    """Tests for SnapshotPolicy and retention parsing."""

    def test_default_policy(self):
        from navig.memory.snapshot import SnapshotPolicy

        p = SnapshotPolicy()
        assert p.store is False
        assert p.retention == "7d"

    def test_retention_delta_hours(self):
        from navig.memory.snapshot import SnapshotPolicy

        p = SnapshotPolicy(store=True, retention="24h")
        delta = p.retention_delta()
        assert delta == timedelta(hours=24)

    def test_retention_delta_days(self):
        from navig.memory.snapshot import SnapshotPolicy

        p = SnapshotPolicy(store=True, retention="7d")
        assert p.retention_delta() == timedelta(days=7)

    def test_retention_delta_weeks(self):
        from navig.memory.snapshot import SnapshotPolicy

        p = SnapshotPolicy(store=True, retention="4w")
        assert p.retention_delta() == timedelta(weeks=4)

    def test_retention_delta_months(self):
        from navig.memory.snapshot import SnapshotPolicy

        p = SnapshotPolicy(store=True, retention="2m")
        assert p.retention_delta() == timedelta(days=60)

    def test_invalid_retention_defaults_7d(self):
        from navig.memory.snapshot import _parse_retention

        delta = _parse_retention("bogus")
        assert delta == timedelta(days=7)


class TestLoadSnapshotPolicies:
    """Tests for load_snapshot_policies."""

    def test_from_dict(self):
        from navig.memory.snapshot import load_snapshot_policies

        config = {
            "trading.fetch.ohlc": {"store": True, "retention": "7d"},
            "web.api.get_json": {"store": False},
        }
        policies = load_snapshot_policies(config)
        assert "trading.fetch.ohlc" in policies
        assert policies["trading.fetch.ohlc"].store is True
        assert policies["trading.fetch.ohlc"].retention == "7d"
        assert policies["web.api.get_json"].store is False

    def test_empty_config(self):
        from navig.memory.snapshot import load_snapshot_policies

        policies = load_snapshot_policies({})
        assert policies == {}

    def test_non_dict_values_skipped(self):
        from navig.memory.snapshot import load_snapshot_policies

        config = {"tool.a": "not-a-dict", "tool.b": {"store": True}}
        policies = load_snapshot_policies(config)
        assert "tool.a" not in policies
        assert "tool.b" in policies

    def test_load_policies_from_yaml_failure_returns_empty_and_logs_debug(self):
        import navig.memory.snapshot as snap_mod

        with (
            patch("navig.config.get_config_manager", side_effect=RuntimeError("boom")),
            patch.object(snap_mod.logger, "debug") as debug_log,
        ):
            result = snap_mod._load_policies_from_yaml()

        assert result == {}
        debug_log.assert_called_once()


class TestShouldStore:
    """Tests for should_store decision helper."""

    def test_store_true_returns_policy(self):
        from navig.memory.snapshot import SnapshotPolicy, should_store

        policies = {"my.tool": SnapshotPolicy(store=True, retention="24h")}
        store, delta = should_store("my.tool", policies)
        assert store is True
        assert delta == timedelta(hours=24)

    def test_store_false_returns_skip(self):
        from navig.memory.snapshot import SnapshotPolicy, should_store

        policies = {"my.tool": SnapshotPolicy(store=False)}
        store, delta = should_store("my.tool", policies)
        assert store is False
        assert delta is None

    def test_unknown_tool_returns_skip(self):
        from navig.memory.snapshot import SnapshotPolicy, should_store

        policies = {"other.tool": SnapshotPolicy(store=True)}
        store, delta = should_store("missing.tool", policies)
        assert store is False
        assert delta is None


# ═══════════════════════════════════════════════════════════════
# 5. SnapshotWriter
# ═══════════════════════════════════════════════════════════════


class TestSnapshotWriter:
    """Tests for the SnapshotWriter class."""

    @pytest.fixture
    def snap_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "snapshots"
        d.mkdir()
        return d

    @pytest.fixture
    def writer(self, snap_dir: Path):
        from navig.memory.snapshot import SnapshotPolicy, SnapshotWriter

        policies = {
            "allowed.tool": SnapshotPolicy(store=True, retention="7d"),
            "denied.tool": SnapshotPolicy(store=False),
        }
        return SnapshotWriter(snapshot_dir=snap_dir, policies=policies)

    def _make_tool_result(self, tool: str = "allowed.tool", **overrides) -> Dict[str, Any]:
        from navig.tools.api_schema import ApiSource, ApiToolResult

        r = ApiToolResult(
            status="ok",
            raw_json={"full": "data"},
            normalized={"metric": 99, "info": "safe"},
            source=ApiSource(tool=tool, endpoint="https://api.example.com"),
        )
        d = r.to_dict()
        d.update(overrides)
        return d

    def test_write_allowed(self, writer, snap_dir):
        result = self._make_tool_result("allowed.tool")
        written = writer.write(result, workspace="test")
        assert written is True
        f = snap_dir / "test.jsonl"
        assert f.is_file()
        lines = f.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool"] == "allowed.tool"

    def test_write_denied(self, writer, snap_dir):
        result = self._make_tool_result("denied.tool")
        written = writer.write(result, workspace="test")
        assert written is False
        f = snap_dir / "test.jsonl"
        assert not f.is_file()

    def test_write_unknown_tool_denied(self, writer, snap_dir):
        result = self._make_tool_result("unknown.tool")
        written = writer.write(result, workspace="test")
        assert written is False

    def test_write_force_overrides_policy(self, writer, snap_dir):
        result = self._make_tool_result("denied.tool")
        written = writer.write(result, workspace="test", force=True)
        assert written is True
        assert (snap_dir / "test.jsonl").is_file()

    def test_write_redacts_sensitive_fields(self, writer, snap_dir):
        from navig.tools.api_schema import ApiSource, ApiToolResult

        r = ApiToolResult(
            status="ok",
            raw_json={},
            normalized={"api_key": "sk-secret", "data": 42},
            source=ApiSource(tool="allowed.tool"),
        )
        writer.write(r.to_dict(), workspace="test")
        f = snap_dir / "test.jsonl"
        entry = json.loads(f.read_text(encoding="utf-8").strip())
        assert entry["normalized"]["api_key"] == "***REDACTED***"
        assert entry["normalized"]["data"] == 42

    def test_write_multiple_appends(self, writer, snap_dir):
        for i in range(3):
            result = self._make_tool_result("allowed.tool")
            result["normalized"] = {"i": i}
            writer.write(result, workspace="test")
        f = snap_dir / "test.jsonl"
        lines = f.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    def test_write_from_api_result(self, snap_dir):
        from navig.memory.snapshot import SnapshotPolicy, SnapshotWriter
        from navig.tools.api_schema import ApiSource, ApiToolResult

        policies = {"my.tool": SnapshotPolicy(store=True, retention="1d")}
        w = SnapshotWriter(snapshot_dir=snap_dir, policies=policies)
        r = ApiToolResult(
            status="ok",
            normalized={"x": 1},
            source=ApiSource(tool="my.tool"),
        )
        written = w.write_from_api_result(r, workspace="ws")
        assert written is True
        assert (snap_dir / "ws.jsonl").is_file()


# ═══════════════════════════════════════════════════════════════
# 6. load_snapshots
# ═══════════════════════════════════════════════════════════════


class TestLoadSnapshots:
    """Tests for load_snapshots reader."""

    @pytest.fixture
    def populated_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "snapshots"
        d.mkdir()
        now = datetime.now(timezone.utc)
        entries = [
            {
                "tool": "tool.a",
                "normalized": {"v": 1},
                "source_endpoint": "",
                "timestamp": (now - timedelta(minutes=10)).isoformat(),
                "workspace": "ws",
                "lane": "",
                "host": "",
            },
            {
                "tool": "tool.b",
                "normalized": {"v": 2},
                "source_endpoint": "",
                "timestamp": (now - timedelta(minutes=5)).isoformat(),
                "workspace": "ws",
                "lane": "",
                "host": "",
            },
            {
                "tool": "tool.a",
                "normalized": {"v": 3},
                "source_endpoint": "",
                "timestamp": now.isoformat(),
                "workspace": "ws",
                "lane": "",
                "host": "",
            },
            {
                "tool": "tool.a",
                "normalized": {"v": 0},
                "source_endpoint": "",
                "timestamp": (now - timedelta(hours=2)).isoformat(),
                "workspace": "ws",
                "lane": "",
                "host": "",
            },
        ]
        f = d / "ws.jsonl"
        with open(f, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")
        return d

    def test_load_all(self, populated_dir):
        from navig.memory.snapshot import load_snapshots

        entries = load_snapshots(workspace="ws", snapshot_dir=populated_dir)
        assert len(entries) == 4  # all entries
        # Most recent first
        assert entries[0].normalized == {"v": 0}  # reversed order: last line in file comes first

    def test_filter_by_tool(self, populated_dir):
        from navig.memory.snapshot import load_snapshots

        entries = load_snapshots(workspace="ws", tool="tool.a", snapshot_dir=populated_dir)
        assert all(e.tool == "tool.a" for e in entries)
        assert len(entries) == 3

    def test_filter_by_max_age(self, populated_dir):
        from navig.memory.snapshot import load_snapshots

        entries = load_snapshots(workspace="ws", max_age_minutes=30, snapshot_dir=populated_dir)
        # Only entries within last 30 minutes (v=1, v=2, v=3)
        assert len(entries) == 3

    def test_limit(self, populated_dir):
        from navig.memory.snapshot import load_snapshots

        entries = load_snapshots(workspace="ws", limit=2, snapshot_dir=populated_dir)
        assert len(entries) == 2

    def test_missing_workspace(self, tmp_path):
        from navig.memory.snapshot import load_snapshots

        entries = load_snapshots(workspace="nonexistent", snapshot_dir=tmp_path)
        assert entries == []


# ═══════════════════════════════════════════════════════════════
# 7. is_stale
# ═══════════════════════════════════════════════════════════════


class TestIsStale:
    """Tests for the is_stale helper."""

    @pytest.fixture
    def fresh_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "snapshots"
        d.mkdir()
        now = datetime.now(timezone.utc)
        entry = {
            "tool": "my.tool",
            "normalized": {"ok": True},
            "source_endpoint": "",
            "timestamp": now.isoformat(),
            "workspace": "ws",
            "lane": "",
            "host": "",
        }
        f = d / "ws.jsonl"
        f.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        return d

    def test_fresh_is_not_stale(self, fresh_dir):
        from navig.memory.snapshot import is_stale

        assert is_stale("ws", "my.tool", max_age_minutes=60, snapshot_dir=fresh_dir) is False

    def test_stale_when_no_snapshot(self, tmp_path):
        from navig.memory.snapshot import is_stale

        d = tmp_path / "empty_snaps"
        d.mkdir()
        assert is_stale("ws", "missing.tool", max_age_minutes=60, snapshot_dir=d) is True

    def test_stale_when_old(self, tmp_path):
        from navig.memory.snapshot import is_stale

        d = tmp_path / "snapshots"
        d.mkdir()
        old = datetime.now(timezone.utc) - timedelta(hours=3)
        entry = {
            "tool": "my.tool",
            "normalized": {},
            "source_endpoint": "",
            "timestamp": old.isoformat(),
            "workspace": "ws",
            "lane": "",
            "host": "",
        }
        (d / "ws.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
        assert is_stale("ws", "my.tool", max_age_minutes=60, snapshot_dir=d) is True


# ═══════════════════════════════════════════════════════════════
# 8. prune_snapshots
# ═══════════════════════════════════════════════════════════════


class TestPruneSnapshots:
    """Tests for retention-based snapshot pruning."""

    def test_prune_removes_old(self, tmp_path):
        from navig.memory.snapshot import SnapshotPolicy, prune_snapshots

        d = tmp_path / "snaps"
        d.mkdir()
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(days=10)).isoformat()
        fresh_ts = now.isoformat()
        lines = [
            json.dumps(
                {
                    "tool": "t",
                    "normalized": {"old": True},
                    "source_endpoint": "",
                    "timestamp": old_ts,
                    "workspace": "ws",
                    "lane": "",
                    "host": "",
                }
            ),
            json.dumps(
                {
                    "tool": "t",
                    "normalized": {"fresh": True},
                    "source_endpoint": "",
                    "timestamp": fresh_ts,
                    "workspace": "ws",
                    "lane": "",
                    "host": "",
                }
            ),
        ]
        (d / "ws.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

        policies = {"t": SnapshotPolicy(store=True, retention="7d")}
        pruned = prune_snapshots("ws", policies, snapshot_dir=d)
        assert pruned == 1

        remaining = (d / "ws.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(remaining) == 1
        assert json.loads(remaining[0])["normalized"]["fresh"] is True

    def test_prune_nothing_to_remove(self, tmp_path):
        from navig.memory.snapshot import SnapshotPolicy, prune_snapshots

        d = tmp_path / "snaps"
        d.mkdir()
        now = datetime.now(timezone.utc).isoformat()
        line = json.dumps(
            {
                "tool": "t",
                "normalized": {"ok": 1},
                "source_endpoint": "",
                "timestamp": now,
                "workspace": "ws",
                "lane": "",
                "host": "",
            }
        )
        (d / "ws.jsonl").write_text(line + "\n", encoding="utf-8")

        policies = {"t": SnapshotPolicy(store=True, retention="7d")}
        pruned = prune_snapshots("ws", policies, snapshot_dir=d)
        assert pruned == 0

    def test_prune_missing_file(self, tmp_path):
        from navig.memory.snapshot import prune_snapshots

        assert prune_snapshots("ws", {}, snapshot_dir=tmp_path) == 0

    def test_prune_uses_atomic_rewrite(self, tmp_path):
        from navig.memory.snapshot import SnapshotPolicy, prune_snapshots

        d = tmp_path / "snaps"
        d.mkdir()
        now = datetime.now(timezone.utc).isoformat()
        line = json.dumps(
            {
                "tool": "t",
                "normalized": {"ok": 1},
                "source_endpoint": "",
                "timestamp": now,
                "workspace": "ws",
                "lane": "",
                "host": "",
            }
        )
        (d / "ws.jsonl").write_text(line + "\n", encoding="utf-8")

        policies = {"t": SnapshotPolicy(store=True, retention="7d")}
        with patch("navig.memory.snapshot._atomic_write_text") as atomic_write:
            pruned = prune_snapshots("ws", policies, snapshot_dir=d)

        assert pruned == 0
        atomic_write.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# 9. clear_snapshots
# ═══════════════════════════════════════════════════════════════


class TestClearSnapshots:
    """Tests for clear_snapshots."""

    def _write_entries(self, d: Path, entries: list):
        d.mkdir(parents=True, exist_ok=True)
        f = d / "ws.jsonl"
        with open(f, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    def test_clear_by_tool(self, tmp_path):
        from navig.memory.snapshot import clear_snapshots

        d = tmp_path / "snaps"
        now = datetime.now(timezone.utc).isoformat()
        entries = [
            {
                "tool": "a",
                "normalized": {},
                "source_endpoint": "",
                "timestamp": now,
                "workspace": "ws",
                "lane": "",
                "host": "",
            },
            {
                "tool": "b",
                "normalized": {},
                "source_endpoint": "",
                "timestamp": now,
                "workspace": "ws",
                "lane": "",
                "host": "",
            },
            {
                "tool": "a",
                "normalized": {},
                "source_endpoint": "",
                "timestamp": now,
                "workspace": "ws",
                "lane": "",
                "host": "",
            },
        ]
        self._write_entries(d, entries)
        removed = clear_snapshots("ws", tool="a", snapshot_dir=d)
        assert removed == 2
        remaining = (d / "ws.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(remaining) == 1
        assert json.loads(remaining[0])["tool"] == "b"

    def test_clear_by_age(self, tmp_path):
        from navig.memory.snapshot import clear_snapshots

        d = tmp_path / "snaps"
        now = datetime.now(timezone.utc)
        entries = [
            {
                "tool": "a",
                "normalized": {},
                "source_endpoint": "",
                "timestamp": (now - timedelta(days=10)).isoformat(),
                "workspace": "ws",
                "lane": "",
                "host": "",
            },
            {
                "tool": "a",
                "normalized": {},
                "source_endpoint": "",
                "timestamp": now.isoformat(),
                "workspace": "ws",
                "lane": "",
                "host": "",
            },
        ]
        self._write_entries(d, entries)
        removed = clear_snapshots("ws", older_than="7d", snapshot_dir=d)
        assert removed == 1

    def test_clear_missing_file(self, tmp_path):
        from navig.memory.snapshot import clear_snapshots

        assert clear_snapshots("ws", snapshot_dir=tmp_path) == 0

    def test_clear_uses_atomic_rewrite(self, tmp_path):
        from navig.memory.snapshot import clear_snapshots

        d = tmp_path / "snaps"
        now = datetime.now(timezone.utc).isoformat()
        entries = [
            {
                "tool": "a",
                "normalized": {},
                "source_endpoint": "",
                "timestamp": now,
                "workspace": "ws",
                "lane": "",
                "host": "",
            }
        ]
        self._write_entries(d, entries)

        with patch("navig.memory.snapshot._atomic_write_text") as atomic_write:
            removed = clear_snapshots("ws", tool="a", snapshot_dir=d)

        assert removed == 1
        atomic_write.assert_called_once()


class TestAtomicWriteText:
    """Tests for atomic snapshot text writes."""

    def test_atomic_write_retries_permission_error_on_windows(self, tmp_path):
        from navig.memory.snapshot import _atomic_write_text

        target = tmp_path / "snap.jsonl"
        # The retry logic lives in navig.core.yaml_io._atomic_replace, where
        # `os` is a module-level import and `sys` is imported locally (so we
        # patch the global `sys.platform` instead of a module attribute).
        with (
            patch("sys.platform", "win32"),
            patch(
                "navig.core.yaml_io.os.replace",
                side_effect=[PermissionError("locked"), PermissionError("locked"), None],
            ) as replace_mock,
        ):
            _atomic_write_text(target, "{}\n")

        assert replace_mock.call_count == 3


# ═══════════════════════════════════════════════════════════════
# 10. SnapshotEntry serialization
# ═══════════════════════════════════════════════════════════════


class TestSnapshotEntry:
    """Tests for SnapshotEntry to_line / from_line."""

    def test_round_trip(self):
        from navig.memory.snapshot import SnapshotEntry

        e = SnapshotEntry(
            tool="test.tool",
            normalized={"key": "value"},
            source_endpoint="https://api.test.com",
            timestamp="2025-01-01T00:00:00Z",
            workspace="myws",
            lane="main",
            host="prod",
        )
        line = e.to_line()
        restored = SnapshotEntry.from_line(line)
        assert restored.tool == e.tool
        assert restored.normalized == e.normalized
        assert restored.workspace == e.workspace
        assert restored.lane == e.lane
        assert restored.host == e.host

    def test_to_line_is_valid_json(self):
        from navig.memory.snapshot import SnapshotEntry

        e = SnapshotEntry(tool="t", normalized={})
        line = e.to_line()
        parsed = json.loads(line)
        assert parsed["tool"] == "t"

    def test_from_line_missing_fields(self):
        from navig.memory.snapshot import SnapshotEntry

        line = json.dumps({"tool": "x"})
        e = SnapshotEntry.from_line(line)
        assert e.tool == "x"
        assert e.normalized == {}
        assert e.workspace == "default"


# ═══════════════════════════════════════════════════════════════
# 11. api_pack registration
# ═══════════════════════════════════════════════════════════════


class TestApiPackRegistration:
    """Test that api_pack registers 6 tools with correct metadata."""

    def test_register_all_tools(self):
        from navig.tools.router import ToolRegistry

        registry = ToolRegistry()
        from navig.tools.domains.api_pack import register_tools

        register_tools(registry)

        expected_names = [
            "web.api.get_json",
            "web.api.post_json",
            "trading.fetch.ohlc",
            "trading.fetch.portfolio",
            "infra.metrics.node_status",
            "infra.inventory.servers",
        ]
        for name in expected_names:
            meta = registry.get_tool(name)
            assert meta is not None, f"Missing tool: {name}"

    def test_tool_domains(self):
        from navig.tools.router import ToolDomain, ToolRegistry

        registry = ToolRegistry()
        from navig.tools.domains.api_pack import register_tools

        register_tools(registry)

        web_tools = ["web.api.get_json", "web.api.post_json"]
        for name in web_tools:
            meta = registry.get_tool(name)
            assert meta.domain == ToolDomain.WEB

        infra_tools = ["infra.metrics.node_status", "infra.inventory.servers"]
        for name in infra_tools:
            meta = registry.get_tool(name)
            assert meta.domain == ToolDomain.SYSTEM

    def test_infra_node_status_handler(self):
        """infra.metrics.node_status returns ApiToolResult-compatible dict."""
        from navig.tools.domains.api_pack import _infra_node_status

        result = _infra_node_status()
        assert result["status"] == "ok"
        assert "normalized" in result
        assert "source" in result
        assert result["source"]["tool"] == "infra.metrics.node_status"
        norm = result["normalized"]
        assert "cpu_count" in norm
        assert "disk_total_gb" in norm


# ═══════════════════════════════════════════════════════════════
# 12. Integration: tool → snapshot → ContextBuilder
# ═══════════════════════════════════════════════════════════════


class TestIntegrationSnapshot:
    """End-to-end: write snapshot, load it via ContextBuilder adapter."""

    @pytest.fixture
    def integration_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "api_state"
        d.mkdir()
        return d

    def test_tool_result_to_snapshot_to_load(self, integration_dir):
        """Full flow: create ApiToolResult → write snapshot → load it back."""
        from navig.memory.snapshot import SnapshotPolicy, SnapshotWriter, load_snapshots
        from navig.tools.api_schema import ApiSource, ApiToolResult

        # 1. Create result
        result = ApiToolResult(
            status="ok",
            raw_json={"big": "payload", "secret": "leak"},
            normalized={"cpu": 45.2, "disk_free_gb": 100},
            source=ApiSource(tool="infra.metrics.node_status", endpoint="local"),
        )

        # 2. Write via SnapshotWriter
        policies = {"infra.metrics.node_status": SnapshotPolicy(store=True, retention="24h")}
        writer = SnapshotWriter(snapshot_dir=integration_dir, policies=policies)
        assert writer.write_from_api_result(result, workspace="project") is True

        # 3. Load back
        entries = load_snapshots(
            workspace="project",
            tool="infra.metrics.node_status",
            snapshot_dir=integration_dir,
        )
        assert len(entries) == 1
        assert entries[0].normalized["cpu"] == 45.2

    def test_context_builder_loads_snapshots(self, tmp_path):
        """
        ContextBuilder Step 5 includes api_snapshots when policy tools have data.
        """
        from navig.memory.snapshot import SnapshotPolicy, SnapshotWriter

        snap_dir = tmp_path / "api_state"
        snap_dir.mkdir()

        # Write a fresh snapshot
        from navig.tools.api_schema import ApiSource, ApiToolResult

        result = ApiToolResult(
            status="ok",
            normalized={"uptime_hours": 720},
            source=ApiSource(tool="infra.metrics.node_status"),
        )
        policies = {"infra.metrics.node_status": SnapshotPolicy(store=True, retention="24h")}
        writer = SnapshotWriter(snapshot_dir=snap_dir, policies=policies)
        writer.write_from_api_result(result, workspace="default")

        # Patch _load_api_snapshots to use our directory
        with patch("navig.memory.context_builder._load_api_snapshots") as mock_load:
            mock_load.return_value = (
                [
                    {
                        "tool": "infra.metrics.node_status",
                        "data": {"uptime_hours": 720},
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
                [],  # no stale
            )

            from navig.memory.context_builder import ContextBuilder

            builder = ContextBuilder(config={"enabled": True, "include_api_snapshots": True})
            ctx = builder.build_context("What is the server status?")

            assert len(ctx["api_snapshots"]) == 1
            assert ctx["api_snapshots"][0]["tool"] == "infra.metrics.node_status"
            assert ctx["stale_sources"] == []

    def test_context_builder_detects_stale(self, tmp_path):
        """ContextBuilder reports stale sources when no fresh snapshot exists."""
        with patch("navig.memory.context_builder._load_api_snapshots") as mock_load:
            mock_load.return_value = ([], ["infra.metrics.node_status"])

            from navig.memory.context_builder import ContextBuilder

            builder = ContextBuilder(config={"enabled": True, "include_api_snapshots": True})
            ctx = builder.build_context("Check server health")

            assert ctx["api_snapshots"] == []
            assert "infra.metrics.node_status" in ctx["stale_sources"]


# ═══════════════════════════════════════════════════════════════
# 13. Config schema — api_snapshot_policies field
# ═══════════════════════════════════════════════════════════════


class TestConfigSchemaSnapshotPolicies:
    """Verify MemoryConfig accepts api_snapshot_policies."""

    def test_default_empty(self):
        from navig.core.config_schema import GlobalConfig

        cfg = GlobalConfig()
        assert cfg.memory.api_snapshot_policies == {}

    def test_custom_policies(self):
        from navig.core.config_schema import GlobalConfig

        cfg = GlobalConfig(
            memory={
                "api_snapshot_policies": {
                    "trading.fetch.ohlc": {"store": True, "retention": "7d"},
                    "web.api.get_json": {"store": False},
                }
            }
        )
        assert cfg.memory.api_snapshot_policies["trading.fetch.ohlc"]["store"] is True
        assert cfg.memory.api_snapshot_policies["web.api.get_json"]["store"] is False


# ═══════════════════════════════════════════════════════════════
# 14. Singleton management
# ═══════════════════════════════════════════════════════════════


class TestSnapshotSingleton:
    """Tests for get_snapshot_writer / reset_snapshot_writer."""

    def test_singleton_returns_same_instance(self):
        from navig.memory.snapshot import get_snapshot_writer, reset_snapshot_writer

        reset_snapshot_writer()
        w1 = get_snapshot_writer()
        w2 = get_snapshot_writer()
        assert w1 is w2
        reset_snapshot_writer()

    def test_reset_creates_new_instance(self):
        from navig.memory.snapshot import get_snapshot_writer, reset_snapshot_writer

        reset_snapshot_writer()
        w1 = get_snapshot_writer()
        reset_snapshot_writer()
        w2 = get_snapshot_writer()
        assert w1 is not w2
        reset_snapshot_writer()
