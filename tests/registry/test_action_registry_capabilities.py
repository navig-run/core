"""Tests for agent/action_registry.py and routing/capabilities.py."""
from __future__ import annotations

import asyncio

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# agent/action_registry.py — ActionRegistry + _chk
# ──────────────────────────────────────────────────────────────────────────────
from navig.agent.action_registry import ActionRegistry, _chk


class TestChk:
    def test_passes_through_normal_result(self):
        assert _chk(42) == 42

    def test_passes_through_string(self):
        assert _chk("ok") == "ok"

    def test_object_without_success_passes_through(self):
        obj = object()
        assert _chk(obj) is obj

    def test_object_with_success_true_passes_through(self):
        class R:
            success = True

        assert isinstance(_chk(R()), R)

    def test_object_with_success_false_raises(self):
        class R:
            success = False
            stderr = "something failed"

        with pytest.raises(RuntimeError, match="something failed"):
            _chk(R())


class TestActionRegistry:
    def _registry(self) -> ActionRegistry:
        return ActionRegistry()

    def test_empty_on_init(self):
        r = self._registry()
        assert len(r) == 0

    def test_register_and_is_registered(self):
        r = self._registry()

        @r.register("my.action")
        async def _handler(params):
            return "done"

        assert r.is_registered("my.action")

    def test_unknown_id_not_registered(self):
        r = self._registry()
        assert not r.is_registered("nonexistent")

    def test_len_increases(self):
        r = self._registry()

        @r.register("a")
        async def _a(p):
            return 1

        @r.register("b")
        async def _b(p):
            return 2

        assert len(r) == 2

    def test_known_ids_returns_frozenset(self):
        r = self._registry()

        @r.register("foo")
        async def _foo(p):
            return None

        assert isinstance(r.known_ids(), frozenset)
        assert "foo" in r.known_ids()

    def test_requires_params_ids_empty_by_default(self):
        r = self._registry()

        @r.register("bar")
        async def _bar(p):
            return None

        assert "bar" not in r.requires_params_ids()

    def test_requires_params_flag(self):
        r = self._registry()

        @r.register("needs_params", requires_params=True)
        async def _handler(p):
            return None

        assert "needs_params" in r.requires_params_ids()

    def test_dispatch_unknown_returns_false_none(self):
        r = self._registry()
        matched, result = asyncio.run(r.dispatch("ghost.action", {}))
        assert matched is False
        assert result is None

    def test_dispatch_known_returns_true_and_result(self):
        r = self._registry()

        @r.register("compute")
        async def _compute(params):
            return params["x"] + params["y"]

        matched, result = asyncio.run(r.dispatch("compute", {"x": 3, "y": 4}))
        assert matched is True
        assert result == 7

    def test_dispatch_handler_receives_params(self):
        r = self._registry()
        received = {}

        @r.register("capture")
        async def _capture(params):
            received.update(params)
            return "captured"

        asyncio.run(r.dispatch("capture", {"key": "value"}))
        assert received == {"key": "value"}

    def test_overwrite_replaces_handler(self):
        r = self._registry()

        @r.register("dup")
        async def _first(p):
            return "first"

        @r.register("dup")
        async def _second(p):
            return "second"

        # second handler replaced first — len stays 1, dispatch returns "second"
        assert len(r) == 1
        _, result = asyncio.run(r.dispatch("dup", {}))
        assert result == "second"


# ──────────────────────────────────────────────────────────────────────────────
# routing/capabilities.py — CAPABILITY_TAGS, ModeProfile, MODE_CAPABILITIES
# ──────────────────────────────────────────────────────────────────────────────
from navig.routing.capabilities import CAPABILITY_TAGS, MODE_CAPABILITIES, ModeProfile


class TestCapabilityTags:
    def test_is_frozenset(self):
        assert isinstance(CAPABILITY_TAGS, frozenset)

    def test_contains_fast(self):
        assert "fast" in CAPABILITY_TAGS

    def test_contains_strong(self):
        assert "strong" in CAPABILITY_TAGS

    def test_contains_coder(self):
        assert "coder" in CAPABILITY_TAGS

    def test_contains_long_context(self):
        assert "long_context" in CAPABILITY_TAGS

    def test_non_empty(self):
        assert len(CAPABILITY_TAGS) > 0


class TestModeProfile:
    def test_required_is_frozenset(self):
        mp = ModeProfile(required={"fast"})
        assert isinstance(mp.required, frozenset)

    def test_preferred_is_frozenset(self):
        mp = ModeProfile(required={"fast"})
        assert isinstance(mp.preferred, frozenset)

    def test_score_model_missing_required(self):
        mp = ModeProfile(required={"strong"})
        score = mp.score_model(frozenset({"fast"}))
        assert score == -1

    def test_score_model_meets_required_no_preferred(self):
        mp = ModeProfile(required={"fast"}, preferred=set())
        score = mp.score_model(frozenset({"fast"}))
        assert score == 0

    def test_score_model_preferred_bonus(self):
        mp = ModeProfile(required={"fast"}, preferred={"strong", "coder"})
        score = mp.score_model(frozenset({"fast", "strong"}))
        assert score == 1

    def test_score_model_all_preferred(self):
        mp = ModeProfile(required={"fast"}, preferred={"strong", "coder"})
        score = mp.score_model(frozenset({"fast", "strong", "coder"}))
        assert score == 2

    def test_default_cost_latency(self):
        mp = ModeProfile(required=set())
        assert mp.cost_target == "medium"
        assert mp.latency_target == "medium"


class TestModeCapabilities:
    def test_is_dict(self):
        assert isinstance(MODE_CAPABILITIES, dict)

    def test_coding_requires_coder(self):
        assert "coder" in MODE_CAPABILITIES["coding"].required

    def test_big_tasks_requires_strong(self):
        assert "strong" in MODE_CAPABILITIES["big_tasks"].required

    def test_all_entries_are_mode_profiles(self):
        for name, profile in MODE_CAPABILITIES.items():
            assert isinstance(profile, ModeProfile), f"{name} is not ModeProfile"

    def test_small_talk_requires_fast(self):
        assert "fast" in MODE_CAPABILITIES["small_talk"].required

    def test_modes_not_empty(self):
        assert len(MODE_CAPABILITIES) > 0
