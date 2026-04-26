"""Tests for navig.core.thresholds — metric threshold registry."""

from __future__ import annotations

import pytest

from navig.core.thresholds import DEFAULTS, REGISTRY, Threshold, resolve


class TestThreshold:
    def test_dataclass_fields(self):
        t = Threshold(warn_pct=70.0, crit_pct=90.0)
        assert t.warn_pct == 70.0
        assert t.crit_pct == 90.0

    def test_frozen(self):
        t = Threshold(warn_pct=70.0, crit_pct=90.0)
        with pytest.raises((AttributeError, TypeError)):
            t.warn_pct = 99.0  # type: ignore[misc]

    def test_warn_less_than_crit_by_convention(self):
        t = Threshold(warn_pct=80.0, crit_pct=95.0)
        assert t.warn_pct < t.crit_pct


class TestDefaults:
    def test_defaults_are_threshold_instance(self):
        assert isinstance(DEFAULTS, Threshold)

    def test_defaults_warn_below_crit(self):
        assert DEFAULTS.warn_pct < DEFAULTS.crit_pct


class TestRegistry:
    def test_registry_is_dict(self):
        assert isinstance(REGISTRY, dict)

    def test_cpu_usage_registered(self):
        assert "cpu_usage" in REGISTRY

    def test_memory_usage_registered(self):
        assert "memory_usage" in REGISTRY

    def test_disk_usage_registered(self):
        assert "disk_usage" in REGISTRY

    def test_all_registry_values_are_thresholds(self):
        for key, value in REGISTRY.items():
            assert isinstance(value, Threshold), f"{key} is not a Threshold"

    def test_all_registry_values_warn_below_crit(self):
        for key, value in REGISTRY.items():
            assert value.warn_pct < value.crit_pct, f"{key}: warn >= crit"


class TestResolve:
    def test_resolves_cpu_usage(self):
        t = resolve("cpu_usage")
        assert isinstance(t, Threshold)

    def test_resolves_disk_usage(self):
        t = resolve("disk_usage")
        assert t.warn_pct > 0

    def test_unknown_metric_returns_defaults(self):
        t = resolve("totally_unknown_metric_xyz")
        assert t is DEFAULTS

    def test_returns_registry_object(self):
        t = resolve("memory_usage")
        assert t is REGISTRY["memory_usage"]

    def test_error_rate_registered(self):
        t = resolve("error_rate")
        assert t.crit_pct <= 100.0
