"""Phase F — silent-exception diagnostic logging.

Verifies that bare ``except Exception: pass`` blocks that previously swallowed
failures without any trace have been replaced with ``_log.debug()`` calls so
that debug log inspection (``~/.navig/debug.log``) can surface root causes.

Targets
-------
1. ``navig.cli._singletons.set_no_cache()``   — config-layer failure path
2. ``navig.onboarding.steps`` module          — ``_log`` logger present (smoke)
"""

from __future__ import annotations

import importlib
import logging
import sys
import unittest.mock as mock

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helper — reload the singletons module in isolation
# ---------------------------------------------------------------------------

def _fresh_singletons_module():
    """Return a freshly-imported instance of navig.cli._singletons.

    We reload so that patched imports inside the module do not bleed across
    test cases.
    """
    mod_name = "navig.cli._singletons"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


# ===========================================================================
# 1. _singletons.set_no_cache()
# ===========================================================================

class TestSetNoCacheNoExceptionLeak:
    """set_no_cache(True) must never raise, even when the config layer blows up."""

    def test_returns_none_when_config_ok(self):
        singletons = _fresh_singletons_module()
        with mock.patch("navig.config.set_config_cache_bypass"), \
             mock.patch("navig.config.reset_config_manager"):
            result = singletons.set_no_cache(True)
        assert result is None  # always None

    def test_no_exception_when_set_config_cache_bypass_raises(self):
        singletons = _fresh_singletons_module()
        with mock.patch(
            "navig.config.set_config_cache_bypass",
            side_effect=RuntimeError("boom"),
        ), mock.patch("navig.config.reset_config_manager"):
            # Must not raise
            singletons.set_no_cache(True)

    def test_no_exception_when_reset_config_manager_raises(self):
        singletons = _fresh_singletons_module()
        with mock.patch("navig.config.set_config_cache_bypass"), \
             mock.patch(
                 "navig.config.reset_config_manager",
                 side_effect=ImportError("missing"),
             ):
            singletons.set_no_cache(True)

    def test_no_exception_when_import_inside_try_fails(self):
        singletons = _fresh_singletons_module()
        # Simulate navig.config not importable
        with mock.patch.dict(sys.modules, {"navig.config": None}):
            # Must not raise
            singletons.set_no_cache(True)

    def test_no_cache_flag_set_even_when_config_layer_fails(self):
        singletons = _fresh_singletons_module()
        with mock.patch(
            "navig.config.set_config_cache_bypass",
            side_effect=RuntimeError("boom"),
        ), mock.patch("navig.config.reset_config_manager"):
            singletons.set_no_cache(True)
        assert singletons._NO_CACHE is True

    def test_no_cache_false_path_does_not_call_config(self):
        """set_no_cache(False) only flips the flag; never touches the config layer."""
        singletons = _fresh_singletons_module()
        with mock.patch("navig.config.set_config_cache_bypass") as mock_bypass, \
             mock.patch("navig.config.reset_config_manager") as mock_reset:
            singletons.set_no_cache(False)
        mock_bypass.assert_not_called()
        mock_reset.assert_not_called()
        assert singletons._NO_CACHE is False

    def test_debug_log_emitted_on_config_failure(self):
        singletons = _fresh_singletons_module()
        with mock.patch.object(singletons._log, "debug") as mock_debug:
            with mock.patch(
                "navig.config.set_config_cache_bypass",
                side_effect=RuntimeError("forced failure"),
            ), mock.patch("navig.config.reset_config_manager"):
                singletons.set_no_cache(True)
        mock_debug.assert_called_once()
        call_str = str(mock_debug.call_args)
        assert "set_no_cache" in call_str, (
            f"Expected 'set_no_cache' in debug log call args, got: {call_str!r}"
        )

    def test_debug_log_contains_exception_text(self):
        singletons = _fresh_singletons_module()
        with mock.patch.object(singletons._log, "debug") as mock_debug:
            with mock.patch(
                "navig.config.set_config_cache_bypass",
                side_effect=RuntimeError("distinctive-error-xyz"),
            ), mock.patch("navig.config.reset_config_manager"):
                singletons.set_no_cache(True)
        full_call = str(mock_debug.call_args)
        assert "distinctive-error-xyz" in full_call, (
            f"Exception text should appear in debug log call, got: {full_call!r}"
        )


# ===========================================================================
# 2. navig.onboarding.steps — module-level _log present (smoke)
# ===========================================================================

class TestStepsModuleHasLogger:
    """Smoke test: steps.py now has a module-level _log Logger.

    The bare ``except Exception: pass`` in the web-search-provider run() path
    has been replaced with ``_log.debug(...)``.  This test verifies the logger
    is reachable at module level so the debug call will work at runtime.
    """

    def test_log_attribute_exists(self):
        import navig.onboarding.steps as steps_mod
        assert hasattr(steps_mod, "_log"), (
            "navig.onboarding.steps must expose a module-level '_log' logger"
        )

    def test_log_is_logger_instance(self):
        import navig.onboarding.steps as steps_mod
        assert isinstance(steps_mod._log, logging.Logger), (
            f"steps._log should be a logging.Logger, got {type(steps_mod._log)}"
        )

    def test_log_name_matches_module(self):
        import navig.onboarding.steps as steps_mod
        assert steps_mod._log.name == "navig.onboarding.steps"
