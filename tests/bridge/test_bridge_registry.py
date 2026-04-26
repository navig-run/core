"""Tests for navig.providers.bridge_registry — BridgeRegistry."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from navig.providers.bridge_registry import (
    BridgeRegistry,
    DynamicProvider,
    get_bridge_registry,
    reset_bridge_registry,
)


@pytest.fixture
def registry() -> BridgeRegistry:
    return BridgeRegistry()


class TestRegisterUnregister:
    def test_register_returns_provider(self, registry):
        p = registry.register("test", "http://localhost:8080/v1")
        assert isinstance(p, DynamicProvider)
        assert p.name == "test"
        assert p.url == "http://localhost:8080/v1"

    def test_get_after_register(self, registry):
        registry.register("mybridge", "http://127.0.0.1:42070/v1", priority=1)
        p = registry.get("mybridge")
        assert p is not None
        assert p.priority == 1

    def test_get_missing_returns_none(self, registry):
        assert registry.get("nonexistent") is None

    def test_unregister_returns_true(self, registry):
        registry.register("to_remove", "http://x/v1")
        assert registry.unregister("to_remove") is True

    def test_unregister_missing_returns_false(self, registry):
        assert registry.unregister("not_there") is False

    def test_unregister_removes_provider(self, registry):
        registry.register("bye", "http://x/v1")
        registry.unregister("bye")
        assert registry.get("bye") is None

    def test_len(self, registry):
        registry.register("a", "http://a/v1")
        registry.register("b", "http://b/v1")
        assert len(registry) == 2


class TestAllAndBestOrdering:
    def test_all_returns_sorted_by_priority(self, registry):
        registry.register("high", "http://h/v1", priority=5)
        registry.register("low", "http://l/v1", priority=10)
        registry.register("top", "http://t/v1", priority=0)
        names = [p.name for p in registry.all()]
        assert names == ["top", "high", "low"]

    def test_best_returns_lowest_priority_number(self, registry):
        registry.register("second", "http://s/v1", priority=2)
        registry.register("first", "http://f/v1", priority=1)
        with patch.object(registry, "_bootstrap_from_bridge_grid", return_value=False):
            best = registry.best()
        assert best.name == "first"

    def test_best_returns_none_when_empty(self, registry):
        # Bootstrap disabled to isolate this test
        with patch.object(registry, "_bootstrap_from_bridge_grid", return_value=False):
            assert registry.best() is None


class TestBootstrapFromBridgeGrid:
    def test_skips_bootstrap_when_providers_present(self, registry):
        registry.register("existing", "http://e/v1")
        result = registry._bootstrap_from_bridge_grid()
        assert result is False

    def test_bootstrap_registers_from_grid(self, registry):
        grid = {"bridge_port": 8091, "slot": 0, "app": "vscode"}
        with patch("navig.providers.bridge_grid_reader.read_bridge_grid", return_value=grid):
            result = registry._bootstrap_from_bridge_grid()
        assert result is True
        assert len(registry) == 1
        assert "bridge-vscode-0" in [p.name for p in registry.all()]

    def test_bootstrap_skips_when_no_bridge_port(self, registry):
        grid = {"slot": 0, "app": "vscode"}  # no bridge_port
        with patch("navig.providers.bridge_grid_reader.read_bridge_grid", return_value=grid):
            result = registry._bootstrap_from_bridge_grid()
        assert result is False

    def test_bootstrap_skips_when_grid_is_none(self, registry):
        with patch("navig.providers.bridge_grid_reader.read_bridge_grid", return_value=None):
            result = registry._bootstrap_from_bridge_grid()
        assert result is False

    def test_bootstrap_is_debounced(self, registry):
        grid = {"bridge_port": 8091, "slot": 0, "app": "cursor"}
        with patch("navig.providers.bridge_grid_reader.read_bridge_grid", return_value=grid) as mock_read:
            registry._bootstrap_from_bridge_grid()
            # Immediately call again — debounce should suppress second read
            registry.unregister("bridge-cursor-0")  # remove so first fast-path doesn't apply
            registry._last_bootstrap_at = 9999999999.0  # fake future ts
            result = registry._bootstrap_from_bridge_grid()
        assert result is False


class TestSingleton:
    def test_get_bridge_registry_returns_same_instance(self):
        reset_bridge_registry()
        r1 = get_bridge_registry()
        r2 = get_bridge_registry()
        assert r1 is r2

    def test_reset_creates_fresh_registry(self):
        r1 = get_bridge_registry()
        r1.register("temp", "http://t/v1")
        reset_bridge_registry()
        r2 = get_bridge_registry()
        assert len(r2) == 0
