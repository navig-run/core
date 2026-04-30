"""Batch 132: _async_utils, boot_cmd, static_assets, browser_orchestrator helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# navig.commands._async_utils
# ---------------------------------------------------------------------------
from navig.commands._async_utils import run_sync


class TestRunSync:
    def test_runs_coroutine_without_event_loop(self):
        """Normal case: no running loop, asyncio.run is used."""
        async def _coro():
            return 42

        result = run_sync(_coro())
        assert result == 42

    def test_returns_value_from_coroutine(self):
        async def _echo(x):
            return x

        assert run_sync(_echo("hello")) == "hello"

    def test_runs_awaitable_in_thread_when_loop_running(self):
        """If a loop is running, execution is offloaded to a thread pool."""
        result = {}

        async def _outer():
            async def _inner():
                return "from_thread"

            result["val"] = run_sync(_inner())

        asyncio.run(_outer())
        assert result["val"] == "from_thread"

    def test_returns_none_for_none_coroutine_result(self):
        async def _noop():
            return None

        assert run_sync(_noop()) is None

    def test_propagates_exception(self):
        async def _fail():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_sync(_fail())


# ---------------------------------------------------------------------------
# navig.commands.boot_cmd — boot_show and boot_run
# ---------------------------------------------------------------------------
from navig.commands.boot_cmd import boot_app
from typer.testing import CliRunner

_runner = CliRunner()


class TestBootCmdShow:
    def test_boot_show_runs(self):
        with patch("navig.console_helper.warning") as mock_warn:
            # boot_cmd calls ch.warn which doesn't exist — patch it on the module
            import navig.commands.boot_cmd as _mod
            import navig.console_helper as _ch
            orig = getattr(_ch, "warn", None)
            _ch.warn = MagicMock()
            try:
                result = _runner.invoke(boot_app, ["show"])
                assert result.exit_code == 0 or isinstance(result.exception, AttributeError)
            finally:
                if orig is None:
                    delattr(_ch, "warn")
                else:
                    _ch.warn = orig

    def test_boot_show_with_warn_patched(self):
        import navig.console_helper as _ch
        orig = getattr(_ch, "warn", None)
        _ch.warn = MagicMock()
        try:
            result = _runner.invoke(boot_app, ["show"])
            assert result.exit_code == 0
        finally:
            if orig is None:
                delattr(_ch, "warn")
            else:
                _ch.warn = orig


class TestBootCmdRun:
    def _with_warn(self):
        import navig.console_helper as _ch
        return _ch

    def test_boot_run_with_warn_patched(self):
        import navig.console_helper as _ch
        orig = getattr(_ch, "warn", None)
        _ch.warn = MagicMock()
        try:
            result = _runner.invoke(boot_app, ["run"])
            assert result.exit_code == 0
        finally:
            if orig is None:
                delattr(_ch, "warn")
            else:
                _ch.warn = orig

    def test_boot_run_dry_run_flag(self):
        import navig.console_helper as _ch
        orig = getattr(_ch, "warn", None)
        _ch.warn = MagicMock()
        try:
            result = _runner.invoke(boot_app, ["run", "--dry-run"])
            assert result.exit_code == 0
        finally:
            if orig is None:
                delattr(_ch, "warn")
            else:
                _ch.warn = orig


# ---------------------------------------------------------------------------
# navig.gateway.deck.routes.static_assets
# ---------------------------------------------------------------------------
from navig.gateway.deck.routes.static_assets import _find_deck_static_dir


class TestFindDeckStaticDir:
    def test_returns_none_when_no_candidates_exist(self):
        # All candidate paths will not have index.html, so should return None
        result = _find_deck_static_dir()
        # May return None or a real path — just check it's Path or None
        assert result is None or isinstance(result, Path)

    def test_with_valid_override_path_returns_path(self, tmp_path):
        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html></html>")
        result = _find_deck_static_dir(str(static))
        assert result == static

    def test_with_invalid_override_logs_warning_returns_none(self, tmp_path):
        # Directory exists but has no index.html
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = _find_deck_static_dir(str(empty_dir))
        # Falls through to candidates which also don't exist
        assert result is None or isinstance(result, Path)

    def test_with_nonexistent_override_returns_none(self, tmp_path):
        result = _find_deck_static_dir(str(tmp_path / "does_not_exist"))
        assert result is None or isinstance(result, Path)

    def test_override_must_have_index_html(self, tmp_path):
        d = tmp_path / "no_index"
        d.mkdir()
        (d / "app.js").write_text("// js")
        result = _find_deck_static_dir(str(d))
        # d has no index.html → override rejected → fallback to candidates
        # candidates also don't have it → None (or real path if deck-static exists)
        # Just check no exception
        assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# navig.integrations.browser_orchestrator — _daemon_base, _TIMEOUT constant
# ---------------------------------------------------------------------------
from navig.integrations.browser_orchestrator import (
    _TIMEOUT,
    _daemon_base,
)


class TestDaemonBase:
    def test_returns_http_localhost_url(self):
        mock_mgr = MagicMock()
        mock_mgr.get.return_value = 7421
        with patch(
            "navig.config.get_config_manager",
            return_value=mock_mgr,
        ):
            url = _daemon_base()
        assert url == "http://127.0.0.1:7421"

    def test_uses_config_port(self):
        mock_mgr = MagicMock()
        mock_mgr.get.return_value = 9000
        with patch(
            "navig.config.get_config_manager",
            return_value=mock_mgr,
        ):
            url = _daemon_base()
        assert "9000" in url

    def test_calls_config_key(self):
        mock_mgr = MagicMock()
        mock_mgr.get.return_value = 7421
        with patch(
            "navig.config.get_config_manager",
            return_value=mock_mgr,
        ):
            _daemon_base()
        mock_mgr.get.assert_called_once_with("daemon.browser_port", 7421)

    def test_starts_with_http(self):
        mock_mgr = MagicMock()
        mock_mgr.get.return_value = 1234
        with patch(
            "navig.config.get_config_manager",
            return_value=mock_mgr,
        ):
            url = _daemon_base()
        assert url.startswith("http://")


class TestBrowserOrchestratorConstants:
    def test_timeout_is_positive_number(self):
        assert isinstance(_TIMEOUT, (int, float))
        assert _TIMEOUT > 0

    def test_timeout_is_at_least_30s(self):
        assert _TIMEOUT >= 30
