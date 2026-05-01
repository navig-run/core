"""Batch 53 — hermetic unit tests.

Modules covered:
- navig._daemon_defaults    (_DAEMON_PORT, _OAUTH_REDIRECT_PORT)
- navig._llm_defaults       (_DEFAULT_TEMPERATURE, _DEFAULT_MAX_TOKENS)
- navig.blackbox.seal       (seal_bundle, is_sealed, unseal)
- navig.connectors.errors   (ConnectorError hierarchy)
- navig.hooks.events        (HookEvent, HookContext, HookResult)
- navig.routing.trace       (RouteTrace, log_trace, recent_traces)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


# ──────────────────────────────────────────────────────────────────────────────
# navig._daemon_defaults
# ──────────────────────────────────────────────────────────────────────────────


class TestDaemonDefaults:
    """Module-level constants for daemon / IPC ports."""

    def test_daemon_port_is_int(self):
        from navig._daemon_defaults import _DAEMON_PORT

        assert isinstance(_DAEMON_PORT, int)

    def test_daemon_port_value(self):
        from navig._daemon_defaults import _DAEMON_PORT

        assert _DAEMON_PORT == 8765

    def test_oauth_redirect_port_is_int(self):
        from navig._daemon_defaults import _OAUTH_REDIRECT_PORT

        assert isinstance(_OAUTH_REDIRECT_PORT, int)

    def test_oauth_redirect_port_value(self):
        from navig._daemon_defaults import _OAUTH_REDIRECT_PORT

        assert _OAUTH_REDIRECT_PORT == 1455

    def test_ports_are_different(self):
        from navig._daemon_defaults import _DAEMON_PORT, _OAUTH_REDIRECT_PORT

        assert _DAEMON_PORT != _OAUTH_REDIRECT_PORT

    def test_ports_are_positive(self):
        from navig._daemon_defaults import _DAEMON_PORT, _OAUTH_REDIRECT_PORT

        assert _DAEMON_PORT > 0
        assert _OAUTH_REDIRECT_PORT > 0


# ──────────────────────────────────────────────────────────────────────────────
# navig._llm_defaults
# ──────────────────────────────────────────────────────────────────────────────


class TestLlmDefaults:
    """Module-level constants for LLM generation parameters."""

    def test_temperature_is_float(self):
        from navig._llm_defaults import _DEFAULT_TEMPERATURE

        assert isinstance(_DEFAULT_TEMPERATURE, float)

    def test_temperature_value(self):
        from navig._llm_defaults import _DEFAULT_TEMPERATURE

        assert _DEFAULT_TEMPERATURE == 0.7

    def test_temperature_in_range(self):
        from navig._llm_defaults import _DEFAULT_TEMPERATURE

        assert 0.0 <= _DEFAULT_TEMPERATURE <= 2.0

    def test_max_tokens_is_int(self):
        from navig._llm_defaults import _DEFAULT_MAX_TOKENS

        assert isinstance(_DEFAULT_MAX_TOKENS, int)

    def test_max_tokens_value(self):
        from navig._llm_defaults import _DEFAULT_MAX_TOKENS

        assert _DEFAULT_MAX_TOKENS == 4096

    def test_max_tokens_positive(self):
        from navig._llm_defaults import _DEFAULT_MAX_TOKENS

        assert _DEFAULT_MAX_TOKENS > 0


# ──────────────────────────────────────────────────────────────────────────────
# navig.blackbox.seal
# ──────────────────────────────────────────────────────────────────────────────


class TestIsSealed:
    """is_sealed — checks for SEALED marker file in blackbox_dir."""

    def _import(self):
        from navig.blackbox.seal import is_sealed

        return is_sealed

    def test_not_sealed_when_marker_absent(self, tmp_path):
        is_sealed = self._import()
        assert is_sealed(blackbox_dir=tmp_path) is False

    def test_sealed_when_marker_present(self, tmp_path):
        is_sealed = self._import()
        (tmp_path / "SEALED").write_text("2024-01-01T00:00:00")
        assert is_sealed(blackbox_dir=tmp_path) is True


class TestUnseal:
    """unseal — removes SEALED marker file."""

    def _import(self):
        from navig.blackbox.seal import unseal

        return unseal

    def test_returns_true_when_marker_removed(self, tmp_path):
        unseal = self._import()
        (tmp_path / "SEALED").write_text("ts")
        result = unseal(blackbox_dir=tmp_path)
        assert result is True

    def test_marker_gone_after_unseal(self, tmp_path):
        unseal = self._import()
        (tmp_path / "SEALED").write_text("ts")
        unseal(blackbox_dir=tmp_path)
        assert not (tmp_path / "SEALED").exists()

    def test_returns_false_when_not_sealed(self, tmp_path):
        unseal = self._import()
        result = unseal(blackbox_dir=tmp_path)
        assert result is False


class TestSealBundle:
    """seal_bundle — marks bundle as sealed and writes marker file."""

    def _make_bundle(self):
        from datetime import datetime, timezone

        from navig.blackbox.types import Bundle

        return Bundle(
            id="b001",
            created_at=datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            navig_version="0.0.0",
            events=[],
            crash_reports=[],
            log_tails={},
            manifest_hash="",
        )

    def test_sets_sealed_true(self, tmp_path):
        from navig.blackbox.seal import seal_bundle

        bundle = self._make_bundle()
        result = seal_bundle(bundle, blackbox_dir=tmp_path)
        assert result.sealed is True

    def test_writes_marker_file(self, tmp_path):
        from navig.blackbox.seal import seal_bundle

        bundle = self._make_bundle()
        seal_bundle(bundle, blackbox_dir=tmp_path)
        assert (tmp_path / "SEALED").exists()

    def test_marker_contains_timestamp(self, tmp_path):
        from navig.blackbox.seal import seal_bundle

        bundle = self._make_bundle()
        seal_bundle(bundle, blackbox_dir=tmp_path)
        content = (tmp_path / "SEALED").read_text()
        assert "2024-06-01" in content

    def test_returns_same_bundle(self, tmp_path):
        from navig.blackbox.seal import seal_bundle

        bundle = self._make_bundle()
        result = seal_bundle(bundle, blackbox_dir=tmp_path)
        assert result is bundle

    def test_creates_dir_if_missing(self, tmp_path):
        from navig.blackbox.seal import seal_bundle

        target = tmp_path / "sub" / "blackbox"
        bundle = self._make_bundle()
        seal_bundle(bundle, blackbox_dir=target)
        assert target.exists()


# ──────────────────────────────────────────────────────────────────────────────
# navig.connectors.errors
# ──────────────────────────────────────────────────────────────────────────────


class TestConnectorError:
    """Base ConnectorError — stores connector_id and formats message."""

    def test_is_exception(self):
        from navig.connectors.errors import ConnectorError

        assert issubclass(ConnectorError, Exception)

    def test_connector_id_stored(self):
        from navig.connectors.errors import ConnectorError

        e = ConnectorError("gmail", "something went wrong")
        assert e.connector_id == "gmail"

    def test_message_includes_connector_id(self):
        from navig.connectors.errors import ConnectorError

        e = ConnectorError("gmail", "timeout")
        assert "gmail" in str(e)
        assert "timeout" in str(e)


class TestConnectorAuthError:
    """ConnectorAuthError — auth failure subtype."""

    def test_is_connector_error(self):
        from navig.connectors.errors import ConnectorAuthError, ConnectorError

        assert issubclass(ConnectorAuthError, ConnectorError)

    def test_can_raise_and_catch(self):
        from navig.connectors.errors import ConnectorAuthError, ConnectorError

        try:
            raise ConnectorAuthError("slack", "token expired")
        except ConnectorError as e:
            assert e.connector_id == "slack"


class TestConnectorNotFoundError:
    """ConnectorNotFoundError — default message when omitted."""

    def test_default_message(self):
        from navig.connectors.errors import ConnectorNotFoundError

        e = ConnectorNotFoundError("stripe")
        assert "stripe" in str(e)
        assert "not registered" in str(e)

    def test_custom_message(self):
        from navig.connectors.errors import ConnectorNotFoundError

        e = ConnectorNotFoundError("stripe", "custom message")
        assert "custom message" in str(e)

    def test_connector_id_stored(self):
        from navig.connectors.errors import ConnectorNotFoundError

        e = ConnectorNotFoundError("stripe")
        assert e.connector_id == "stripe"


class TestConnectorDegradedError:
    """ConnectorDegradedError — circuit breaker open."""

    def test_default_message_mentions_degraded(self):
        from navig.connectors.errors import ConnectorDegradedError

        e = ConnectorDegradedError("redis")
        assert "circuit breaker" in str(e).lower() or "degraded" in str(e).lower()

    def test_custom_message(self):
        from navig.connectors.errors import ConnectorDegradedError

        e = ConnectorDegradedError("redis", "too many failures")
        assert "too many failures" in str(e)


class TestConnectorAPIError:
    """ConnectorAPIError — stores status code and detail."""

    def test_status_code_stored(self):
        from navig.connectors.errors import ConnectorAPIError

        e = ConnectorAPIError("github", 404, "not found")
        assert e.status_code == 404

    def test_detail_stored(self):
        from navig.connectors.errors import ConnectorAPIError

        e = ConnectorAPIError("github", 500, "internal server error")
        assert e.detail == "internal server error"

    def test_status_code_in_message(self):
        from navig.connectors.errors import ConnectorAPIError

        e = ConnectorAPIError("github", 403, "forbidden")
        assert "403" in str(e)

    def test_no_detail(self):
        from navig.connectors.errors import ConnectorAPIError

        e = ConnectorAPIError("github", 500)
        assert e.status_code == 500


class TestConnectorRateLimitError:
    """ConnectorRateLimitError — HTTP 429 with retry_after."""

    def test_is_api_error(self):
        from navig.connectors.errors import ConnectorAPIError, ConnectorRateLimitError

        assert issubclass(ConnectorRateLimitError, ConnectorAPIError)

    def test_status_code_is_429(self):
        from navig.connectors.errors import ConnectorRateLimitError

        e = ConnectorRateLimitError("twitter")
        assert e.status_code == 429

    def test_retry_after_stored(self):
        from navig.connectors.errors import ConnectorRateLimitError

        e = ConnectorRateLimitError("twitter", retry_after=60.0)
        assert e.retry_after == 60.0

    def test_no_retry_after(self):
        from navig.connectors.errors import ConnectorRateLimitError

        e = ConnectorRateLimitError("twitter")
        assert e.retry_after is None


# ──────────────────────────────────────────────────────────────────────────────
# navig.hooks.events
# ──────────────────────────────────────────────────────────────────────────────


class TestHookEvent:
    """HookEvent enum — str values."""

    def test_pre_tool_use_value(self):
        from navig.hooks.events import HookEvent

        assert HookEvent.PRE_TOOL_USE.value == "PreToolUse"

    def test_post_tool_use_value(self):
        from navig.hooks.events import HookEvent

        assert HookEvent.POST_TOOL_USE.value == "PostToolUse"

    def test_session_start_value(self):
        from navig.hooks.events import HookEvent

        assert HookEvent.SESSION_START.value == "SessionStart"

    def test_notification_value(self):
        from navig.hooks.events import HookEvent

        assert HookEvent.NOTIFICATION.value == "Notification"

    def test_is_str_enum(self):
        from navig.hooks.events import HookEvent

        assert isinstance(HookEvent.SESSION_START, str)

    def test_six_events(self):
        from navig.hooks.events import HookEvent

        assert len(HookEvent) == 6


class TestHookContext:
    """HookContext dataclass and to_json serialization."""

    def test_defaults(self):
        from navig.hooks.events import HookContext, HookEvent

        ctx = HookContext(event=HookEvent.SESSION_START)
        assert ctx.tool_name == ""
        assert ctx.tool_input == {}
        assert ctx.tool_result is None
        assert ctx.tool_error is None

    def test_to_json_contains_event(self):
        from navig.hooks.events import HookContext, HookEvent

        ctx = HookContext(event=HookEvent.PRE_TOOL_USE, tool_name="bash", session_id="s1")
        data = json.loads(ctx.to_json())
        assert data["event"] == "PreToolUse"
        assert data["tool_name"] == "bash"
        assert data["session_id"] == "s1"

    def test_to_json_omits_none_tool_result(self):
        from navig.hooks.events import HookContext, HookEvent

        ctx = HookContext(event=HookEvent.POST_TOOL_USE)
        data = json.loads(ctx.to_json())
        assert "tool_result" not in data

    def test_to_json_includes_tool_result_when_set(self):
        from navig.hooks.events import HookContext, HookEvent

        ctx = HookContext(event=HookEvent.POST_TOOL_USE, tool_result={"output": "ok"})
        data = json.loads(ctx.to_json())
        assert data["tool_result"] == {"output": "ok"}

    def test_to_json_includes_tool_error_when_set(self):
        from navig.hooks.events import HookContext, HookEvent

        ctx = HookContext(event=HookEvent.POST_TOOL_USE_FAILURE, tool_error="timeout")
        data = json.loads(ctx.to_json())
        assert data["tool_error"] == "timeout"

    def test_to_json_includes_metadata_when_set(self):
        from navig.hooks.events import HookContext, HookEvent

        ctx = HookContext(event=HookEvent.SESSION_START, metadata={"tier": "big"})
        data = json.loads(ctx.to_json())
        assert data["metadata"]["tier"] == "big"

    def test_to_json_omits_empty_metadata(self):
        from navig.hooks.events import HookContext, HookEvent

        ctx = HookContext(event=HookEvent.SESSION_START)
        data = json.loads(ctx.to_json())
        assert "metadata" not in data


class TestHookResult:
    """HookResult — aggregated result after hook run."""

    def test_defaults(self):
        from navig.hooks.events import HookResult

        r = HookResult()
        assert r.block is False
        assert r.message == ""
        assert r.executed is False
        assert r.retry is False

    def test_block_true(self):
        from navig.hooks.events import HookResult

        r = HookResult(block=True, message="blocked by policy")
        assert r.block is True
        assert r.message == "blocked by policy"

    def test_executed_true(self):
        from navig.hooks.events import HookResult

        r = HookResult(executed=True)
        assert r.executed is True

    def test_retry_true(self):
        from navig.hooks.events import HookResult

        r = HookResult(retry=True)
        assert r.retry is True


# ──────────────────────────────────────────────────────────────────────────────
# navig.routing.trace
# ──────────────────────────────────────────────────────────────────────────────


class TestRouteTrace:
    """RouteTrace dataclass construction and to_dict."""

    def test_defaults(self):
        from navig.routing.trace import RouteTrace

        t = RouteTrace()
        assert t.trace_id == ""
        assert t.input_tokens == 0
        assert t.reasons == []
        assert t.fallbacks_tried == []

    def test_custom_values(self):
        from navig.routing.trace import RouteTrace

        t = RouteTrace(
            trace_id="abc123",
            mode="coder",
            provider="openai",
            model="gpt-4o",
            input_tokens=500,
            output_tokens=200,
        )
        assert t.trace_id == "abc123"
        assert t.provider == "openai"
        assert t.input_tokens == 500

    def test_to_dict_returns_dict(self):
        from navig.routing.trace import RouteTrace

        t = RouteTrace(trace_id="x")
        d = t.to_dict()
        assert isinstance(d, dict)
        assert d["trace_id"] == "x"

    def test_to_dict_includes_all_fields(self):
        from navig.routing.trace import RouteTrace

        t = RouteTrace()
        d = t.to_dict()
        expected_keys = {
            "trace_id", "timestamp", "mode", "confidence", "reasons",
            "provider", "model", "input_tokens", "output_tokens",
            "latency_ms", "audit_result", "tools_used", "entrypoint",
        }
        assert expected_keys <= set(d.keys())

    def test_to_dict_serializable(self):
        from navig.routing.trace import RouteTrace

        t = RouteTrace(trace_id="json-test", reasons=["speed", "cost"])
        result = json.dumps(t.to_dict())
        data = json.loads(result)
        assert data["trace_id"] == "json-test"
        assert "speed" in data["reasons"]


class TestLogTrace:
    """log_trace — appends JSON lines to trace log file."""

    def test_creates_file(self, tmp_path):
        from navig.routing import trace as trace_mod
        from navig.routing.trace import RouteTrace, log_trace

        log_path = tmp_path / "traces.jsonl"
        with patch.object(trace_mod, "TRACE_LOG_PATH", log_path):
            log_trace(RouteTrace(trace_id="t1"))
        assert log_path.exists()

    def test_writes_valid_jsonl(self, tmp_path):
        from navig.routing import trace as trace_mod
        from navig.routing.trace import RouteTrace, log_trace

        log_path = tmp_path / "traces.jsonl"
        with patch.object(trace_mod, "TRACE_LOG_PATH", log_path):
            log_trace(RouteTrace(trace_id="t2", mode="fast"))
        line = log_path.read_text().strip()
        data = json.loads(line)
        assert data["trace_id"] == "t2"

    def test_appends_multiple(self, tmp_path):
        from navig.routing import trace as trace_mod
        from navig.routing.trace import RouteTrace, log_trace

        log_path = tmp_path / "traces.jsonl"
        with patch.object(trace_mod, "TRACE_LOG_PATH", log_path):
            log_trace(RouteTrace(trace_id="a"))
            log_trace(RouteTrace(trace_id="b"))
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2


class TestRecentTraces:
    """recent_traces — reads last N traces from JSONL file."""

    def test_returns_empty_when_file_missing(self, tmp_path):
        from navig.routing import trace as trace_mod
        from navig.routing.trace import recent_traces

        missing = tmp_path / "no_file.jsonl"
        with patch.object(trace_mod, "TRACE_LOG_PATH", missing):
            result = recent_traces()
        assert result == []

    def test_returns_traces_from_file(self, tmp_path):
        from navig.routing import trace as trace_mod
        from navig.routing.trace import RouteTrace, log_trace, recent_traces

        log_path = tmp_path / "traces.jsonl"
        with patch.object(trace_mod, "TRACE_LOG_PATH", log_path):
            log_trace(RouteTrace(trace_id="r1"))
            log_trace(RouteTrace(trace_id="r2"))
            result = recent_traces(limit=10)
        assert len(result) == 2
        assert result[0]["trace_id"] == "r1"

    def test_respects_limit(self, tmp_path):
        from navig.routing import trace as trace_mod
        from navig.routing.trace import RouteTrace, log_trace, recent_traces

        log_path = tmp_path / "traces.jsonl"
        with patch.object(trace_mod, "TRACE_LOG_PATH", log_path):
            for i in range(10):
                log_trace(RouteTrace(trace_id=f"t{i}"))
            result = recent_traces(limit=3)
        assert len(result) == 3
