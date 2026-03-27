"""Tests for navig.agent.model_router — HybridRouter, heuristic_route, config."""

import pytest

from navig.agent.model_router import (
    HybridRouter,
    ModelRouter,
    ModelSlot,
    RoutingConfig,
    RoutingDecision,
    heuristic_route,
    needs_fallback,
    pick_fallback_tier,
)

# ---- RoutingConfig ----


class TestRoutingConfig:
    def test_from_dict_new_schema(self):
        data = {
            "enabled": True,
            "mode": "rules_then_fallback",
            "models": {
                "small": {
                    "provider": "ollama",
                    "model": "qwen2.5:3b",
                    "defaults": {"max_tokens": 200},
                },
                "big": {
                    "provider": "openrouter",
                    "model": "gpt-4o",
                    "defaults": {"max_tokens": 4096},
                },
                "coder_big": {"provider": "openrouter", "model": "deepseek-coder"},
            },
        }
        cfg = RoutingConfig.from_dict(data)
        assert cfg.enabled is True
        assert cfg.mode == "rules_then_fallback"
        assert cfg.small.model == "qwen2.5:3b"
        assert cfg.big.model == "gpt-4o"
        assert cfg.coder_big.model == "deepseek-coder"
        assert cfg.is_active is True

    def test_from_dict_legacy_flat(self):
        data = {
            "mode": "heuristic",
            "small_model": "phi3:mini",
            "big_model": "gpt-4o-mini",
        }
        cfg = RoutingConfig.from_dict(data)
        assert cfg.mode == "rules_then_fallback"
        assert cfg.small.model == "phi3:mini"
        assert cfg.big.model == "gpt-4o-mini"

    def test_disabled_by_default(self):
        cfg = RoutingConfig.from_dict({})
        assert cfg.enabled is False
        assert cfg.is_active is False

    def test_slot_for_tier(self):
        cfg = RoutingConfig(
            small=ModelSlot(model="s"),
            big=ModelSlot(model="b"),
            coder_big=ModelSlot(model="c"),
        )
        assert cfg.slot_for_tier("small").model == "s"
        assert cfg.slot_for_tier("big").model == "b"
        assert cfg.slot_for_tier("coder_big").model == "c"
        assert cfg.slot_for_tier("unknown").model == "b"  # fallback to big


# ---- Heuristic routing ----


class TestHeuristicRoute:
    @pytest.fixture
    def cfg(self):
        return RoutingConfig(
            enabled=True,
            mode="rules_then_fallback",
            small=ModelSlot(provider="ollama", model="qwen:3b", max_tokens=200),
            big=ModelSlot(provider="openrouter", model="gpt-4o", max_tokens=4096),
            coder_big=ModelSlot(
                provider="openrouter", model="deepseek-coder", max_tokens=8192
            ),
        )

    def test_code_fence_routes_coder(self, cfg):
        d = heuristic_route("```python\nprint('hi')\n```", cfg)
        assert d.tier == "coder_big"
        assert "code_fence" in d.reason

    def test_stack_trace_routes_coder(self, cfg):
        d = heuristic_route("Traceback (most recent call last):\n  File test.py", cfg)
        assert d.tier == "coder_big"
        assert "stack_trace" in d.reason

    def test_code_keywords_route_coder(self, cfg):
        d = heuristic_route("write a function to parse JSON", cfg)
        assert d.tier == "coder_big"

    def test_debug_keyword_routes_coder(self, cfg):
        d = heuristic_route("debug the authentication module", cfg)
        assert d.tier == "coder_big"

    def test_planning_routes_big(self, cfg):
        d = heuristic_route("design a comprehensive migration strategy", cfg)
        assert d.tier == "big"

    def test_structured_output_routes_big(self, cfg):
        d = heuristic_route("create a json schema for the API response", cfg)
        assert d.tier == "big"

    def test_long_message_routes_big(self, cfg):
        long_msg = "This is a question. " * 30  # >350 chars
        d = heuristic_route(long_msg, cfg)
        assert d.tier == "big"
        assert "long_msg" in d.reason

    def test_short_chat_routes_small(self, cfg):
        d = heuristic_route("hey how are you?", cfg)
        assert d.tier == "small"
        assert d.reason == "simple_chat"

    def test_diff_fragment_routes_coder(self, cfg):
        d = heuristic_route("--- a/file.py\n+++ b/file.py\n@@ -10,3 +10,4 @@", cfg)
        assert d.tier == "coder_big"
        assert "diff_fragment" in d.reason


