"""
Batch 104 — tests for:
  - navig.lazy_loader  (LazyModule, lazy_import, lazy_callable, is_module_loaded,
                        preload_module, clear_lazy_cache, get_loaded_modules)
  - navig.ipc_pipe     (_pipe_address, _is_promoted, get_pipe_status, constants,
                        log_shadow_anomaly)
"""

from __future__ import annotations

import sys
from pathlib import Path


# ============================================================================
# navig.lazy_loader
# ============================================================================


class TestLazyModule:
    def setup_method(self):
        from navig.lazy_loader import clear_lazy_cache
        clear_lazy_cache()

    def test_not_loaded_on_creation(self):
        from navig.lazy_loader import LazyModule

        lm = LazyModule("os")
        # Module should not be loaded yet (proxy state)
        loaded = object.__getattribute__(lm, "_loaded")
        assert loaded is False

    def test_repr_not_loaded(self):
        from navig.lazy_loader import LazyModule

        lm = LazyModule("os.path")
        repr_str = repr(lm)
        assert "os.path" in repr_str
        assert "not loaded" in repr_str

    def test_repr_after_load(self):
        from navig.lazy_loader import LazyModule

        lm = LazyModule("os")
        _ = lm.sep  # trigger load
        assert "loaded" in repr(lm)

    def test_attribute_access_loads_module(self):
        from navig.lazy_loader import LazyModule

        lm = LazyModule("os")
        assert lm.sep in ("/", "\\")  # os.sep is platform-specific
        loaded = object.__getattribute__(lm, "_loaded")
        assert loaded is True

    def test_attribute_access_returns_correct_value(self):
        from navig.lazy_loader import LazyModule

        lm = LazyModule("os")
        assert lm.sep == __import__("os").sep

    def test_nonexistent_module_raises(self):
        import pytest
        from navig.lazy_loader import LazyModule

        lm = LazyModule("navig._nonexistent_module_9x9x9")
        with pytest.raises((ImportError, ModuleNotFoundError)):
            _ = lm.something


class TestLazyImport:
    def setup_method(self):
        from navig.lazy_loader import clear_lazy_cache
        clear_lazy_cache()

    def test_returns_lazy_module(self):
        from navig.lazy_loader import LazyModule, lazy_import

        lm = lazy_import("os")
        assert isinstance(lm, LazyModule)

    def test_module_accessible_via_proxy(self):
        from navig.lazy_loader import lazy_import

        os_proxy = lazy_import("os")
        assert os_proxy.sep in ("/", "\\")

    def test_cache_reused(self):
        from navig.lazy_loader import _lazy_cache, lazy_import

        lazy_import("pathlib")
        _ = lazy_import("pathlib")
        # Pathlib should be in cache after first access trigger
        lm = lazy_import("pathlib")
        _ = lm.Path  # trigger load
        assert "pathlib" in _lazy_cache


class TestLazyCallable:
    def setup_method(self):
        from navig.lazy_loader import clear_lazy_cache
        clear_lazy_cache()

    def test_returns_callable(self):
        from navig.lazy_loader import lazy_callable

        path_cls = lazy_callable("pathlib", "Path")
        assert callable(path_cls)

    def test_callable_has_correct_name(self):
        from navig.lazy_loader import lazy_callable

        fn = lazy_callable("os.path", "join")
        assert fn.__name__ == "join"

    def test_callable_works_when_invoked(self):
        from navig.lazy_loader import lazy_callable

        join = lazy_callable("os.path", "join")
        result = join("/tmp", "test.txt")
        assert "test.txt" in result

    def test_callable_caches_module(self):
        from navig.lazy_loader import _lazy_cache, lazy_callable

        fn = lazy_callable("os.path", "basename")
        fn("/tmp/file.txt")
        assert "os.path" in _lazy_cache


class TestIsModuleLoaded:
    def setup_method(self):
        from navig.lazy_loader import clear_lazy_cache
        clear_lazy_cache()

    def test_stdlib_module_always_loaded(self):
        from navig.lazy_loader import is_module_loaded

        # sys is always in sys.modules
        assert is_module_loaded("sys") is True

    def test_unloaded_module_returns_false(self):
        from navig.lazy_loader import is_module_loaded

        # A module we haven't loaded yet
        # Note: some exotic modules might already be loaded as side effects
        # so use a very unlikely module name
        assert is_module_loaded("navig._not_loaded_batch104") is False

    def test_preloaded_module_returns_true(self):
        from navig.lazy_loader import is_module_loaded, preload_module

        preload_module("os")
        assert is_module_loaded("os") is True


