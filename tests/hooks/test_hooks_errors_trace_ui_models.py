"""
Batch 44 — navig/hooks/events.py + navig/connectors/errors.py
             + navig/routing/trace.py + navig/ui/models.py
Pure-logic and I/O-mocked tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock

import pytest

# ─────────────────────────────────────────────────────────────
# navig.hooks.events
# ─────────────────────────────────────────────────────────────

from navig.hooks.events import HookEvent, HookContext, HookResult


class TestHookEvent:
    def test_is_str_enum(self):
        assert HookEvent.PRE_TOOL_USE.value == "PreToolUse"

    def test_post_tool_use_value(self):
        assert HookEvent.POST_TOOL_USE.value == "PostToolUse"

    def test_session_start_value(self):
        assert HookEvent.SESSION_START.value == "SessionStart"

    def test_notification_value(self):
        assert HookEvent.NOTIFICATION.value == "Notification"

    def test_permission_denied_value(self):
        assert HookEvent.PERMISSION_DENIED.value == "PermissionDenied"


class TestHookContext:
    def _make(self, **kwargs):
        defaults = dict(event=HookEvent.PRE_TOOL_USE, tool_name="bash_exec")
        defaults.update(kwargs)
        return HookContext(**defaults)

    def test_to_json_contains_event_value(self):
        ctx = self._make()
        payload = json.loads(ctx.to_json())
        assert payload["event"] == "PreToolUse"

    def test_to_json_contains_tool_name(self):
        ctx = self._make(tool_name="read_file")
        payload = json.loads(ctx.to_json())
        assert payload["tool_name"] == "read_file"

    def test_tool_result_omitted_when_none(self):
        ctx = self._make()
        payload = json.loads(ctx.to_json())
        assert "tool_result" not in payload

    def test_tool_result_included_when_set(self):
        ctx = self._make(tool_result={"exit_code": 0})
        payload = json.loads(ctx.to_json())
        assert "tool_result" in payload

    def test_tool_error_omitted_when_none(self):
        ctx = self._make()
        payload = json.loads(ctx.to_json())
        assert "tool_error" not in payload

    def test_tool_error_included_when_set(self):
        ctx = self._make(tool_error="some error")
        payload = json.loads(ctx.to_json())
        assert payload["tool_error"] == "some error"

    def test_metadata_omitted_when_empty(self):
        ctx = self._make()
        payload = json.loads(ctx.to_json())
        assert "metadata" not in payload

    def test_metadata_included_when_set(self):
        ctx = self._make(metadata={"key": "value"})
        payload = json.loads(ctx.to_json())
        assert payload["metadata"] == {"key": "value"}

    def test_to_json_returns_valid_json_string(self):
        ctx = self._make()
        result = ctx.to_json()
        assert isinstance(result, str)
        json.loads(result)  # should not raise

    def test_session_and_turn_ids_in_payload(self):
        ctx = self._make(session_id="s1", turn_id="t1")
        payload = json.loads(ctx.to_json())
        assert payload["session_id"] == "s1"
        assert payload["turn_id"] == "t1"


class TestHookResult:
    def test_defaults(self):
        r = HookResult()
        assert r.block is False
        assert r.message == ""
        assert r.executed is False
        assert r.retry is False

    def test_block_true(self):
        r = HookResult(block=True, message="blocked")
        assert r.block is True
        assert r.message == "blocked"


# ─────────────────────────────────────────────────────────────
# navig.connectors.errors
# ─────────────────────────────────────────────────────────────

from navig.connectors.errors import (
    ConnectorError,
    ConnectorAuthError,
    ConnectorNotFoundError,
    ConnectorDegradedError,
    ConnectorAPIError,
    ConnectorRateLimitError,
)


class TestConnectorError:
    def test_is_exception(self):
        assert issubclass(ConnectorError, Exception)

    def test_message_includes_connector_id(self):
        err = ConnectorError("my_connector", "something failed")
        assert "my_connector" in str(err)
        assert "something failed" in str(err)

    def test_connector_id_attribute(self):
        err = ConnectorError("my_conn", "oops")
        assert err.connector_id == "my_conn"


class TestConnectorAuthError:
    def test_subclass_of_connector_error(self):
        assert issubclass(ConnectorAuthError, ConnectorError)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(ConnectorError):
            raise ConnectorAuthError("github", "token expired")


class TestConnectorNotFoundError:
    def test_subclass_of_connector_error(self):
        assert issubclass(ConnectorNotFoundError, ConnectorError)

    def test_default_message_contains_connector_id(self):
        err = ConnectorNotFoundError("slack")
        assert "slack" in str(err)

    def test_custom_message(self):
        err = ConnectorNotFoundError("slack", "not found in vault")
        assert "not found in vault" in str(err)


class TestConnectorDegradedError:
    def test_subclass_of_connector_error(self):
        assert issubclass(ConnectorDegradedError, ConnectorError)

    def test_default_message_mentions_circuit_breaker(self):
        err = ConnectorDegradedError("redis")
        assert "circuit breaker" in str(err).lower() or "degraded" in str(err).lower()


class TestConnectorAPIError:
    def test_subclass_of_connector_error(self):
        assert issubclass(ConnectorAPIError, ConnectorError)

    def test_status_code_attribute(self):
        err = ConnectorAPIError("api", 404, "Not Found")
        assert err.status_code == 404

    def test_detail_in_message(self):
        err = ConnectorAPIError("api", 500, "Internal error")
        assert "Internal error" in str(err)

    def test_no_detail_message(self):
        err = ConnectorAPIError("api", 500)
        assert "500" in str(err)


class TestConnectorRateLimitError:
    def test_subclass_of_api_error(self):
        assert issubclass(ConnectorRateLimitError, ConnectorAPIError)

    def test_status_code_is_429(self):
        err = ConnectorRateLimitError("api")
        assert err.status_code == 429

    def test_retry_after_attribute(self):
        err = ConnectorRateLimitError("api", retry_after=30.0)
        assert err.retry_after == 30.0

    def test_retry_after_none(self):
        err = ConnectorRateLimitError("api")
        assert err.retry_after is None


# ─────────────────────────────────────────────────────────────
# navig.routing.trace
# ─────────────────────────────────────────────────────────────

from navig.routing.trace import RouteTrace, log_trace, recent_traces


class TestRouteTrace:
    def test_defaults(self):
        t = RouteTrace()
        assert t.trace_id == ""
        assert t.timestamp == 0.0
        assert t.input_tokens == 0
        assert t.output_tokens == 0

    def test_to_dict_returns_dict(self):
        t = RouteTrace(trace_id="abc", provider="anthropic", model="claude-3")
        d = t.to_dict()
        assert isinstance(d, dict)
        assert d["trace_id"] == "abc"
        assert d["provider"] == "anthropic"
        assert d["model"] == "claude-3"

    def test_to_dict_includes_lists(self):
        t = RouteTrace(reasons=["high_confidence"], tools_used=["bash_exec"])
        d = t.to_dict()
        assert d["reasons"] == ["high_confidence"]
        assert d["tools_used"] == ["bash_exec"]

    def test_custom_values_set(self):
        t = RouteTrace(input_tokens=500, output_tokens=200, latency_ms=150)
        assert t.input_tokens == 500
        assert t.output_tokens == 200
        assert t.latency_ms == 150


class TestLogTrace:
    def test_writes_json_line(self, tmp_path):
        trace = RouteTrace(trace_id="x1", provider="openai")
        fake_path = tmp_path / "traces.jsonl"
        with patch("navig.routing.trace.TRACE_LOG_PATH", fake_path):
            log_trace(trace)
        line = fake_path.read_text(encoding="utf-8").strip()
        data = json.loads(line)
        assert data["trace_id"] == "x1"

    def test_does_not_raise_on_write_failure(self):
        trace = RouteTrace(trace_id="x2")
        with patch("builtins.open", side_effect=OSError("disk full")), \
             patch("navig.routing.trace.TRACE_LOG_PATH") as mock_path:
            mock_path.parent.mkdir.return_value = None
            log_trace(trace)  # must not raise


class TestRecentTraces:
    def test_returns_empty_when_file_not_exists(self, tmp_path):
        fake_path = tmp_path / "nonexistent.jsonl"
        with patch("navig.routing.trace.TRACE_LOG_PATH", fake_path):
            result = recent_traces()
        assert result == []

    def test_returns_parsed_traces(self, tmp_path):
        line1 = json.dumps({"trace_id": "a"})
        line2 = json.dumps({"trace_id": "b"})
        fake_path = tmp_path / "traces.jsonl"
        fake_path.write_text(f"{line1}\n{line2}\n", encoding="utf-8")
        with patch("navig.routing.trace.TRACE_LOG_PATH", fake_path):
            result = recent_traces(limit=10)
        assert len(result) == 2
        assert result[0]["trace_id"] == "a"

    def test_limit_applied(self, tmp_path):
        lines = "\n".join(json.dumps({"trace_id": str(i)}) for i in range(10))
        fake_path = tmp_path / "traces.jsonl"
        fake_path.write_text(lines + "\n", encoding="utf-8")
        with patch("navig.routing.trace.TRACE_LOG_PATH", fake_path):
            result = recent_traces(limit=3)
        assert len(result) <= 3


# ─────────────────────────────────────────────────────────────
# navig.ui.models
# ─────────────────────────────────────────────────────────────

from navig.ui.models import (
    StatusChip,
    Metric,
    CauseScore,
    Event,
    ActionItem,
    DiffLine,
    DiffPreview,
    SummaryResult,
)


class TestStatusChip:
    def test_required_fields(self):
        chip = StatusChip(icon="✓", icon_safe="OK", label="Status")
        assert chip.icon == "✓"
        assert chip.icon_safe == "OK"
        assert chip.label == "Status"

    def test_defaults(self):
        chip = StatusChip(icon="!", icon_safe="!", label="Alert")
        assert chip.value is None
        assert chip.color == "white"


class TestMetric:
    def test_fields(self):
        m = Metric(label="CPU", value="72%", bar_fill=0.72)
        assert m.label == "CPU"
        assert m.value == "72%"
        assert m.bar_fill == pytest.approx(0.72)

    def test_defaults(self):
        m = Metric(label="MEM", value="50%", bar_fill=0.5)
        assert m.sparkline is None
        assert m.color == "cyan"


class TestCauseScore:
    def test_fields(self):
        cs = CauseScore(confidence=85, description="High CPU")
        assert cs.confidence == 85
        assert cs.description == "High CPU"

    def test_default_severity(self):
        cs = CauseScore(confidence=50, description="test")
        assert cs.severity == "info"

    def test_custom_severity(self):
        cs = CauseScore(confidence=90, description="critical issue", severity="critical")
        assert cs.severity == "critical"


class TestEvent:
    def test_fields(self):
        e = Event(timestamp="10:00", icon="🔔", label="Alert", detail="Disk full")
        assert e.timestamp == "10:00"
        assert e.label == "Alert"

    def test_default_color(self):
        e = Event(timestamp="", icon="", label="", detail="")
        assert e.color == "white"


class TestActionItem:
    def test_fields(self):
        a = ActionItem(index=1, description="Restart service")
        assert a.index == 1
        assert a.description == "Restart service"

    def test_defaults(self):
        a = ActionItem(index=2, description="Check logs")
        assert a.estimated_value is None
        assert a.risk == "low"


class TestDiffLine:
    def test_add_op(self):
        d = DiffLine(op="add", content="+ new line")
        assert d.op == "add"

    def test_remove_op(self):
        d = DiffLine(op="remove", content="- old line")
        assert d.op == "remove"

    def test_context_op(self):
        d = DiffLine(op="context", content="  unchanged")
        assert d.op == "context"


class TestDiffPreview:
    def test_fields(self):
        dp = DiffPreview(title="config.yaml")
        assert dp.title == "config.yaml"
        assert dp.lines == []

    def test_with_lines(self):
        lines = [DiffLine(op="add", content="+x"), DiffLine(op="remove", content="-y")]
        dp = DiffPreview(title="f.py", lines=lines)
        assert len(dp.lines) == 2


class TestSummaryResult:
    def test_fields(self):
        sr = SummaryResult(root_cause="memory leak", recommendation="restart", confidence=90)
        assert sr.root_cause == "memory leak"
        assert sr.recommendation == "restart"
        assert sr.confidence == 90

    def test_default_action_prompt(self):
        sr = SummaryResult(root_cause="x", recommendation="y", confidence=50)
        assert sr.action_prompt is None
