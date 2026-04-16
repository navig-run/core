"""Tests for session-correlated logging extensions in navig.core.logging.

Covers:
  - set_session_context() / clear_session_context()
  - _install_session_record_factory() idempotency
  - session_tag injection on log records
  - _ComponentFilter
"""

from __future__ import annotations

import logging
import threading

import pytest

from navig.core.logging import (
    COMPONENT_PREFIXES,
    _ComponentFilter,
    _install_session_record_factory,
    _session_context,
    clear_session_context,
    get_logger,
    set_session_context,
)


# ---------------------------------------------------------------------------
# Session context helpers
# ---------------------------------------------------------------------------

class TestSessionContext:
    def teardown_method(self):
        # Always clean up so tests don't bleed state.
        clear_session_context()

    def test_set_then_clear(self):
        set_session_context("abc123")
        assert getattr(_session_context, "session_id", None) == "abc123"
        clear_session_context()
        assert getattr(_session_context, "session_id", None) is None

    def test_set_none(self):
        set_session_context("some_id")
        set_session_context(None)
        assert getattr(_session_context, "session_id", None) is None

    def test_thread_isolation(self):
        """Each thread should have its own session context."""
        set_session_context("main_thread")

        results: dict[str, str | None] = {}

        def worker():
            results["before"] = getattr(_session_context, "session_id", None)
            set_session_context("worker_thread")
            results["after"] = getattr(_session_context, "session_id", None)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        # Worker thread isolation: before = None, after = "worker_thread"
        assert results["before"] is None
        assert results["after"] == "worker_thread"
        # Main thread untouched
        assert getattr(_session_context, "session_id", None) == "main_thread"


# ---------------------------------------------------------------------------
# Record factory — session_tag injection
# ---------------------------------------------------------------------------

class TestSessionRecordFactory:
    def teardown_method(self):
        clear_session_context()

    def _capture_records(self, logger_name: str) -> list[logging.LogRecord]:
        """Attach a list-backed handler and return the list."""
        captured: list[logging.LogRecord] = []

        class _ListHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:  # noqa: A003
                captured.append(record)

        h = _ListHandler()
        log = logging.getLogger(logger_name)
        log.addHandler(h)
        log.setLevel(logging.DEBUG)
        return captured, h, log

    def test_session_tag_present_when_context_set(self):
        set_session_context("SESS01")
        captured, h, log = self._capture_records("navig.test.session_tag_test")
        try:
            log.info("hello")
        finally:
            log.removeHandler(h)
        assert len(captured) == 1
        assert hasattr(captured[0], "session_tag")
        assert "SESS01" in captured[0].session_tag

    def test_session_tag_empty_when_no_context(self):
        clear_session_context()
        captured, h, log = self._capture_records("navig.test.session_tag_empty")
        try:
            log.info("hello")
        finally:
            log.removeHandler(h)
        assert len(captured) == 1
        assert getattr(captured[0], "session_tag", "") == ""

    def test_install_is_idempotent(self):
        """Calling _install_session_record_factory multiple times must not
        double-wrap the factory chain."""
        _install_session_record_factory()
        _install_session_record_factory()

        factory = logging.getLogRecordFactory()
        assert getattr(factory, "_navig_session_injector", False) is True

        # Emit a real log record and verify session_tag appears exactly once
        set_session_context("IDEM42")
        captured, h, log = self._capture_records("navig.test.idempotent")
        try:
            log.info("idempotent test")
        finally:
            log.removeHandler(h)
        assert len(captured) == 1
        tag = getattr(captured[0], "session_tag", "")
        assert tag.count("IDEM42") == 1


# ---------------------------------------------------------------------------
# _ComponentFilter
# ---------------------------------------------------------------------------

class TestComponentFilter:
    def _make_record(self, name: str) -> logging.LogRecord:
        return logging.LogRecord(
            name=name,
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="msg",
            args=(),
            exc_info=None,
        )

    def test_passes_matching_prefix(self):
        flt = _ComponentFilter(("navig.gateway",))
        assert flt.filter(self._make_record("navig.gateway.channels.telegram")) is True

    def test_blocks_non_matching_prefix(self):
        flt = _ComponentFilter(("navig.gateway",))
        assert flt.filter(self._make_record("navig.commands.run")) is False

    def test_multiple_prefixes(self):
        flt = _ComponentFilter(("navig.ai", "navig.llm"))
        assert flt.filter(self._make_record("navig.ai.router")) is True
        assert flt.filter(self._make_record("navig.llm.generate")) is True
        assert flt.filter(self._make_record("navig.commands.run")) is False

    def test_component_prefixes_dict_populated(self):
        assert "gateway" in COMPONENT_PREFIXES
        assert "commands" in COMPONENT_PREFIXES
        assert "memory" in COMPONENT_PREFIXES


# ---------------------------------------------------------------------------
# get_logger returns StructuredLogger
# ---------------------------------------------------------------------------

def test_get_logger_returns_structured_logger():
    from navig.core.logging import StructuredLogger

    logger = get_logger("test_session_logging")
    assert isinstance(logger, StructuredLogger)
