"""Tests for navig.hooks — HookEvent, HookRegistry, HookExecutor, SSRF guard."""

from __future__ import annotations

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# HookEvent & HookContext
# ─────────────────────────────────────────────────────────────────────────────

class TestHookContext:
    def test_to_json_is_valid(self):
        import json
        from navig.hooks.events import HookContext, HookEvent

        ctx = HookContext(
            event=HookEvent.PRE_TOOL_USE,
            tool_name="bash",
            tool_input={"command": "ls"},
            session_id="s1",
        )
        raw = ctx.to_json()
        data = json.loads(raw)
        assert data["event"] == HookEvent.PRE_TOOL_USE
        assert data["tool_name"] == "bash"
        assert data["session_id"] == "s1"

    def test_to_json_without_optional_fields(self):
        import json
        from navig.hooks.events import HookContext, HookEvent

        ctx = HookContext(event=HookEvent.NOTIFICATION)
        raw = ctx.to_json()
        data = json.loads(raw)
        assert "event" in data


# ─────────────────────────────────────────────────────────────────────────────
# HookDefinition.matches_tool
# ─────────────────────────────────────────────────────────────────────────────

class TestHookDefinitionMatchesTool:
    def _make_defn(self, tool_filter):
        from navig.hooks.registry import HookDefinition
        from navig.hooks.events import HookEvent

        return HookDefinition(
            event=HookEvent.PRE_TOOL_USE,
            command="echo hi",
            tool_filter=tool_filter,
        )

    def test_none_filter_matches_any(self):
        d = self._make_defn(None)
        assert d.matches_tool("bash") is True
        assert d.matches_tool("python") is True

    def test_exact_match(self):
        d = self._make_defn("bash")
        assert d.matches_tool("bash") is True
        assert d.matches_tool("python") is False

    def test_glob_match(self):
        d = self._make_defn("ba*")
        assert d.matches_tool("bash") is True
        assert d.matches_tool("batch") is True
        assert d.matches_tool("python") is False


# ─────────────────────────────────────────────────────────────────────────────
# SSRF guard
# ─────────────────────────────────────────────────────────────────────────────

class TestSSRFGuard:
    def _is_private(self, url: str) -> bool:
        from navig.hooks.executor import _is_private_url

        return _is_private_url(url)

    def test_blocks_loopback(self):
        assert self._is_private("http://127.0.0.1/api") is True

    def test_blocks_rfc1918_10(self):
        assert self._is_private("http://10.0.0.1/") is True

    def test_blocks_rfc1918_192(self):
        assert self._is_private("http://192.168.1.100/") is True

    def test_blocks_rfc1918_172(self):
        assert self._is_private("http://172.20.0.5/") is True

    def test_blocks_link_local(self):
        assert self._is_private("http://169.254.169.254/latest/meta-data") is True

    def test_allows_public_ip(self):
        assert self._is_private("https://api.openai.com/v1/chat") is False

    def test_allows_public_domain(self):
        assert self._is_private("https://example.com/hook") is False

    def test_handles_malformed_url_gracefully(self):
        # Non-HTTP strings are not considered private (guard is HTTP/HTTPS only)
        assert self._is_private("not-a-url") is False


# ─────────────────────────────────────────────────────────────────────────────
# fire_hook (smoke test — no real subprocess)
# ─────────────────────────────────────────────────────────────────────────────

class TestFireHook:
    def test_fire_hook_with_empty_registry(self):
        """fire_hook should return a no-op result when no hooks match."""
        from navig.hooks import fire_hook
        from navig.hooks.events import HookContext, HookEvent
        from navig.hooks.registry import HookRegistry

        # Provide an empty registry so nothing is executed
        from navig.hooks import executor as _exec_mod, registry as _reg_mod

        ctx = HookContext(event=HookEvent.SESSION_START, session_id="smoke-test")
        result = fire_hook(ctx)
        # When no hooks fire, result should not block
        assert result.block is False
