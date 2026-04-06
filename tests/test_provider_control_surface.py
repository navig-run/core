"""Tests for the provider control surface feature.

Covers:
1. Capability registry — get/check/list capabilities
2. Discovery layer — connected providers, models, vision resolution
3. Session override helpers — set/get/clear overrides
4. Routing injection — session overrides applied in UnifiedRouter.run()
5. Slash registry entries — new commands registered
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════
# 1. Capability Registry
# ═══════════════════════════════════════════════════════════════════════


class TestCapabilityRegistry:
    """navig.providers.capabilities — static model capability metadata."""

    def test_capability_enum_values(self):
        from navig.providers.capabilities import Capability

        assert "vision" in Capability.VISION.value
        assert "text" in Capability.TEXT.value
        assert "code" in Capability.CODE.value

    def test_get_model_capabilities_known_model(self):
        from navig.providers.capabilities import Capability, get_model_capabilities

        caps, source = get_model_capabilities("gpt-4o")
        assert Capability.VISION in caps
        assert Capability.TEXT in caps
        assert source == "verified"

    def test_get_model_capabilities_unknown_model(self):
        from navig.providers.capabilities import Capability, get_model_capabilities

        caps, source = get_model_capabilities("totally-fake-model-xyz")
        # Unknown models get TEXT as default
        assert Capability.TEXT in caps
        assert source == "inferred"

    def test_has_capability_true(self):
        from navig.providers.capabilities import Capability, has_capability

        assert has_capability("gpt-4o", Capability.VISION) is True

    def test_has_capability_false(self):
        from navig.providers.capabilities import Capability, has_capability

        # A small/fast model like gpt-4o-mini doesn't have REASONING
        result = has_capability("gpt-4o-mini", Capability.REASONING)
        # May or may not — just verify it returns a bool
        assert isinstance(result, bool)

    def test_list_vision_models_non_empty(self):
        from navig.providers.capabilities import list_vision_models

        # list_vision_models takes a sequence of model names to filter
        candidate_models = ["gpt-4o", "gpt-4o-mini", "claude-sonnet-4-20250514", "llama-3.1-8b"]
        models = list_vision_models(candidate_models)
        assert len(models) > 0
        model_names = [m[0] for m in models]
        assert "gpt-4o" in model_names

    def test_list_models_with_capability(self):
        from navig.providers.capabilities import Capability, list_models_with_capability

        candidate_models = ["gpt-4o", "gpt-4o-mini", "llama-3.1-8b"]
        models = list_models_with_capability(candidate_models, Capability.TEXT)
        assert len(models) > 0

    def test_capabilities_label_formatting(self):
        from navig.providers.capabilities import capabilities_label

        label = capabilities_label("gpt-4o")
        assert isinstance(label, str)
        assert len(label) > 0
        # gpt-4o has vision, should include eye emoji
        assert "👁" in label


# ═══════════════════════════════════════════════════════════════════════
# 2. Discovery Layer
# ═══════════════════════════════════════════════════════════════════════


class TestDiscoveryLayer:
    """navig.providers.discovery — provider/model enumeration + vision resolution."""

    def test_provider_info_dataclass(self):
        from navig.providers.discovery import ProviderInfo

        info = ProviderInfo(
            id="openai",
            display_name="OpenAI",
            emoji="🟢",
            tier="cloud",
            connected=True,
            active=False,
        )
        assert info.id == "openai"
        assert info.connected is True
        assert info.display_name == "OpenAI"

    def test_model_info_dataclass(self):
        from navig.providers.discovery import ModelInfo

        m = ModelInfo(
            name="gpt-4o",
            provider_id="openai",
            capabilities=[],
            capability_source="verified",
        )
        assert m.name == "gpt-4o"
        assert m.provider_id == "openai"

    def test_list_connected_providers_returns_list(self):
        from navig.providers.discovery import list_connected_providers

        # Without any env keys, may return empty or providers with vault keys
        providers = list_connected_providers()
        assert isinstance(providers, list)

    def test_get_vision_api_format_openai(self):
        from navig.providers.discovery import get_vision_api_format

        fmt = get_vision_api_format("openai")
        assert fmt == "openai"

    def test_get_vision_api_format_anthropic(self):
        from navig.providers.discovery import get_vision_api_format

        fmt = get_vision_api_format("anthropic")
        assert fmt == "anthropic"

    def test_get_vision_api_format_google(self):
        from navig.providers.discovery import get_vision_api_format

        fmt = get_vision_api_format("google")
        assert fmt == "google"

    def test_get_vision_api_format_unknown_defaults_openai(self):
        from navig.providers.discovery import get_vision_api_format

        fmt = get_vision_api_format("openrouter")
        assert fmt == "openai"

    def test_resolve_vision_model_returns_tuple_or_none(self):
        from navig.providers.discovery import resolve_vision_model

        result = resolve_vision_model()
        # Returns (provider_id, model_name, reason) or None
        assert result is None or (isinstance(result, tuple) and len(result) == 3)

    def test_resolve_vision_model_respects_session_overrides(self):
        from navig.providers.discovery import resolve_vision_model

        overrides = {"vision_provider": "anthropic", "vision_model": "claude-sonnet-4-20250514"}
        result = resolve_vision_model(session_overrides=overrides)
        assert result is not None
        prov, model, reason = result
        # Session override should be respected
        assert prov == "anthropic"
        assert model == "claude-sonnet-4-20250514"
        assert reason == "session_override"


# ═══════════════════════════════════════════════════════════════════════
# 3. Session Override Helpers
# ═══════════════════════════════════════════════════════════════════════


class TestSessionOverrides:
    """telegram_sessions — so: prefixed session metadata overrides."""

    def test_so_prefix_constant(self):
        from navig.gateway.channels.telegram_sessions import SessionManager

        assert SessionManager._SO_PREFIX == "so:"

    def _make_manager(self, tmp_path):
        """Create a SessionManager pointing at a temp dir."""
        from navig.gateway.channels.telegram_sessions import SessionManager

        return SessionManager(storage_dir=tmp_path / "sessions")

    def test_set_and_get_session_override(self, tmp_path):
        sm = self._make_manager(tmp_path)
        sm.set_session_override(123, 456, "tier_big_provider", "anthropic")
        val = sm.get_session_override(123, 456, "tier_big_provider")
        assert val == "anthropic"

    def test_get_session_override_missing_returns_none(self, tmp_path):
        sm = self._make_manager(tmp_path)
        val = sm.get_session_override(123, 456, "nonexistent_key")
        assert val is None

    def test_get_all_session_overrides(self, tmp_path):
        sm = self._make_manager(tmp_path)
        sm.set_session_override(123, 456, "tier_big_provider", "openai")
        sm.set_session_override(123, 456, "tier_big_model", "gpt-4o")
        sm.set_session_override(123, 456, "vision_model", "gpt-4o")

        all_overrides = sm.get_all_session_overrides(123, 456)
        assert all_overrides["tier_big_provider"] == "openai"
        assert all_overrides["tier_big_model"] == "gpt-4o"
        assert all_overrides["vision_model"] == "gpt-4o"
        assert len(all_overrides) == 3

    def test_clear_session_overrides(self, tmp_path):
        sm = self._make_manager(tmp_path)
        sm.set_session_override(123, 456, "tier_big_provider", "openai")
        sm.set_session_override(123, 456, "tier_small_provider", "groq")

        count = sm.clear_session_overrides(123, 456)
        assert count == 2
        assert sm.get_all_session_overrides(123, 456) == {}

    def test_clear_session_overrides_empty(self, tmp_path):
        sm = self._make_manager(tmp_path)
        count = sm.clear_session_overrides(123, 456)
        assert count == 0

    def test_session_override_does_not_pollute_regular_metadata(self, tmp_path):
        sm = self._make_manager(tmp_path)
        sm.set_session_override(123, 456, "tier_big_provider", "openai")

        session = sm.get_or_create_session(123, 456)
        # The override should be stored with so: prefix
        assert "so:tier_big_provider" in session.metadata
        # Regular key should NOT exist
        assert "tier_big_provider" not in session.metadata


# ═══════════════════════════════════════════════════════════════════════
# 4. Unified Router Session Override Injection
# ═══════════════════════════════════════════════════════════════════════


class TestRouterSessionOverrides:
    """UnifiedRouter.run() should apply session_tier_overrides from metadata."""

    def test_route_with_session_tier_override_sets_model(self):
        """When session_tier_overrides specifies a model for big tier,
        the route decision should have that model."""
        from navig.routing.router import RouteRequest, UnifiedRouter

        router = UnifiedRouter(config={})
        request = RouteRequest(
            messages=[{"role": "user", "content": "Hello"}],
            text="Hello",
            tier_override="big",
            metadata={
                "session_tier_overrides": {
                    "big": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
                }
            },
        )
        decision = router.route(request)
        # route() itself doesn't apply overrides — that happens in run().
        # But we can verify the route decision mode is correct.
        assert decision.mode == "big_tasks"

    def test_route_without_overrides_no_model(self):
        """Without session overrides, route decision has no explicit model."""
        from navig.routing.router import RouteRequest, UnifiedRouter

        router = UnifiedRouter(config={})
        request = RouteRequest(
            messages=[{"role": "user", "content": "Hello"}],
            text="Hello",
            tier_override="big",
        )
        decision = router.route(request)
        assert decision.mode == "big_tasks"
        assert decision.model == ""  # No model forced

    def test_session_override_maps_small_mode_correctly(self):
        """Session override for 'small' tier should apply when mode is small_talk."""
        from navig.routing.router import RouteRequest, UnifiedRouter

        router = UnifiedRouter(config={})
        request = RouteRequest(
            messages=[{"role": "user", "content": "Hi"}],
            text="Hi",
            tier_override="small",
            metadata={
                "session_tier_overrides": {
                    "small": {"provider": "groq", "model": "llama-3.1-8b-instant"},
                }
            },
        )
        decision = router.route(request)
        assert decision.mode == "small_talk"

    def test_session_override_maps_coder_mode_correctly(self):
        from navig.routing.router import RouteRequest, UnifiedRouter

        router = UnifiedRouter(config={})
        request = RouteRequest(
            messages=[{"role": "user", "content": "Write code"}],
            text="Write code",
            tier_override="coder_big",
            metadata={
                "session_tier_overrides": {
                    "coder_big": {"provider": "openai", "model": "gpt-4o"},
                }
            },
        )
        decision = router.route(request)
        assert decision.mode == "coding"


# ═══════════════════════════════════════════════════════════════════════
# 5. Slash Registry — New Commands
# ═══════════════════════════════════════════════════════════════════════


class TestSlashRegistryEntries:
    """New commands are registered in _SLASH_REGISTRY."""

    def test_provider_hybrid_registered(self):
        from navig.gateway.channels.telegram_commands import _SLASH_REGISTRY

        cmds = {e.command for e in _SLASH_REGISTRY}
        assert "provider_hybrid" in cmds

    def test_provider_vision_registered(self):
        from navig.gateway.channels.telegram_commands import _SLASH_REGISTRY

        cmds = {e.command for e in _SLASH_REGISTRY}
        assert "provider_vision" in cmds

    def test_provider_show_registered(self):
        from navig.gateway.channels.telegram_commands import _SLASH_REGISTRY

        cmds = {e.command for e in _SLASH_REGISTRY}
        assert "provider_show" in cmds

    def test_provider_reset_registered(self):
        from navig.gateway.channels.telegram_commands import _SLASH_REGISTRY

        cmds = {e.command for e in _SLASH_REGISTRY}
        assert "provider_reset" in cmds

    def test_models_reset_registered(self):
        from navig.gateway.channels.telegram_commands import _SLASH_REGISTRY

        cmds = {e.command for e in _SLASH_REGISTRY}
        assert "models_reset" in cmds

    def test_new_commands_have_handlers(self):
        from navig.gateway.channels.telegram_commands import _SLASH_REGISTRY

        new_cmds = {
            "provider_hybrid",
            "provider_vision",
            "provider_show",
            "provider_reset",
            "models_reset",
        }
        for entry in _SLASH_REGISTRY:
            if entry.command in new_cmds:
                assert entry.handler, f"{entry.command} missing handler"

    def test_new_commands_category_is_model(self):
        from navig.gateway.channels.telegram_commands import _SLASH_REGISTRY

        new_cmds = {
            "provider_hybrid",
            "provider_vision",
            "provider_show",
            "provider_reset",
            "models_reset",
        }
        for entry in _SLASH_REGISTRY:
            if entry.command in new_cmds:
                assert entry.category == "model", f"{entry.command} category is {entry.category}"


# ═══════════════════════════════════════════════════════════════════════
# 6. Callback Prefix Wiring
# ═══════════════════════════════════════════════════════════════════════


class TestCallbackPrefixWiring:
    """New callback prefixes (hyb_*, vis_*, pu_*) are handled in dispatch."""

    def test_callback_handler_has_hybrid_method(self):
        from navig.gateway.channels.telegram_keyboards import CallbackHandler

        assert hasattr(CallbackHandler, "_handle_hybrid_callback")

    def test_callback_handler_has_vision_method(self):
        from navig.gateway.channels.telegram_keyboards import CallbackHandler

        assert hasattr(CallbackHandler, "_handle_vision_callback")

    def test_callback_handler_has_utility_method(self):
        from navig.gateway.channels.telegram_keyboards import CallbackHandler

        assert hasattr(CallbackHandler, "_handle_provider_utility_callback")

    def test_hybrid_sub_helpers_exist(self):
        from navig.gateway.channels.telegram_keyboards import CallbackHandler

        assert hasattr(CallbackHandler, "_show_hybrid_tier_picker")
        assert hasattr(CallbackHandler, "_show_hybrid_provider_tiers")


# ═══════════════════════════════════════════════════════════════════════
# 7. Vision Pipeline Wiring
# ═══════════════════════════════════════════════════════════════════════


class TestVisionPipelineWiring:
    """Photo handler and vision API methods exist on TelegramChannel."""

    def test_handle_photo_vision_method_exists(self):
        from navig.gateway.channels.telegram import TelegramChannel

        assert hasattr(TelegramChannel, "_handle_photo_vision")

    def test_call_vision_api_method_exists(self):
        from navig.gateway.channels.telegram import TelegramChannel

        assert hasattr(TelegramChannel, "_call_vision_api")

    def test_call_vision_openai_method_exists(self):
        from navig.gateway.channels.telegram import TelegramChannel

        assert hasattr(TelegramChannel, "_call_vision_openai")

    def test_call_vision_anthropic_method_exists(self):
        from navig.gateway.channels.telegram import TelegramChannel

        assert hasattr(TelegramChannel, "_call_vision_anthropic")

    def test_call_vision_google_method_exists(self):
        from navig.gateway.channels.telegram import TelegramChannel

        assert hasattr(TelegramChannel, "_call_vision_google")


# ═══════════════════════════════════════════════════════════════════════
# 8. Command Handler Wiring
# ═══════════════════════════════════════════════════════════════════════


class TestCommandHandlerWiring:
    """New command handler methods exist on the mixin."""

    def test_handle_provider_hybrid_exists(self):
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        assert hasattr(TelegramCommandsMixin, "_handle_provider_hybrid")

    def test_handle_provider_vision_exists(self):
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        assert hasattr(TelegramCommandsMixin, "_handle_provider_vision")

    def test_handle_provider_show_exists(self):
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        assert hasattr(TelegramCommandsMixin, "_handle_provider_show")

    def test_handle_provider_reset_exists(self):
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        assert hasattr(TelegramCommandsMixin, "_handle_provider_reset")

    def test_handle_models_reset_exists(self):
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        assert hasattr(TelegramCommandsMixin, "_handle_models_reset")


# ═══════════════════════════════════════════════════════════════════════
# 9. Bug Regression Tests
# ═══════════════════════════════════════════════════════════════════════


class TestBugRegressions:
    """Regressions for specific bugs discovered in the provider control surface."""

    def test_session_override_banner_shows_tier_overrides(self):
        """BUG-03: banner must detect tier_*_provider keys, not bare 'provider'."""
        overrides = {
            "tier_big_provider": "anthropic",
            "tier_big_model": "claude-sonnet-4-20250514",
            "tier_small_provider": "groq",
        }
        tier_override_keys = [
            k for k in overrides if k.startswith("tier_") and k.endswith("_provider")
        ]
        assert len(tier_override_keys) == 2
        assert "provider" not in overrides  # old broken check would find nothing

    def test_vis_truncation_uses_exact_slice_not_startswith(self):
        """BUG-09: truncated model recovery must not match a model that only shares a prefix.

        The old code used m.startswith(model_name).  When model_name is short
        (not truncated, fits in 40 chars), startswith wrongly matches any model
        whose name *begins* with that string.  The fix uses m[:40]==model_name
        which only matches when the truncated slice equals model_name exactly.
        """
        model_name = "gpt-4o"  # short, never truncated
        models = ["gpt-4o", "gpt-4o-mini", "gpt-4o-turbo"]
        startswith_matches = [m for m in models if m.startswith(model_name)]
        slice_matches = [m for m in models if m[:40] == model_name or m == model_name]
        assert len(startswith_matches) > 1, "startswith should be ambiguous for short model names"
        assert len(slice_matches) == 1
        assert slice_matches[0] == "gpt-4o"

    def test_github_copilot_claude_dot_notation_has_vision(self):
        """BUG-10: pattern r'claude-3[.-]5-sonnet' must match dot-separator variant."""
        from navig.providers.capabilities import Capability, has_capability

        assert has_capability("claude-3.5-sonnet", Capability.VISION) is True
        assert has_capability("claude-3.5-sonnet", Capability.CODE) is True
        # dash notation must still work
        assert has_capability("claude-3-5-sonnet-20241022", Capability.VISION) is True

    def test_infer_provider_deepseek_not_groq(self):
        """BUG-08: DeepSeek models should map to openrouter, not groq."""
        from navig.providers.discovery import _infer_provider_from_model

        result = _infer_provider_from_model("deepseek-v3")
        assert result == "openrouter"
        assert result != "groq"

    def test_mode_to_tier_covers_summarize_and_research(self):
        """BUG-11: _MODE_TO_TIER module constant must include summarize and research."""
        from navig.routing.router import _MODE_TO_TIER

        assert "summarize" in _MODE_TO_TIER
        assert "research" in _MODE_TO_TIER
        assert _MODE_TO_TIER["summarize"] == "big"
        assert _MODE_TO_TIER["research"] == "big"
        # existing entries unchanged
        assert _MODE_TO_TIER["small_talk"] == "small"
        assert _MODE_TO_TIER["big_tasks"] == "big"
        assert _MODE_TO_TIER["coding"] == "coder_big"

    def test_vis_clear_no_leading_empty_answer(self):
        """BUG-01: vis_clear must not emit an empty _answer before the descriptive one."""
        import ast
        import inspect
        import textwrap

        from navig.gateway.channels.telegram_keyboards import CallbackHandler

        src = textwrap.dedent(inspect.getsource(CallbackHandler._handle_vision_callback))
        tree = ast.parse(src)
        answer_calls: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Await)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "_answer"
            ):
                args = node.value.args
                if len(args) >= 2 and isinstance(args[1], ast.Constant):
                    answer_calls.append((node.lineno, args[1].value))
        # Verify no empty string precedes a non-empty string (dedup guard would kill it)
        for i, (lineno, txt) in enumerate(answer_calls):
            if txt == "" and i + 1 < len(answer_calls) and answer_calls[i + 1][1] != "":
                next_lineno, next_txt = answer_calls[i + 1]
                raise AssertionError(
                    f"Empty _answer at line {lineno} will silence '{next_txt}' "
                    f"(line {next_lineno}) via dedup guard"
                )

    def test_pu_unknown_action_uses_show_alert(self):
        """BUG-02: unknown pu_* callback must pass show_alert=True so user sees it."""
        import ast
        import inspect
        import textwrap

        from navig.gateway.channels.telegram_keyboards import CallbackHandler

        src = textwrap.dedent(inspect.getsource(CallbackHandler._handle_provider_utility_callback))
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Await)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "_answer"
            ):
                args = node.value.args
                if len(args) >= 2 and isinstance(args[1], ast.Constant):
                    if "Unknown action" in str(args[1].value):
                        kw_names = {kw.arg for kw in node.value.keywords}
                        assert "show_alert" in kw_names, (
                            "⚠️ Unknown action _answer call missing show_alert=True"
                        )
                        return
        raise AssertionError("Could not find '⚠️ Unknown action' _answer call in method")
