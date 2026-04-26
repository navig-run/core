"""Tests for navig.comms.matrix_features — feature toggle system."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.comms.matrix_features import (
    MATRIX_FEATURE_DEFAULTS,
    get_all_features,
    is_feature_enabled,
    is_matrix_enabled,
    require_feature,
    require_matrix,
)
import navig.comms.matrix_features as mf


def _fake_cfg(matrix_cfg: dict) -> MagicMock:
    """Build a fake config manager that returns the given matrix block."""
    mgr = MagicMock()
    mgr.get_global_config.return_value = {"comms": {"matrix": matrix_cfg}}
    return mgr


class TestFeatureDefaults:
    def test_messaging_enabled_by_default(self):
        assert MATRIX_FEATURE_DEFAULTS["messaging"] is True

    def test_admin_ops_disabled_by_default(self):
        assert MATRIX_FEATURE_DEFAULTS["admin_ops"] is False

    def test_e2ee_disabled_by_default(self):
        assert MATRIX_FEATURE_DEFAULTS["e2ee"] is False

    def test_all_defaults_are_bool(self):
        for key, val in MATRIX_FEATURE_DEFAULTS.items():
            assert isinstance(val, bool), f"{key} should be bool"


class TestIsMatrixEnabled:
    def test_returns_false_when_config_missing(self):
        with patch("navig.config.get_config_manager") as mock_gcm:
            mock_gcm.side_effect = Exception("no config")
            assert is_matrix_enabled() is False

    def test_returns_false_when_not_in_config(self):
        mgr = MagicMock()
        mgr.get_global_config.return_value = {}
        with patch("navig.config.get_config_manager", return_value=mgr):
            assert is_matrix_enabled() is False

    def test_returns_true_when_enabled_in_config(self):
        mgr = _fake_cfg({"enabled": True})
        with patch("navig.config.get_config_manager", return_value=mgr):
            assert is_matrix_enabled() is True

    def test_returns_false_when_explicitly_disabled(self):
        mgr = _fake_cfg({"enabled": False})
        with patch("navig.config.get_config_manager", return_value=mgr):
            assert is_matrix_enabled() is False


class TestIsFeatureEnabled:
    def test_uses_default_when_not_in_config(self):
        mgr = _fake_cfg({"features": {}})
        with patch("navig.config.get_config_manager", return_value=mgr):
            # messaging default is True
            assert is_feature_enabled("messaging") is True
            # admin_ops default is False
            assert is_feature_enabled("admin_ops") is False

    def test_config_overrides_default(self):
        mgr = _fake_cfg({"features": {"admin_ops": True}})
        with patch("navig.config.get_config_manager", return_value=mgr):
            assert is_feature_enabled("admin_ops") is True

    def test_unknown_feature_returns_false(self):
        mgr = _fake_cfg({"features": {}})
        with patch("navig.config.get_config_manager", return_value=mgr):
            assert is_feature_enabled("nonexistent_feature") is False

    def test_config_exception_uses_defaults(self):
        with patch("navig.config.get_config_manager") as mock_gcm:
            mock_gcm.side_effect = Exception("fail")
            # Falls back to system defaults: messaging=True
            assert is_feature_enabled("messaging") is True


class TestGetAllFeatures:
    def test_returns_all_keys_from_defaults(self):
        with patch("navig.config.get_config_manager") as mock_gcm:
            mock_gcm.side_effect = Exception("no config")
            result = get_all_features()
        assert set(result.keys()) == set(MATRIX_FEATURE_DEFAULTS.keys())

    def test_config_override_reflected(self):
        mgr = _fake_cfg({"features": {"messaging": False}})
        with patch("navig.config.get_config_manager", return_value=mgr):
            result = get_all_features()
        assert result["messaging"] is False


class TestRequireFeatureDecorator:
    def test_calls_function_when_feature_enabled(self):
        called = []

        @require_feature("messaging")
        def my_func():
            called.append(True)

        mgr = _fake_cfg({"features": {"messaging": True}})
        with patch("navig.config.get_config_manager", return_value=mgr):
            my_func()
        assert called == [True]

    def test_raises_typer_exit_when_feature_disabled(self):
        import typer

        @require_feature("admin_ops")
        def admin_func():
            pass

        mgr = _fake_cfg({"features": {"admin_ops": False}})
        with patch("navig.config.get_config_manager", return_value=mgr):
            with pytest.raises(typer.Exit):
                admin_func()

    def test_preserves_function_name(self):
        @require_feature("messaging")
        def special_func():
            pass

        assert special_func.__name__ == "special_func"


class TestRequireMatrixDecorator:
    def test_calls_function_when_matrix_enabled(self):
        called = []

        @require_matrix()
        def my_op():
            called.append(True)

        mgr = _fake_cfg({"enabled": True})
        with patch("navig.config.get_config_manager", return_value=mgr):
            my_op()
        assert called == [True]

    def test_raises_typer_exit_when_matrix_disabled(self):
        import typer

        @require_matrix()
        def matrix_op():
            pass

        mgr = _fake_cfg({"enabled": False})
        with patch("navig.config.get_config_manager", return_value=mgr):
            with pytest.raises(typer.Exit):
                matrix_op()
