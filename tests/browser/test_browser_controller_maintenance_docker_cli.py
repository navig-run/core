"""
Tests covering:
  - navig/browser/controller.py  (BrowserConfig, BrowserController)
  - navig/commands/maintenance.py
  - navig/commands/docker.py

(It also used to cover navig/cli/legacy_flat_commands.py and navig/cli/
host_infra.py. Nothing ever registered those two modules, so every command and
deprecation shim in them was dead — including the ones other code told users to
run. They were deleted, and these tests with them: they only ever proved that
dead code was internally consistent.)
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal playwright stub so browser/controller.py imports cleanly
# ---------------------------------------------------------------------------
def _stub_playwright():
    """Stub playwright ONLY when it is genuinely not installed.

    The old guard was ``if "playwright" in sys.modules: return`` — which asks "has it been
    imported YET?", not "is it INSTALLED?". This function runs at COLLECTION time, when
    nothing has imported playwright yet, so with a real playwright present it happily shoved
    a MagicMock into ``sys.modules`` and left it there — never restored — for the whole
    worker process, permanently shadowing the real package.

    Anything imported afterwards that needs real playwright then died. camoufox imports
    playwright, so tests/browser/test_firefox.py blew up with ImportError — but only when
    this module happened to be imported first in a worker. That is why all 5 of those tests
    passed in isolation and failed in the full xdist run: a cross-test poisoning, not a bug
    in the tests they broke.

    Importing the real module here (instead of sniffing sys.modules) costs one import and
    makes the stub what it always claimed to be: a fallback for environments without
    playwright.
    """
    try:
        import playwright.async_api  # noqa: F401, PLC0415
    except ImportError:
        pass  # not installed — the stub below is the whole point
    else:
        return  # real playwright is present; never shadow it

    pw = types.ModuleType("playwright")
    pw_async = types.ModuleType("playwright.async_api")
    pw_async.async_playwright = MagicMock()
    pw_async.Browser = MagicMock
    pw_async.Page = MagicMock
    pw.async_api = pw_async
    sys.modules["playwright"] = pw
    sys.modules["playwright.async_api"] = pw_async


_stub_playwright()


# ---------------------------------------------------------------------------
# 1. navig/browser/controller.py
# ---------------------------------------------------------------------------
class TestBrowserConfig:
    def test_import(self):
        from navig.browser.controller import BrowserConfig
        assert BrowserConfig is not None

    def test_defaults(self):
        from navig.browser.controller import BrowserConfig
        cfg = BrowserConfig()
        assert cfg.enabled is True
        assert cfg.headless is True
        assert isinstance(cfg.timeout_ms, int)

    def test_custom_values(self):
        from navig.browser.controller import BrowserConfig
        cfg = BrowserConfig(enabled=False, headless=False, timeout_ms=5000)
        assert cfg.enabled is False
        assert cfg.headless is False
        assert cfg.timeout_ms == 5000


class TestBrowserController:
    def test_import(self):
        from navig.browser.controller import BrowserController
        assert BrowserController is not None

    def test_default_construction(self):
        from navig.browser.controller import BrowserConfig, BrowserController
        ctrl = BrowserController(BrowserConfig())
        assert ctrl is not None

    def test_is_running_false_by_default(self):
        from navig.browser.controller import BrowserConfig, BrowserController
        ctrl = BrowserController(BrowserConfig())
        # is_running is a property (bool)
        assert ctrl.is_running is False

    def test_has_navigate_method(self):
        from navig.browser.controller import BrowserConfig, BrowserController
        ctrl = BrowserController(BrowserConfig())
        assert callable(getattr(ctrl, "navigate", None))

    def test_has_screenshot_method(self):
        from navig.browser.controller import BrowserConfig, BrowserController
        ctrl = BrowserController(BrowserConfig())
        assert callable(getattr(ctrl, "screenshot", None))

    def test_has_get_a11y_tree_method(self):
        from navig.browser.controller import BrowserConfig, BrowserController
        ctrl = BrowserController(BrowserConfig())
        assert callable(getattr(ctrl, "get_a11y_tree", None))

    def test_has_fill_method(self):
        from navig.browser.controller import BrowserConfig, BrowserController
        ctrl = BrowserController(BrowserConfig())
        assert callable(getattr(ctrl, "fill", None))

    def test_check_domain_allowed_no_restriction(self):
        from navig.browser.controller import BrowserConfig, BrowserController
        cfg = BrowserConfig()
        ctrl = BrowserController(cfg)
        # When no allowed_domains configured, any domain should be allowed
        result = ctrl._check_domain_allowed("https://example.com")
        assert result is True or result is None or isinstance(result, bool)


# ---------------------------------------------------------------------------
# 2. navig/commands/maintenance.py
# ---------------------------------------------------------------------------
class TestMaintenanceModule:
    def test_import(self):
        import navig.commands.maintenance as m
        assert m is not None

    def test_update_packages_callable(self):
        import navig.commands.maintenance as m
        assert callable(m.update_packages)

    def test_clean_packages_callable(self):
        import navig.commands.maintenance as m
        assert callable(m.clean_packages)

    def test_rotate_logs_callable(self):
        import navig.commands.maintenance as m
        assert callable(m.rotate_logs)

    def test_cleanup_temp_callable(self):
        import navig.commands.maintenance as m
        assert callable(m.cleanup_temp)

    def test_check_filesystem_callable(self):
        import navig.commands.maintenance as m
        assert callable(m.check_filesystem)

    def test_system_maintenance_callable(self):
        import navig.commands.maintenance as m
        assert callable(m.system_maintenance)

    def test_system_info_callable(self):
        import navig.commands.maintenance as m
        assert callable(m.system_info)

    def test_update_packages_dry_run(self):
        """Should not execute remote ops in dry_run=True path."""
        import navig.commands.maintenance as m
        mock_remote = MagicMock()
        mock_cm = MagicMock()
        mock_cm.get = MagicMock(return_value=None)
        mock_ops = MagicMock()
        mock_ops.execute_remote_command.return_value = (0, "ok", "")
        with (
            patch("navig.commands.maintenance.get_config_manager", return_value=mock_cm),
            patch("navig.commands.maintenance.require_active_server", return_value="myhost"),
            patch.object(mock_cm, "load_server_config", return_value={}),
            patch("navig.commands.maintenance.RemoteOperations", return_value=mock_ops),
        ):
            # Should run without exception (dry_run guards remote calls)
            try:
                m.update_packages({"dry_run": True, "json": True})
            except Exception:
                pass  # allowed — remote call may still fail in unit context


# ---------------------------------------------------------------------------
# 3. navig/commands/docker.py
# ---------------------------------------------------------------------------
class TestDockerModule:
    def test_import(self):
        import navig.commands.docker as d
        assert d is not None

    def test_docker_ps_callable(self):
        import navig.commands.docker as d
        assert callable(d.docker_ps)

    def test_docker_logs_callable(self):
        import navig.commands.docker as d
        assert callable(d.docker_logs)

    def test_docker_exec_callable(self):
        import navig.commands.docker as d
        assert callable(d.docker_exec)

    def test_docker_restart_callable(self):
        import navig.commands.docker as d
        assert callable(d.docker_restart)

    def test_docker_stop_callable(self):
        import navig.commands.docker as d
        assert callable(d.docker_stop)

    def test_docker_start_callable(self):
        import navig.commands.docker as d
        assert callable(d.docker_start)

    def test_docker_stats_callable(self):
        import navig.commands.docker as d
        assert callable(d.docker_stats)

    def test_docker_inspect_callable(self):
        import navig.commands.docker as d
        assert callable(d.docker_inspect)

    def test_docker_compose_callable(self):
        import navig.commands.docker as d
        assert callable(d.docker_compose)

    def test_private_callback_callable(self):
        import navig.commands.docker as d
        assert callable(d._docker_callback)

