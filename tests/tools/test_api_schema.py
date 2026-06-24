"""
Hermetic unit tests for navig.tools.api_schema

Covers:
- redact_sensitive: dict / list / nested / extra_keys
- ApiSource dataclass
- ApiToolResult: ok property, to_dict, to_snapshot_dict, to_llm_dict
- ApiToolResult.from_error factory
- ApiToolResult.from_dict roundtrip
"""

import pytest

from navig.tools.api_schema import ApiSource, ApiToolResult, redact_sensitive


# ─────────────────────────────────────────────────────────────
# redact_sensitive
# ─────────────────────────────────────────────────────────────


class TestRedactSensitive:
    def test_plain_value_unchanged(self):
        assert redact_sensitive(42) == 42

    def test_non_sensitive_key_unchanged(self):
        d = {"name": "Alice", "age": 30}
        result = redact_sensitive(d)
        assert result["name"] == "Alice"
        assert result["age"] == 30

    def test_api_key_redacted(self):
        d = {"api_key": "secret123", "model": "gpt4"}
        result = redact_sensitive(d)
        assert result["api_key"] == "***REDACTED***"
        assert result["model"] == "gpt4"

    def test_token_redacted(self):
        d = {"token": "abc", "data": 1}
        assert redact_sensitive(d)["token"] == "***REDACTED***"

    def test_password_redacted(self):
        d = {"password": "s3cr3t"}
        assert redact_sensitive(d)["password"] == "***REDACTED***"

    def test_nested_dict_redacted(self):
        d = {"creds": {"api_key": "key", "user": "admin"}}
        result = redact_sensitive(d)
        assert result["creds"]["api_key"] == "***REDACTED***"
        assert result["creds"]["user"] == "admin"

    def test_list_items_processed(self):
        data = [{"key": "v"}, {"password": "p"}]
        result = redact_sensitive(data)
        assert result[0]["key"] == "v"
        assert result[1]["password"] == "***REDACTED***"

    def test_extra_keys_redacted(self):
        d = {"myfield": "secret", "other": "ok"}
        result = redact_sensitive(d, extra_keys={"myfield"})
        assert result["myfield"] == "***REDACTED***"
        assert result["other"] == "ok"

    def test_case_insensitive_redaction(self):
        d = {"API_KEY": "secret"}
        assert redact_sensitive(d)["API_KEY"] == "***REDACTED***"


# ─────────────────────────────────────────────────────────────
# ApiSource
# ─────────────────────────────────────────────────────────────


class TestApiSource:
    def test_required_field(self):
        src = ApiSource(tool="web.api.get_json")
        assert src.tool == "web.api.get_json"

    def test_endpoint_default(self):
        src = ApiSource(tool="any_tool")
        assert src.endpoint == ""

    def test_timestamp_auto_set(self):
        src = ApiSource(tool="tool")
        assert isinstance(src.timestamp, str)
        assert len(src.timestamp) > 0

    def test_custom_endpoint(self):
        src = ApiSource(tool="trading", endpoint="https://api.example.com/prices")
        assert "example.com" in src.endpoint


# ─────────────────────────────────────────────────────────────
# ApiToolResult
# ─────────────────────────────────────────────────────────────


class TestApiToolResultDefaults:
    def test_default_status_ok(self):
        r = ApiToolResult()
        assert r.status == "ok"

    def test_ok_property_true(self):
        r = ApiToolResult()
        assert r.ok is True

    def test_ok_property_false_on_error_status(self):
        r = ApiToolResult(status="error", error="something failed")
        assert r.ok is False

    def test_ok_property_false_when_error_message_set(self):
        r = ApiToolResult(status="ok", error="oops")
        assert r.ok is False


class TestApiToolResultToDict:
    def _make(self):
        return ApiToolResult(
            status="ok",
            raw_json={"price": 100},
            normalized={"price": 100},
            source=ApiSource(tool="trading.fetch"),
        )

    def test_to_dict_has_required_keys(self):
        d = self._make().to_dict()
        assert "status" in d
        assert "raw_json" in d
        assert "normalized" in d
        assert "source" in d
        assert "error" in d

    def test_to_dict_source_structure(self):
        d = self._make().to_dict()
        src = d["source"]
        assert src["tool"] == "trading.fetch"
        assert "endpoint" in src
        assert "timestamp" in src

    def test_to_snapshot_excludes_raw_json(self):
        d = self._make().to_snapshot_dict()
        assert "raw_json" not in d
        assert "normalized" in d

    def test_to_snapshot_redacts_sensitive(self):
        r = ApiToolResult(
            normalized={"api_key": "secret", "price": 100},
            source=ApiSource(tool="test"),
        )
        d = r.to_snapshot_dict()
        assert d["normalized"]["api_key"] == "***REDACTED***"
        assert d["normalized"]["price"] == 100

    def test_to_llm_dict_structure(self):
        d = self._make().to_llm_dict()
        assert "data" in d
        assert "tool" in d
        assert "fetched_at" in d
        assert "status" in d
        assert "raw_json" not in d


class TestApiToolResultFromError:
    def test_from_error_status(self):
        r = ApiToolResult.from_error(tool="web.api", error="timeout")
        assert r.status == "error"
        assert r.error == "timeout"

    def test_from_error_ok_false(self):
        r = ApiToolResult.from_error(tool="t", error="e")
        assert r.ok is False

    def test_from_error_source_tool(self):
        r = ApiToolResult.from_error(tool="my_tool", error="fail", endpoint="https://x.com")
        assert r.source.tool == "my_tool"
        assert r.source.endpoint == "https://x.com"


class TestApiToolResultFromDict:
    def test_from_dict_roundtrip(self):
        original = ApiToolResult(
            status="ok",
            raw_json={"a": 1},
            normalized={"a": 1},
            source=ApiSource(tool="trading", endpoint="https://api.x.com"),
        )
        d = original.to_dict()
        restored = ApiToolResult.from_dict(d)
        assert restored.status == "ok"
        assert restored.source.tool == "trading"
        assert restored.source.endpoint == "https://api.x.com"
        assert restored.raw_json == {"a": 1}

    def test_from_dict_error(self):
        d = {
            "status": "error",
            "error": "not found",
            "normalized": {},
            "source": {"tool": "t", "endpoint": "", "timestamp": ""},
        }
        r = ApiToolResult.from_dict(d)
        assert r.status == "error"
        assert r.error == "not found"