# ---- Fallback logic ----


class TestFallbackLogic:
    def test_needs_fallback_small_empty(self):
        assert needs_fallback("", "small") is True

    def test_needs_fallback_small_short(self):
        assert needs_fallback("ok", "small") is True

    def test_needs_fallback_small_low_confidence(self):
        assert needs_fallback("I'm not sure about this topic", "small") is True

    def test_no_fallback_for_big(self):
        assert needs_fallback("I'm not sure", "big") is False

    def test_no_fallback_good_response(self):
        assert (
            needs_fallback(
                "Here is the detailed answer to your question about Python.", "small"
            )
            is False
        )

    def test_pick_fallback_tier_code(self):
        assert pick_fallback_tier("small", "fix this code bug") == "coder_big"

    def test_pick_fallback_tier_general(self):
        assert pick_fallback_tier("small", "explain the concept") == "big"


# ---- HybridRouter ----


class TestHybridRouter:
    def test_inactive_routes_to_big(self):
        cfg = RoutingConfig(enabled=False, big=ModelSlot(model="fallback-model"))
        router = HybridRouter(cfg)
        assert router.is_active is False
        d = router.route("hello")
        assert d.reason == "single_mode"

    def test_tier_override(self):
        cfg = RoutingConfig(
            enabled=True,
            mode="rules_then_fallback",
            small=ModelSlot(model="s"),
            big=ModelSlot(model="b"),
            coder_big=ModelSlot(model="c"),
        )
        router = HybridRouter(cfg)
        d = router.route("hello", tier_override="coder_big")
        assert d.tier == "coder_big"
        assert d.model == "c"
        assert "user_override" in d.reason

    def test_status_summary(self):
        cfg = RoutingConfig(
            enabled=True,
            mode="rules_then_fallback",
            small=ModelSlot(provider="ollama", model="qwen:3b"),
            big=ModelSlot(provider="openrouter", model="gpt-4o"),
            coder_big=ModelSlot(provider="openrouter", model="deepseek-coder"),
        )
        router = HybridRouter(cfg)
        s = router.status_summary()
        assert s["enabled"] is True
        assert s["mode"] == "rules_then_fallback"
        assert s["models"]["small"]["model"] == "qwen:3b"

    def test_models_table(self):
        cfg = RoutingConfig(
            small=ModelSlot(provider="ollama", model="qwen:3b", max_tokens=200),
            big=ModelSlot(provider="openrouter", model="gpt-4o", max_tokens=4096),
            coder_big=ModelSlot(
                provider="openrouter", model="deepseek-coder", max_tokens=8192
            ),
        )
        router = HybridRouter(cfg)
        table = router.models_table()
        assert "qwen:3b" in table
        assert "gpt-4o" in table

    def test_backward_compat_alias(self):
        assert ModelRouter is HybridRouter

    def test_from_config(self):
        global_cfg = {
            "ai": {
                "routing": {
                    "mode": "rules_then_fallback",
                    "models": {
                        "small": {"provider": "ollama", "model": "qwen:3b"},
                        "big": {"provider": "openrouter", "model": "gpt-4o"},
                    },
                },
            },
        }
        router = HybridRouter.from_config(global_cfg)
        assert router.cfg.mode == "rules_then_fallback"
        assert router.cfg.small.model == "qwen:3b"


# ---- RoutingDecision ----


class TestRoutingDecision:
    def test_defaults(self):
        d = RoutingDecision(tier="small", model="test-model")
        assert d.provider == ""
        assert d.max_tokens == 512
        assert d.temperature == 0.7

    def test_custom_values(self):
        d = RoutingDecision(
            tier="big",
            model="gpt-4o",
            provider="openai",
            max_tokens=4096,
            temperature=0.5,
            reason="test",
        )
        assert d.reason == "test"
        assert d.max_tokens == 4096
