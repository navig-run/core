"""Batch 130: tests for windows_utils, screenshot backend, and BrowserConfig."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# navig.platform.windows_utils
# ---------------------------------------------------------------------------
from navig.platform.windows_utils import (
    _GRACEFUL_KILL_GRACE_PERIOD,
    _PRIVATE_USE_AREA_PATTERN,
    check_pid_exists,
    ps_quote,
    ps_quote_for_xml,
    remove_private_use_chars,
    run_with_graceful_timeout,
)


class TestPsQuote:
    def test_simple_string(self):
        assert ps_quote("hello") == "'hello'"

    def test_empty_string(self):
        assert ps_quote("") == "''"

    def test_string_with_single_quote(self):
        assert ps_quote("O'Reilly") == "'O''Reilly'"

    def test_string_with_multiple_single_quotes(self):
        assert ps_quote("it's a 'test'") == "'it''s a ''test'''"

    def test_string_with_spaces(self):
        assert ps_quote("hello world") == "'hello world'"

    def test_string_with_double_quotes(self):
        # double quotes should pass through unchanged
        assert ps_quote('say "hi"') == "'say \"hi\"'"

    def test_wraps_as_single_quoted(self):
        result = ps_quote("value")
        assert result.startswith("'")
        assert result.endswith("'")


class TestPsQuoteForXml:
    def test_ampersand(self):
        result = ps_quote_for_xml("A&B")
        assert "&amp;" in result

    def test_less_than(self):
        result = ps_quote_for_xml("<div>")
        assert "&lt;" in result

    def test_greater_than(self):
        result = ps_quote_for_xml("a>b")
        assert "&gt;" in result

    def test_double_quote(self):
        result = ps_quote_for_xml('say "hi"')
        assert "&quot;" in result

    def test_single_quote(self):
        result = ps_quote_for_xml("O'Reilly")
        assert "&apos;" in result

    def test_no_special_chars(self):
        result = ps_quote_for_xml("hello")
        assert result == "'hello'"

    def test_wraps_in_single_quotes(self):
        result = ps_quote_for_xml("test")
        assert result.startswith("'")
        assert result.endswith("'")

    def test_all_xml_chars(self):
        result = ps_quote_for_xml("& < > \" '")
        assert "&amp;" in result
        assert "&lt;" in result
        assert "&gt;" in result
        assert "&quot;" in result
        assert "&apos;" in result

    def test_result_is_ps_quoted(self):
        # The final result must be wrapped by ps_quote, so embedded apos must be doubled
        result = ps_quote_for_xml("O'Reilly")
        # &apos; becomes &&apos;' after ps_quote escaping
        assert result.startswith("'") and result.endswith("'")


class TestRemovePrivateUseChars:
    def test_no_pua(self):
        assert remove_private_use_chars("hello") == "hello"

    def test_empty_string(self):
        assert remove_private_use_chars("") == ""

    def test_bmp_pua_stripped(self):
        text = "A\ue000B"
        assert remove_private_use_chars(text) == "AB"

    def test_multiple_pua_stripped(self):
        text = "\ue000\uf8ff\uf000"
        assert remove_private_use_chars(text) == ""

    def test_non_pua_unicode_kept(self):
        text = "Héllo wörld"
        assert remove_private_use_chars(text) == text

    def test_pattern_compiled(self):
        import re
        assert isinstance(_PRIVATE_USE_AREA_PATTERN, re.Pattern)


class TestCheckPidExists:
    def test_returns_false_without_psutil(self):
        with patch.dict("sys.modules", {"psutil": None}):
            # Importing psutil fails → returns False
            # We need to patch the import inside the function
            with patch("builtins.__import__", side_effect=ImportError):
                result = check_pid_exists(9999999)
                assert result is False

    def test_returns_false_no_such_process(self):
        mock_psutil = MagicMock()
        mock_psutil.NoSuchProcess = Exception
        mock_psutil.STATUS_ZOMBIE = "zombie"
        mock_psutil.STATUS_DEAD = "dead"
        mock_psutil.Process.side_effect = mock_psutil.NoSuchProcess("no such")
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = check_pid_exists(9999999)
            assert result is False

    def test_returns_false_for_zombie(self):
        mock_psutil = MagicMock()
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.STATUS_ZOMBIE = "zombie"
        mock_psutil.STATUS_DEAD = "dead"
        mock_proc = MagicMock()
        mock_proc.status.return_value = "zombie"
        mock_psutil.Process.return_value = mock_proc
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = check_pid_exists(123)
            assert result is False

    def test_returns_true_for_running_process(self):
        mock_psutil = MagicMock()
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.STATUS_ZOMBIE = "zombie"
        mock_psutil.STATUS_DEAD = "dead"
        mock_proc = MagicMock()
        mock_proc.status.return_value = "running"
        mock_psutil.Process.return_value = mock_proc
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = check_pid_exists(123)
            assert result is True


class TestGracefulKillConstants:
    def test_grace_period_is_float(self):
        assert isinstance(_GRACEFUL_KILL_GRACE_PERIOD, float)
        assert _GRACEFUL_KILL_GRACE_PERIOD > 0


class TestRunWithGracefulTimeout:
    def test_non_windows_delegates_to_subprocess_run(self):
        """On non-windows, just calls subprocess.run with timeout."""
        completed = subprocess.CompletedProcess(["echo"], 0, b"hi", b"")
        with patch("sys.platform", "linux"):
            with patch("subprocess.run", return_value=completed) as mock_run:
                result = run_with_graceful_timeout(
                    ["echo", "hi"], timeout=5.0, capture_output=True
                )
        mock_run.assert_called_once()
        assert result.returncode == 0

    def test_non_windows_passes_timeout(self):
        completed = subprocess.CompletedProcess(["echo"], 0, b"", b"")
        with patch("sys.platform", "linux"):
            with patch("subprocess.run", return_value=completed) as mock_run:
                run_with_graceful_timeout(["echo"], timeout=3.0)
        _, kwargs = mock_run.call_args
        assert kwargs.get("timeout") == 3.0


# ---------------------------------------------------------------------------
# navig.adapters.automation.screenshot
# ---------------------------------------------------------------------------
from navig.adapters.automation.screenshot import (
    _BACKEND_ENV_VAR,
    _BACKEND_REGISTRY,
    _ScreenshotBackend,
    _DxcamBackend,
    _MssBackend,
    _PillowBackend,
    _get_env_backend,
    get_screenshot_backend,
)


class TestBackendRegistry:
    def test_registry_contains_dxcam(self):
        assert "dxcam" in _BACKEND_REGISTRY

    def test_registry_contains_mss(self):
        assert "mss" in _BACKEND_REGISTRY

    def test_registry_contains_pillow(self):
        assert "pillow" in _BACKEND_REGISTRY

    def test_dxcam_backend_class(self):
        assert _BACKEND_REGISTRY["dxcam"] is _DxcamBackend

    def test_mss_backend_class(self):
        assert _BACKEND_REGISTRY["mss"] is _MssBackend

    def test_pillow_backend_class(self):
        assert _BACKEND_REGISTRY["pillow"] is _PillowBackend

    def test_subclass_auto_registered(self):
        """A new subclass with a unique name is auto-registered."""

        class _TestBackend(_ScreenshotBackend):
            name = "_test_batch_130"
            priority = 999

        assert "_test_batch_130" in _BACKEND_REGISTRY
        del _BACKEND_REGISTRY["_test_batch_130"]  # cleanup

    def test_subclass_without_name_not_registered(self):
        before = set(_BACKEND_REGISTRY.keys())

        class _NoName(_ScreenshotBackend):
            pass  # name="" → not registered

        assert set(_BACKEND_REGISTRY.keys()) == before


class TestBackendPriorities:
    def test_dxcam_priority_lower_than_mss(self):
        assert _DxcamBackend.priority < _MssBackend.priority

    def test_mss_priority_lower_than_pillow(self):
        assert _MssBackend.priority < _PillowBackend.priority

    def test_pillow_priority_is_100(self):
        assert _PillowBackend.priority == 100


class TestGetScreenshotBackend:
    def setup_method(self):
        """Clear lru_cache before each test."""
        get_screenshot_backend.cache_clear()

    def test_explicit_pillow_available(self):
        backend_cls = _BACKEND_REGISTRY["pillow"]
        with patch.object(backend_cls, "is_available", return_value=True):
            get_screenshot_backend.cache_clear()
            result = get_screenshot_backend("pillow")
            assert isinstance(result, backend_cls)

    def test_explicit_unavailable_raises_runtime_error(self):
        backend_cls = _BACKEND_REGISTRY["dxcam"]
        with patch.object(backend_cls, "is_available", return_value=False):
            get_screenshot_backend.cache_clear()
            with pytest.raises(RuntimeError, match="not available"):
                get_screenshot_backend("dxcam")

    def test_unknown_name_raises_value_error(self):
        get_screenshot_backend.cache_clear()
        with pytest.raises(ValueError, match="Unknown screenshot backend"):
            get_screenshot_backend("nonexistent_backend_xyz")

    def test_auto_picks_lowest_priority_available(self):
        """auto mode should pick the first available by priority (lowest number)."""
        pillow_cls = _BACKEND_REGISTRY["pillow"]
        mss_cls = _BACKEND_REGISTRY["mss"]
        dxcam_cls = _BACKEND_REGISTRY["dxcam"]

        def _available_only_pillow(self):
            return isinstance(self, pillow_cls)

        with (
            patch.object(dxcam_cls, "is_available", return_value=False),
            patch.object(mss_cls, "is_available", return_value=False),
            patch.object(pillow_cls, "is_available", return_value=True),
        ):
            get_screenshot_backend.cache_clear()
            result = get_screenshot_backend("auto")
            assert isinstance(result, pillow_cls)

    def test_auto_no_backend_raises_runtime_error(self):
        pillow_cls = _BACKEND_REGISTRY["pillow"]
        mss_cls = _BACKEND_REGISTRY["mss"]
        dxcam_cls = _BACKEND_REGISTRY["dxcam"]

        with (
            patch.object(dxcam_cls, "is_available", return_value=False),
            patch.object(mss_cls, "is_available", return_value=False),
            patch.object(pillow_cls, "is_available", return_value=False),
        ):
            get_screenshot_backend.cache_clear()
            with pytest.raises(RuntimeError, match="No screenshot backend"):
                get_screenshot_backend("auto")


class TestGetEnvBackend:
    def setup_method(self):
        get_screenshot_backend.cache_clear()

    def test_defaults_to_auto(self):
        pillow_cls = _BACKEND_REGISTRY["pillow"]
        with patch.dict("os.environ", {}, clear=False):
            # Remove the key if present
            import os
            os.environ.pop(_BACKEND_ENV_VAR, None)
            with (
                patch.object(_DxcamBackend, "is_available", return_value=False),
                patch.object(_MssBackend, "is_available", return_value=False),
                patch.object(pillow_cls, "is_available", return_value=True),
            ):
                get_screenshot_backend.cache_clear()
                result = _get_env_backend()
                assert isinstance(result, pillow_cls)

    def test_env_var_selects_backend(self):
        with patch.dict("os.environ", {_BACKEND_ENV_VAR: "pillow"}):
            pillow_cls = _BACKEND_REGISTRY["pillow"]
            with patch.object(pillow_cls, "is_available", return_value=True):
                get_screenshot_backend.cache_clear()
                result = _get_env_backend()
                assert isinstance(result, pillow_cls)

    def test_env_var_name(self):
        assert _BACKEND_ENV_VAR == "NAVIG_SCREENSHOT_BACKEND"


class TestScreenshotBackendInterface:
    def test_base_is_available_raises(self):
        b = _ScreenshotBackend()
        with pytest.raises(NotImplementedError):
            b.is_available()

    def test_base_capture_region_raises(self):
        b = _ScreenshotBackend()
        with pytest.raises(NotImplementedError):
            b.capture_region(0, 0, 100, 100)

    def test_dxcam_not_available_on_non_windows(self):
        with patch("sys.platform", "linux"):
            _DxcamBackend.is_available.cache_clear()
            backend = _DxcamBackend()
            # On linux, dxcam should return False
            # (is_available checks sys.platform before importing)
            # We need to clear the lru_cache first
            assert backend.is_available() is False

    def test_mss_unavailable_when_import_fails(self):
        _MssBackend.is_available.cache_clear()
        with patch.dict("sys.modules", {"mss": None}):
            backend = _MssBackend()
            with patch("builtins.__import__", side_effect=ImportError("no mss")):
                result = backend.is_available()
                assert result is False


# ---------------------------------------------------------------------------
# navig.browser.controller — BrowserConfig
# ---------------------------------------------------------------------------
from navig.browser.controller import BrowserConfig


class TestBrowserConfigDefaults:
    def test_enabled_default(self):
        cfg = BrowserConfig()
        assert cfg.enabled is True

    def test_headless_default(self):
        cfg = BrowserConfig()
        assert cfg.headless is True

    def test_timeout_ms_default(self):
        cfg = BrowserConfig()
        assert cfg.timeout_ms == 30000

    def test_viewport_defaults(self):
        cfg = BrowserConfig()
        assert cfg.viewport_width == 1280
        assert cfg.viewport_height == 720

    def test_screenshot_dir_default(self):
        cfg = BrowserConfig()
        assert "screenshots" in cfg.screenshot_dir

    def test_user_data_dir_default_none(self):
        cfg = BrowserConfig()
        assert cfg.user_data_dir is None

    def test_proxy_default_none(self):
        cfg = BrowserConfig()
        assert cfg.proxy is None

    def test_ignore_https_errors_default(self):
        cfg = BrowserConfig()
        assert cfg.ignore_https_errors is False

    def test_allowed_domains_empty_list(self):
        cfg = BrowserConfig()
        assert cfg.allowed_domains == []

    def test_blocked_domains_empty_list(self):
        cfg = BrowserConfig()
        assert cfg.blocked_domains == []

    def test_mutable_defaults_are_independent(self):
        cfg1 = BrowserConfig()
        cfg2 = BrowserConfig()
        cfg1.allowed_domains.append("example.com")
        assert cfg2.allowed_domains == []


class TestBrowserConfigFromConfig:
    def test_empty_dict_uses_defaults(self):
        cfg = BrowserConfig.from_config({})
        assert cfg.enabled is True
        assert cfg.headless is True
        assert cfg.timeout_ms == 30000
        assert cfg.viewport_width == 1280
        assert cfg.viewport_height == 720

    def test_full_config(self):
        data = {
            "browser": {
                "enabled": False,
                "headless": False,
                "timeout_seconds": 60,
                "viewport": {"width": 1920, "height": 1080},
                "user_data_dir": "/tmp/userdata",
                "screenshot_dir": "/tmp/shots",
                "proxy": "socks5://localhost:1080",
                "ignore_https_errors": True,
                "allowed_domains": ["example.com"],
                "blocked_domains": ["bad.com"],
            }
        }
        cfg = BrowserConfig.from_config(data)
        assert cfg.enabled is False
        assert cfg.headless is False
        assert cfg.timeout_ms == 60 * 1000
        assert cfg.viewport_width == 1920
        assert cfg.viewport_height == 1080
        assert cfg.user_data_dir == "/tmp/userdata"
        assert cfg.screenshot_dir == "/tmp/shots"
        assert cfg.proxy == "socks5://localhost:1080"
        assert cfg.ignore_https_errors is True
        assert cfg.allowed_domains == ["example.com"]
        assert cfg.blocked_domains == ["bad.com"]

    def test_timeout_seconds_multiplied_by_1000(self):
        cfg = BrowserConfig.from_config({"browser": {"timeout_seconds": 10}})
        assert cfg.timeout_ms == 10000

    def test_viewport_only_width_overridden(self):
        cfg = BrowserConfig.from_config({"browser": {"viewport": {"width": 800}}})
        assert cfg.viewport_width == 800
        assert cfg.viewport_height == 720  # default

    def test_proxy_none_by_default(self):
        cfg = BrowserConfig.from_config({"browser": {}})
        assert cfg.proxy is None


class TestGetPlaywright:
    def test_import_error_raises_import_error(self):
        from navig.browser.controller import _get_playwright
        import navig.browser.controller as ctrl
        # Reset global so we force re-import
        ctrl._playwright = None
        with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
            with patch("builtins.__import__", side_effect=ImportError("no playwright")):
                with pytest.raises(ImportError, match="Playwright not installed"):
                    _get_playwright()
        ctrl._playwright = None  # cleanup
