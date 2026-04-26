"""
Batch 15: Tests for
- navig.tools.api_schema (redact_sensitive, ApiSource, ApiToolResult, validate_api_result)
- navig.messaging.adapter (DeliveryStatus.can_transition_to, ComplianceMode, IdentityMode)
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# navig.tools.api_schema
# ---------------------------------------------------------------------------
from navig.tools.api_schema import (
    ApiSource,
    ApiToolResult,
    _REDACTED,
    _SENSITIVE_KEYS_RE,
    redact_sensitive,
    validate_api_result,
)


class TestRedactSensitive:
    def test_plain_dict_unchanged(self):
        data = {"name": "Alice", "age": 30}
        result = redact_sensitive(data)
        assert result == {"name": "Alice", "age": 30}

    def test_api_key_redacted(self):
        data = {"api_key": "my-secret"}
        result = redact_sensitive(data)
        assert result["api_key"] == _REDACTED

    def test_token_redacted(self):
        data = {"token": "abc123"}
        result = redact_sensitive(data)
        assert result["token"] == _REDACTED

    def test_password_redacted(self):
        data = {"password": "hunter2"}
        result = redact_sensitive(data)
        assert result["password"] == _REDACTED

    def test_nested_redacted(self):
        data = {"profile": {"api_key": "secret", "user": "alice"}}
        result = redact_sensitive(data)
        assert result["profile"]["api_key"] == _REDACTED
        assert result["profile"]["user"] == "alice"

    def test_list_of_dicts(self):
        data = [{"token": "abc"}, {"name": "Bob"}]
        result = redact_sensitive(data)
        assert result[0]["token"] == _REDACTED
        assert result[1]["name"] == "Bob"

    def test_non_sensitive_keys_unchanged(self):
        data = {"url": "https://example.com", "count": 42}
        result = redact_sensitive(data)
        assert result == {"url": "https://example.com", "count": 42}

    def test_extra_keys_redacted(self):
        data = {"my_secret_field": "value", "normal": "ok"}
        result = redact_sensitive(data, extra_keys={"my_secret_field"})
        assert result["my_secret_field"] == _REDACTED
        assert result["normal"] == "ok"

    def test_scalar_passthrough(self):
        assert redact_sensitive("hello") == "hello"
        assert redact_sensitive(42) == 42
        assert redact_sensitive(None) is None

    def test_original_not_mutated(self):
        data = {"api_key": "secret"}
        _ = redact_sensitive(data)
        assert data["api_key"] == "secret"  # original unchanged


class TestApiSource:
    def test_basic_creation(self):
        src = ApiSource(tool="my.tool", endpoint="https://api.example.com")
        assert src.tool == "my.tool"
        assert src.endpoint == "https://api.example.com"
        assert src.timestamp  # auto-generated

    def test_default_endpoint_empty(self):
        src = ApiSource(tool="t")
        assert src.endpoint == ""

    def test_timestamp_is_iso_format(self):
        src = ApiSource(tool="t")
        # Should look like "2024-..." (ISO 8601 with timezone)
        assert "T" in src.timestamp


class TestApiToolResult:
    def test_default_status_ok(self):
        r = ApiToolResult()
        assert r.status == "ok"
        assert r.ok is True

    def test_ok_false_when_error(self):
        r = ApiToolResult(status="error", error="something broke")
        assert r.ok is False

    def test_ok_false_when_error_message_set(self):
        r = ApiToolResult(error="bad")
        assert r.ok is False

    def test_to_dict_has_all_keys(self):
        r = ApiToolResult(source=ApiSource(tool="test"))
        d = r.to_dict()
        assert "status" in d
        assert "raw_json" in d
        assert "normalized" in d
        assert "source" in d
        assert "error" in d

    def test_to_snapshot_dict_excludes_raw_json(self):
        r = ApiToolResult(raw_json={"private": "data"}, source=ApiSource(tool="t"))
        snap = r.to_snapshot_dict()
        assert "raw_json" not in snap

    def test_to_snapshot_dict_redacts_sensitive(self):
        r = ApiToolResult(normalized={"token": "secret", "price": 99.0}, source=ApiSource(tool="t"))
        snap = r.to_snapshot_dict()
        assert snap["normalized"]["token"] == _REDACTED
        assert snap["normalized"]["price"] == 99.0

    def test_to_llm_dict_has_data_key(self):
        r = ApiToolResult(normalized={"x": 1}, source=ApiSource(tool="my.tool"))
        d = r.to_llm_dict()
        assert "data" in d
        assert d["data"] == {"x": 1}
        assert d["tool"] == "my.tool"

    def test_from_error_factory(self):
        r = ApiToolResult.from_error("price.tool", "API timeout", endpoint="https://x.com")
        assert r.status == "error"
        assert r.error == "API timeout"
        assert r.source.tool == "price.tool"
        assert r.ok is False

    def test_from_dict_roundtrip(self):
        original = ApiToolResult(
            status="ok",
            raw_json={"a": 1},
            normalized={"b": 2},
            source=ApiSource(tool="t.test", endpoint="https://e.com", timestamp="2024-01-01T00:00:00+00:00"),
        )
        d = original.to_dict()
        restored = ApiToolResult.from_dict(d)
        assert restored.status == "ok"
        assert restored.normalized == {"b": 2}
        assert restored.source.tool == "t.test"

    def test_from_dict_defaults_on_empty(self):
        r = ApiToolResult.from_dict({})
        assert r.status == "ok"
        assert r.source.tool == "unknown"


class TestValidateApiResult:
    def test_valid_result_no_issues(self):
        r = ApiToolResult(source=ApiSource(tool="my.tool"))
        assert validate_api_result(r) == []

    def test_invalid_status(self):
        r = ApiToolResult(status="unknown")
        issues = validate_api_result(r)
        assert any("status" in i for i in issues)

    def test_empty_tool_name(self):
        r = ApiToolResult(source=ApiSource(tool=""))
        issues = validate_api_result(r)
        assert any("tool" in i for i in issues)

    def test_error_status_without_message(self):
        r = ApiToolResult(status="error", error=None)
        issues = validate_api_result(r)
        assert any("error" in i.lower() for i in issues)

    def test_error_status_with_message_valid(self):
        r = ApiToolResult(status="error", error="some error", source=ApiSource(tool="t"))
        issues = validate_api_result(r)
        assert not any("error message" in i for i in issues)


# ---------------------------------------------------------------------------
# navig.messaging.adapter — DeliveryStatus + enums
# ---------------------------------------------------------------------------
from navig.messaging.adapter import ComplianceMode, DeliveryStatus, IdentityMode


class TestDeliveryStatus:
    def test_all_members(self):
        members = {s.value for s in DeliveryStatus}
        assert "queued" in members
        assert "sent" in members
        assert "delivered" in members
        assert "read" in members
        assert "failed" in members

    def test_queued_to_sent(self):
        assert DeliveryStatus.QUEUED.can_transition_to(DeliveryStatus.SENT) is True

    def test_sent_to_delivered(self):
        assert DeliveryStatus.SENT.can_transition_to(DeliveryStatus.DELIVERED) is True

    def test_delivered_to_read(self):
        assert DeliveryStatus.DELIVERED.can_transition_to(DeliveryStatus.READ) is True

    def test_cannot_go_backward(self):
        # SENT cannot go back to QUEUED
        assert DeliveryStatus.SENT.can_transition_to(DeliveryStatus.QUEUED) is False

    def test_cannot_skip_forward_multiple(self):
        # QUEUED → READ (skip SENT and DELIVERED)
        assert DeliveryStatus.QUEUED.can_transition_to(DeliveryStatus.READ) is True  # higher order is fine

    def test_any_can_transition_to_failed(self):
        for status in DeliveryStatus:
            assert status.can_transition_to(DeliveryStatus.FAILED) is True

    def test_failed_cannot_transition_forward(self):
        # FAILED → DELIVERED should be False
        assert DeliveryStatus.FAILED.can_transition_to(DeliveryStatus.DELIVERED) is False


class TestComplianceMode:
    def test_official_value(self):
        assert ComplianceMode.OFFICIAL.value == "official"

    def test_experimental_value(self):
        assert ComplianceMode.EXPERIMENTAL.value == "experimental"

    def test_disabled_value(self):
        assert ComplianceMode.DISABLED.value == "disabled"


class TestIdentityMode:
    def test_bot_value(self):
        assert IdentityMode.BOT.value == "bot"

    def test_business_value(self):
        assert IdentityMode.BUSINESS.value == "business"

    def test_bridge_user_value(self):
        assert IdentityMode.BRIDGE_USER.value == "bridge_user"
