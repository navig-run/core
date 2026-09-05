"""Tests for navig/integrations/browser_orchestrator.py."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


class TestDaemonBase:
    def test_daemon_base_returns_url_string(self):
        from navig.integrations.browser_orchestrator import _daemon_base
        with patch("navig.config.get_config_manager") as mock_cfg:
            mock_cfg.return_value.get.return_value = 7421
            result = _daemon_base()
        assert isinstance(result, str)
        assert "7421" in result

    def test_daemon_base_default_port(self):
        from navig.integrations.browser_orchestrator import _daemon_base
        with patch("navig.config.get_config_manager") as mock_cfg:
            mock_cfg.return_value.get.return_value = 7421
            result = _daemon_base()
        assert "7421" in result

    def test_daemon_base_custom_port(self):
        from navig.integrations.browser_orchestrator import _daemon_base
        with patch("navig.config.get_config_manager") as mock_cfg:
            mock_cfg.return_value.get.return_value = 9999
            result = _daemon_base()
        assert "9999" in result


class TestTimeoutConstant:
    def test_timeout_is_positive_int(self):
        from navig.integrations.browser_orchestrator import _TIMEOUT
        assert isinstance(_TIMEOUT, int)
        assert _TIMEOUT > 0

    def test_timeout_value(self):
        from navig.integrations.browser_orchestrator import _TIMEOUT
        assert _TIMEOUT == 120


class TestRunBrowserTask:
    def test_run_browser_task_is_coroutine(self):
        import inspect

        from navig.integrations.browser_orchestrator import run_browser_task
        assert inspect.iscoroutinefunction(run_browser_task)

    def test_run_browser_task_accepts_spec(self):
        from navig.integrations.browser_orchestrator import run_browser_task

        async def _run():
            from unittest.mock import patch
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "done", "result": "ok"}
            with patch("navig.config.get_config_manager") as mcfg, \
                 patch("httpx.AsyncClient") as mcls:
                mcfg.return_value.get.return_value = 7421
                mc = AsyncMock()
                mcls.return_value.__aenter__ = AsyncMock(return_value=mc)
                mcls.return_value.__aexit__ = AsyncMock(return_value=False)
                mc.post = AsyncMock(return_value=mock_resp)
                return await run_browser_task({"action": "screenshot"})

        # Was `except Exception: return None` plus no assertion, so it passed whether the
        # orchestrator worked, returned nothing, or blew up. The transport is mocked to a
        # 200 with a known body, so the call has one correct answer -- assert it.
        result = asyncio.run(_run())
        assert result is not None, "run_browser_task returned nothing for a mocked 200"

    def test_run_browser_task_max_hitl_retries_param(self):
        import inspect

        from navig.integrations.browser_orchestrator import run_browser_task
        sig = inspect.signature(run_browser_task)
        assert "max_hitl_retries" in sig.parameters
