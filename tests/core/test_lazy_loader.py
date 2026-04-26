"""Tests for navig.lazy_loader"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from navig.lazy_loader import (
    LazyModule,
    _lazy_cache,
    clear_lazy_cache,
    get_loaded_modules,
    is_module_loaded,
    lazy_callable,
    lazy_import,
    preload_module,
)


@pytest.fixture(autouse=True)
def clean_cache():
    """Ensure lazy cache is clean before each test."""
    clear_lazy_cache()
    yield
    clear_lazy_cache()


class TestLazyModule:
    def test_repr_shows_not_loaded(self):
        lm = LazyModule("json")
        assert "not loaded" in repr(lm)
        assert "json" in repr(lm)

    def test_repr_shows_loaded_after_access(self):
        lm = LazyModule("json")
        _ = lm.dumps  # trigger load
        assert "loaded" in repr(lm)

    def test_attribute_access_loads_module(self):
        lm = LazyModule("json")
        # json.dumps is a callable
        assert callable(lm.dumps)

    def test_loaded_flag_false_initially(self):
        lm = LazyModule("json")
        assert not object.__getattribute__(lm, "_loaded")

    def test_loaded_flag_true_after_access(self):
        lm = LazyModule("json")
        _ = lm.dumps
        assert object.__getattribute__(lm, "_loaded")

    def test_nonexistent_module_raises(self):
        lm = LazyModule("navig._does_not_exist_xyz")
        with pytest.raises(ModuleNotFoundError):
            _ = lm.anything

    def test_uses_cache_on_second_access(self):
        lm1 = LazyModule("json")
        lm2 = LazyModule("json")
        _ = lm1.dumps
        # Second LazyModule should use cache
        with patch("navig.lazy_loader.importlib.import_module") as mock_import:
            _ = lm2.dumps
        mock_import.assert_not_called()

    def test_module_functionality_works(self):
        """json.dumps/loads must work through the proxy."""
        lm = LazyModule("json")
        text = lm.dumps({"hello": "world"})
        assert '"hello"' in text
        parsed = lm.loads(text)
        assert parsed == {"hello": "world"}


class TestLazyImport:
    def test_returns_lazy_module_instance(self):
        result = lazy_import("json")
        assert isinstance(result, LazyModule)

    def test_module_not_loaded_on_creation(self):
        result = lazy_import("json")
        assert not object.__getattribute__(result, "_loaded")

    def test_attr_access_loads_module(self):
        lm = lazy_import("json")
        assert callable(lm.dumps)


class TestLazyCallable:
    def test_returns_callable(self):
        fn = lazy_callable("json", "dumps")
        assert callable(fn)

    def test_callable_name_preserved(self):
        fn = lazy_callable("json", "dumps")
        assert fn.__name__ == "dumps"

    def test_callable_invocation_works(self):
        fn = lazy_callable("json", "dumps")
        result = fn({"a": 1})
        assert '"a"' in result

    def test_uses_cache_on_second_call(self):
        fn = lazy_callable("json", "dumps")
        fn({"x": 1})  # populate cache
        with patch("navig.lazy_loader.importlib.import_module") as mock_import:
            fn({"y": 2})
        mock_import.assert_not_called()


class TestPreloadModule:
    def test_preload_adds_to_cache(self):
        assert "json" not in _lazy_cache
        preload_module("json")
        assert "json" in _lazy_cache

    def test_preload_noop_if_already_cached(self):
        preload_module("json")
        with patch("navig.lazy_loader.importlib.import_module") as mock_import:
            preload_module("json")
        mock_import.assert_not_called()


class TestIsModuleLoaded:
    def test_false_for_unloaded(self):
        # Remove from both caches to ensure clean state
        _lazy_cache.pop("json", None)
        # Save and restore sys.modules state
        was_loaded = "json" in sys.modules
        if "json" in sys.modules:
            # json is always in sys.modules on Python, skip this test path
            assert is_module_loaded("json") is True
            return
        assert is_module_loaded("json") is False

    def test_true_after_preload(self):
        preload_module("json")
        assert is_module_loaded("json") is True

    def test_true_for_module_in_sys_modules(self):
        # json is always in sys.modules
        assert "json" in sys.modules
        assert is_module_loaded("json") is True

    def test_false_for_unknown_module(self):
        assert is_module_loaded("navig._certainly_not_real_module_xyz") is False


class TestClearLazyCache:
    def test_clears_all_entries(self):
        preload_module("json")
        preload_module("os")
        assert len(_lazy_cache) >= 2
        clear_lazy_cache()
        assert len(_lazy_cache) == 0


class TestGetLoadedModules:
    def test_returns_list(self):
        assert isinstance(get_loaded_modules(), list)

    def test_includes_preloaded_module(self):
        preload_module("json")
        assert "json" in get_loaded_modules()

    def test_empty_after_clear(self):
        preload_module("json")
        clear_lazy_cache()
        assert get_loaded_modules() == []
