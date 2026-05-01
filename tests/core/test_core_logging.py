"""
Batch 85 — navig/core/logging.py
Tests for set_session_context, clear_session_context, get_logger, StructuredLogger.
"""
import json
import logging

import pytest

from navig.core.logging import (
    COMPONENT_PREFIXES,
    LOG_FORMAT,
    StructuredLogger,
    _ComponentFilter,
    clear_session_context,
    get_logger,
    set_session_context,
)


# ---------------------------------------------------------------------------
# Session context helpers
# ---------------------------------------------------------------------------


class TestSessionContext:
    def teardown_method(self):
        clear_session_context()

    def test_set_and_clear_no_crash(self):
        set_session_context("test-session-123")
        clear_session_context()

    def test_set_accepts_none(self):
        set_session_context(None)  # Should not raise


# ---------------------------------------------------------------------------
# COMPONENT_PREFIXES
# ---------------------------------------------------------------------------


class TestComponentPrefixes:
    def test_is_dict(self):
        assert isinstance(COMPONENT_PREFIXES, dict)

    def test_gateway_present(self):
        assert "gateway" in COMPONENT_PREFIXES

    def test_ai_present(self):
        assert "ai" in COMPONENT_PREFIXES

    def test_core_present(self):
        assert "core" in COMPONENT_PREFIXES

    def test_all_values_are_tuples(self):
        for key, val in COMPONENT_PREFIXES.items():
            assert isinstance(val, tuple), f"Expected tuple for '{key}', got {type(val)}"

    def test_all_prefixes_are_strings(self):
        for key, prefixes in COMPONENT_PREFIXES.items():
            for p in prefixes:
                assert isinstance(p, str)


# ---------------------------------------------------------------------------
# LOG_FORMAT constant
# ---------------------------------------------------------------------------


class TestLogFormat:
    def test_is_string(self):
        assert isinstance(LOG_FORMAT, str)

    def test_contains_name(self):
        assert "%(name)s" in LOG_FORMAT

    def test_contains_levelname(self):
        assert "%(levelname)s" in LOG_FORMAT


# ---------------------------------------------------------------------------
# _ComponentFilter
# ---------------------------------------------------------------------------


class TestComponentFilter:
    def _make_record(self, logger_name: str) -> logging.LogRecord:
        return logging.LogRecord(
            name=logger_name,
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )

    def test_matching_prefix_passes(self):
        f = _ComponentFilter(("navig.core",))
        record = self._make_record("navig.core.config")
        assert f.filter(record) is True

    def test_non_matching_prefix_blocked(self):
        f = _ComponentFilter(("navig.core",))
        record = self._make_record("navig.gateway.stuff")
        assert f.filter(record) is False

    def test_multiple_prefixes(self):
        f = _ComponentFilter(("navig.ai", "navig.llm"))
        assert f.filter(self._make_record("navig.ai.router")) is True
        assert f.filter(self._make_record("navig.llm.generate")) is True
        assert f.filter(self._make_record("navig.core.other")) is False

    def test_exact_prefix_match(self):
        f = _ComponentFilter(("navig.platform",))
        assert f.filter(self._make_record("navig.platform")) is True


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------


class TestGetLogger:
    def test_returns_structured_logger(self):
        logger = get_logger("test_subsystem_batch85")
        assert isinstance(logger, StructuredLogger)

    def test_name_has_navig_prefix(self):
        logger = get_logger("batch85_check")
        assert logger.name.startswith("navig.")

    def test_name_contains_subsystem(self):
        logger = get_logger("batch85_sub")
        assert "batch85_sub" in logger.name

    def test_same_subsystem_returns_same_instance(self):
        a = get_logger("batch85_dedup")
        b = get_logger("batch85_dedup")
        assert a is b

    def test_default_subsystem_core(self):
        logger = get_logger()
        assert "core" in logger.name


# ---------------------------------------------------------------------------
# StructuredLogger.structured()
# ---------------------------------------------------------------------------


class TestStructuredLogger:
    def _capture_structured(self, logger: StructuredLogger, level: int, event: str, **kwargs):
        """Capture log records emitted by logger.structured()."""
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Capture()
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.structured(level, event, **kwargs)
        finally:
            logger.removeHandler(handler)
        return records

    def test_structured_emits_record(self):
        logger = get_logger("batch85_struct_a")
        records = self._capture_structured(logger, logging.INFO, "my_event")
        assert len(records) == 1

    def test_structured_json_parseable(self):
        logger = get_logger("batch85_struct_b")
        records = self._capture_structured(logger, logging.INFO, "test_event", count=5)
        assert len(records) == 1
        data = json.loads(records[0].getMessage())
        assert data["event"] == "test_event"

    def test_structured_kwargs_included(self):
        logger = get_logger("batch85_struct_c")
        records = self._capture_structured(logger, logging.INFO, "ev", rows=42, host="prod")
        data = json.loads(records[0].getMessage())
        assert data["rows"] == 42
        assert data["host"] == "prod"

    def test_structured_subsystem_field(self):
        logger = get_logger("batch85_struct_d")
        records = self._capture_structured(logger, logging.DEBUG, "ev2")
        data = json.loads(records[0].getMessage())
        assert "subsystem" in data

    def test_structured_string_kwargs_redacted_if_sensitive(self):
        logger = get_logger("batch85_struct_e")
        records = self._capture_structured(
            logger, logging.INFO, "login",
            token="sk-" + "x" * 20,  # looks like an OpenAI key
        )
        data = json.loads(records[0].getMessage())
        # The token value should be redacted
        assert "x" * 20 not in data["token"]

    def test_log_message_redacted(self):
        """StructuredLogger._log() should redact sensitive data in messages."""
        logger = get_logger("batch85_redact")
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Capture()
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("connecting with token sk-" + "y" * 25)
        finally:
            logger.removeHandler(handler)

        assert len(records) >= 1
        # The formatted message should not contain the raw secret
        msg = records[0].getMessage()
        assert "y" * 25 not in msg
