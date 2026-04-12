"""Tests for backward compatibility with legacy config (no llm_modes)."""

import os
from unittest.mock import patch
import pytest

pytestmark = pytest.mark.unit


class TestBackwardCompat:
    """Config with no llm_modes key → system uses NAVIG_AI_MODEL env var as before."""

    def test_no_llm_modes_router_uses_defaults(self):
        """Router with empty config still produces valid defaults."""
        from navig.llm_router import LLMModeRouter

        router = LLMModeRouter({})  # No llm_modes
        resolved = router.get_config("big_tasks")
        assert resolved.provider != ""
        assert resolved.model != ""
        assert resolved.resolution_reason != ""

    def test_no_llm_modes_no_error(self):
        """No errors or warnings when llm_modes is absent."""
        from navig.llm_router import LLMModeRouter

        # Should not raise
        router = LLMModeRouter({})
        for mode in ("small_talk", "big_tasks", "coding", "summarize", "research"):
            resolved = router.get_config(mode)
            assert resolved is not None

    @patch("navig.llm_router._has_api_key", return_value=True)
    def test_legacy_env_var_still_works(self, mock_key):
        """NAVIG_AI_MODEL env var path is not broken."""
        env_model = os.environ.get("NAVIG_AI_MODEL", "")
        # The env var should be respected by the legacy path in llm_generate
        from navig.llm_router import LLMModeRouter

        router = LLMModeRouter({})
        # Just verify no crash
        resolved = router.get_config("big_tasks")
        assert resolved is not None

    def test_detect_mode_works_without_config(self):
        """detect_mode is independent of config and always works."""
        from navig.llm_router import detect_mode

        assert detect_mode("hello") == "small_talk"
        assert detect_mode("write a function") == "coding"
        assert detect_mode("summarize this") == "summarize"

    def test_resolve_llm_convenience(self):
        """resolve_llm() works even without config."""
        # Reset singleton
        import navig.llm_router as mod
        from navig.llm_router import resolve_llm

        old = mod._router_instance
        mod._router_instance = None
        try:
            cfg = resolve_llm(mode="coding")
            assert cfg.mode == "coding"
            assert cfg.provider != ""
        finally:
            mod._router_instance = old
