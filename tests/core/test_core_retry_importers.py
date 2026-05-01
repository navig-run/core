"""
Batch 43 — navig/core/retry_utils.py + navig/importers/models.py + navig/importers/base.py
Pure-logic and I/O-mocked tests.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

# ─────────────────────────────────────────────────────────────
# navig.core.retry_utils
# ─────────────────────────────────────────────────────────────

from navig.core.retry_utils import (
    jittered_backoff,
    RetryConfig,
    async_retry,
    retry_sync,
)


class TestJitteredBackoff:
    def test_attempt_0_near_base_delay(self):
        # attempt=0 → exponent=max(0,0-1)=0 → delay = base * 2^0 = base
        result = jittered_backoff(0, base_delay=5.0, max_delay=120.0, jitter_ratio=0.0)
        assert result == pytest.approx(5.0)

    def test_attempt_1_doubles(self):
        # attempt=1 → exponent=0 → delay=base * 1 = 5 (counter-intuitive but correct per implementation)
        # Actually: exponent = max(0, attempt-1) = max(0,0)=0 → base_delay * 2^0 = base_delay
        # attempt=2 → exponent=1 → base_delay * 2
        result = jittered_backoff(2, base_delay=5.0, max_delay=120.0, jitter_ratio=0.0)
        assert result == pytest.approx(10.0)

    def test_capped_at_max_delay(self):
        result = jittered_backoff(20, base_delay=5.0, max_delay=30.0, jitter_ratio=0.0)
        assert result == pytest.approx(30.0)

    def test_jitter_adds_positive_value(self):
        # With jitter_ratio > 0, result should be >= base delay
        results = [jittered_backoff(0, base_delay=10.0, max_delay=120.0, jitter_ratio=0.5) for _ in range(10)]
        assert all(r >= 10.0 for r in results)

    def test_jitter_varies_results(self):
        # With jitter, repeated calls should produce different values
        results = {jittered_backoff(1, base_delay=5.0, max_delay=120.0, jitter_ratio=0.9) for _ in range(20)}
        assert len(results) > 1

    def test_returns_float(self):
        assert isinstance(jittered_backoff(0), float)

    def test_custom_base_delay(self):
        result = jittered_backoff(0, base_delay=2.0, max_delay=120.0, jitter_ratio=0.0)
        assert result == pytest.approx(2.0)


class TestRetryConfig:
    def test_defaults(self):
        cfg = RetryConfig()
        assert cfg.max_attempts == 3
        assert cfg.base_delay == 5.0
        assert cfg.max_delay == 120.0
        assert cfg.jitter_ratio == 0.5
        assert cfg.reraise_last is True

    def test_retryable_exceptions_default_is_exception(self):
        cfg = RetryConfig()
        assert Exception in cfg.retryable_exceptions

    def test_custom_values(self):
        cfg = RetryConfig(max_attempts=5, base_delay=1.0, reraise_last=False)
        assert cfg.max_attempts == 5
        assert cfg.base_delay == 1.0
        assert cfg.reraise_last is False


class TestAsyncRetry:
    def test_succeeds_on_first_attempt(self):
        @async_retry(RetryConfig(max_attempts=3))
        async def always_ok():
            return 42

        result = asyncio.run(always_ok())
        assert result == 42

    def test_retries_and_succeeds_on_third(self):
        calls = []

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.001, max_delay=0.001))
        async def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("not yet")
            return "ok"

        with patch("asyncio.sleep", AsyncMock()):
            result = asyncio.run(flaky())
        assert result == "ok"
        assert len(calls) == 3

    def test_reraises_after_exhaustion(self):
        @async_retry(RetryConfig(max_attempts=2, base_delay=0.001, max_delay=0.001))
        async def always_fails():
            raise RuntimeError("boom")

        with patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(RuntimeError, match="boom"):
                asyncio.run(always_fails())

    def test_no_reraise_returns_none(self):
        @async_retry(RetryConfig(max_attempts=2, base_delay=0.001, max_delay=0.001, reraise_last=False))
        async def always_fails():
            raise RuntimeError("boom")

        with patch("asyncio.sleep", AsyncMock()):
            result = asyncio.run(always_fails())
        assert result is None

    def test_on_retry_callback_called(self):
        retries = []

        def on_r(attempt, exc, delay):
            retries.append((attempt, type(exc).__name__))

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.001, max_delay=0.001), on_retry=on_r)
        async def flaky():
            raise ValueError("oops")

        with patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(ValueError):
                asyncio.run(flaky())

        assert len(retries) > 0

    def test_preserves_function_name(self):
        @async_retry()
        async def my_function():
            return 1

        assert my_function.__name__ == "my_function"


class TestRetrySync:
    def test_success_on_first_call(self):
        result = retry_sync(lambda: 99, config=RetryConfig(max_attempts=3))
        assert result == 99

    def test_retries_on_failure(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise IOError("not yet")
            return "done"

        with patch("time.sleep"):
            result = retry_sync(flaky, config=RetryConfig(max_attempts=3, base_delay=0.001, max_delay=0.001))
        assert result == "done"
        assert len(calls) == 3

    def test_reraises_after_exhaustion(self):
        def always_fails():
            raise ValueError("fail")

        with patch("time.sleep"):
            with pytest.raises(ValueError, match="fail"):
                retry_sync(always_fails, config=RetryConfig(max_attempts=2, base_delay=0.001))

    def test_no_reraise_returns_none(self):
        def always_fails():
            raise IOError("fail")

        with patch("time.sleep"):
            result = retry_sync(always_fails, config=RetryConfig(max_attempts=2, reraise_last=False, base_delay=0.001))
        assert result is None

    def test_passes_args_to_function(self):
        def add(a, b):
            return a + b

        result = retry_sync(add, 3, 4, config=RetryConfig(max_attempts=1))
        assert result == 7


# ─────────────────────────────────────────────────────────────
# navig.importers.models
# ─────────────────────────────────────────────────────────────

from navig.importers.models import ImportedItem, validate_item_dict


class TestImportedItem:
    def _make(self, **kwargs):
        defaults = dict(source="ssh_config", type="server", label="prod", value="10.0.0.1", meta={})
        defaults.update(kwargs)
        return ImportedItem(**defaults)

    def test_valid_item_validates_without_error(self):
        self._make().validate()

    def test_to_dict_returns_all_fields(self):
        item = self._make()
        d = item.to_dict()
        assert d["source"] == "ssh_config"
        assert d["type"] == "server"
        assert d["label"] == "prod"
        assert d["value"] == "10.0.0.1"
        assert d["meta"] == {}

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="type must be one of"):
            self._make(type="unknown_type").validate()

    def test_empty_source_raises(self):
        with pytest.raises(ValueError, match="source"):
            self._make(source="").validate()

    def test_empty_label_raises(self):
        with pytest.raises(ValueError, match="label"):
            self._make(label="").validate()

    def test_non_string_value_raises(self):
        with pytest.raises((ValueError, TypeError)):
            ImportedItem(source="s", type="server", label="l", value=123).validate()  # type: ignore

    def test_meta_defaults_to_empty_dict(self):
        item = ImportedItem(source="s", type="server", label="l", value="v")
        assert item.meta == {}

    def test_valid_types_accepted(self):
        for t in ("server", "contact", "bookmark"):
            self._make(type=t).validate()  # no exception


class TestValidateItemDict:
    def _valid(self):
        return {"source": "s", "type": "server", "label": "l", "value": "v", "meta": {}}

    def test_valid_dict_returns_normalized(self):
        result = validate_item_dict(self._valid())
        assert result["source"] == "s"
        assert result["type"] == "server"

    def test_missing_field_raises(self):
        d = self._valid()
        del d["label"]
        with pytest.raises(ValueError, match="missing"):
            validate_item_dict(d)

    def test_non_dict_meta_coerced_to_empty(self):
        d = self._valid()
        d["meta"] = None  # invalid but tolerated
        result = validate_item_dict(d)
        assert result["meta"] == {}


# ─────────────────────────────────────────────────────────────
# navig.importers.base
# ─────────────────────────────────────────────────────────────

from navig.importers.base import BaseImporter
from navig.importers.models import ImportedItem


class ConcreteImporter(BaseImporter):
    SOURCE_NAME = "test_source"
    ITEM_TYPE = "server"
    _detect_result = True
    _default = "/tmp/known_path.txt"
    _items: list[ImportedItem] = []

    def detect(self) -> bool:
        return self._detect_result

    def parse(self, path: str) -> list[ImportedItem]:
        return self._items

    def default_path(self) -> str | None:
        return self._default


class TestBaseImporterRun:
    def test_returns_items_when_path_exists(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("content")
        imp = ConcreteImporter()
        imp._default = str(f)
        imp._items = [ImportedItem(source="s", type="server", label="l", value="v")]
        result = imp.run()
        assert len(result) == 1

    def test_returns_empty_when_path_not_found(self):
        imp = ConcreteImporter()
        imp._default = "/nonexistent/path/file.txt"
        result = imp.run()
        assert result == []

    def test_returns_empty_when_default_path_is_none(self):
        imp = ConcreteImporter()
        imp._default = None
        result = imp.run()
        assert result == []

    def test_detect_false_skips_parse_for_default_path(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("content")
        imp = ConcreteImporter()
        imp._default = str(f)
        imp._detect_result = False
        result = imp.run()
        assert result == []

    def test_explicit_path_bypasses_detect(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("content")
        imp = ConcreteImporter()
        imp._detect_result = False
        imp._items = [ImportedItem(source="s", type="server", label="l", value="v")]
        result = imp.run(path=str(f))
        # Explicit path skips detect check
        assert len(result) == 1