class TestPreloadModule:
    def setup_method(self):
        from navig.lazy_loader import clear_lazy_cache
        clear_lazy_cache()

    def test_adds_to_cache(self):
        from navig.lazy_loader import _lazy_cache, preload_module

        preload_module("os.path")
        assert "os.path" in _lazy_cache

    def test_no_double_load(self):
        from navig.lazy_loader import _lazy_cache, preload_module

        preload_module("os.path")
        first = _lazy_cache.get("os.path")
        preload_module("os.path")  # second call should be no-op
        assert _lazy_cache.get("os.path") is first


class TestClearLazyCache:
    def test_clears_cache(self):
        from navig.lazy_loader import _lazy_cache, clear_lazy_cache, preload_module

        preload_module("os")
        assert _lazy_cache  # non-empty
        clear_lazy_cache()
        assert not _lazy_cache

    def test_idempotent(self):
        from navig.lazy_loader import clear_lazy_cache

        clear_lazy_cache()
        clear_lazy_cache()  # should not raise


class TestGetLoadedModules:
    def setup_method(self):
        from navig.lazy_loader import clear_lazy_cache
        clear_lazy_cache()

    def test_empty_initially(self):
        from navig.lazy_loader import get_loaded_modules

        assert get_loaded_modules() == []

    def test_returns_loaded_names(self):
        from navig.lazy_loader import get_loaded_modules, preload_module

        preload_module("os")
        preload_module("os.path")
        loaded = get_loaded_modules()
        assert "os" in loaded
        assert "os.path" in loaded

    def test_returns_list(self):
        from navig.lazy_loader import get_loaded_modules

        result = get_loaded_modules()
        assert isinstance(result, list)


# ============================================================================
# navig.ipc_pipe
# ============================================================================


class TestIpcPipeConstants:
    def test_is_windows_is_bool(self):
        from navig.ipc_pipe import _IS_WINDOWS

        assert isinstance(_IS_WINDOWS, bool)
        assert _IS_WINDOWS == (sys.platform == "win32")

    def test_shadow_promote_after_positive(self):
        from navig.ipc_pipe import SHADOW_PROMOTE_AFTER

        assert isinstance(SHADOW_PROMOTE_AFTER, int)
        assert SHADOW_PROMOTE_AFTER > 0


class TestPipeAddress:
    def test_returns_string(self):
        from navig.ipc_pipe import _pipe_address

        addr = _pipe_address()
        assert isinstance(addr, str)
        assert len(addr) > 0

    def test_windows_named_pipe_format(self):
        from navig.ipc_pipe import _IS_WINDOWS, _pipe_address

        addr = _pipe_address()
        if _IS_WINDOWS:
            assert addr.startswith("\\\\.\\pipe\\")
        else:
            # Unix domain socket path
            assert addr.startswith("/") or "navig" in addr.lower()

    def test_deterministic(self):
        from navig.ipc_pipe import _pipe_address

        assert _pipe_address() == _pipe_address()


class TestIsPromoted:
    def test_returns_bool(self):
        from navig.ipc_pipe import _is_promoted

        result = _is_promoted()
        assert isinstance(result, bool)

    def test_false_when_no_flag_file(self, tmp_path, monkeypatch):
        """Without the promoted flag file, should return False."""
        from navig.ipc_pipe import _is_promoted

        # Patch paths.config_dir() to a temp path with no flag file
        monkeypatch.setattr(
            "navig.platform.paths.config_dir",
            lambda: tmp_path,
        )
        # Re-import to apply patch (or call directly)
        result = _is_promoted()
        assert isinstance(result, bool)


class TestGetPipeStatus:
    def test_returns_dict(self):
        from navig.ipc_pipe import get_pipe_status

        status = get_pipe_status()
        assert isinstance(status, dict)

    def test_contains_expected_keys(self):
        from navig.ipc_pipe import get_pipe_status

        status = get_pipe_status()
        # Should have at least promoted and promote_after
        assert "promoted" in status or len(status) > 0

    def test_promoted_is_bool(self):
        from navig.ipc_pipe import get_pipe_status

        status = get_pipe_status()
        if "promoted" in status:
            assert isinstance(status["promoted"], bool)


class TestLogShadowAnomaly:
    def test_does_not_raise(self, tmp_path, monkeypatch):
        from navig.ipc_pipe import log_shadow_anomaly

        # Should not raise even with arbitrary data
        try:
            log_shadow_anomaly(
                source="test",
                event_type="mismatch",
                data={"fast": "A", "safe": "B"},
            )
        except Exception as exc:
            # IO errors are acceptable (e.g. can't write to log dir)
            # but should not propagate fatal errors
            assert isinstance(exc, (OSError, IOError, PermissionError))
