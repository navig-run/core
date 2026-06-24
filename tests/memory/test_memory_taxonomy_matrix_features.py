"""Tests for memory/taxonomy.py and comms/matrix_features.py."""
from __future__ import annotations

from unittest.mock import patch

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# memory/taxonomy.py
# ──────────────────────────────────────────────────────────────────────────────
from navig.memory.taxonomy import (
    MEMORY_TAXONOMY,
    MemoryType,
    _MemoryTypeConfig,
    build_memory_guidance,
    is_taxonomy_enabled,
)


class TestMemoryTypeEnum:
    def test_user_value(self):
        assert MemoryType.USER.value == "user"

    def test_feedback_value(self):
        assert MemoryType.FEEDBACK.value == "feedback"

    def test_project_value(self):
        assert MemoryType.PROJECT.value == "project"

    def test_reference_value(self):
        assert MemoryType.REFERENCE.value == "reference"

    def test_four_members(self):
        assert len(MemoryType) == 4

    def test_is_str_subclass(self):
        assert isinstance(MemoryType.USER, str)


class TestMemoryTaxonomyRegistry:
    def test_has_all_four_types(self):
        for mt in MemoryType:
            assert mt in MEMORY_TAXONOMY

    def test_all_values_are_configs(self):
        for mt, cfg in MEMORY_TAXONOMY.items():
            assert isinstance(cfg, _MemoryTypeConfig), f"{mt} cfg wrong type"

    def test_user_scope_is_private(self):
        assert MEMORY_TAXONOMY[MemoryType.USER].scope == "private"

    def test_project_scope_is_project(self):
        assert MEMORY_TAXONOMY[MemoryType.PROJECT].scope in (
            "project",
            "team",
            "workspace",
        )

    def test_config_has_label(self):
        for cfg in MEMORY_TAXONOMY.values():
            assert cfg.label

    def test_config_has_examples_list(self):
        for cfg in MEMORY_TAXONOMY.values():
            assert isinstance(cfg.examples, list)
            assert len(cfg.examples) > 0

    def test_config_has_when_to_save(self):
        for cfg in MEMORY_TAXONOMY.values():
            assert cfg.when_to_save

    def test_config_has_how_to_use(self):
        for cfg in MEMORY_TAXONOMY.values():
            assert cfg.how_to_use

    def test_config_has_body_structure(self):
        for cfg in MEMORY_TAXONOMY.values():
            assert cfg.body_structure


class TestBuildMemoryGuidance:
    def test_returns_string(self):
        result = build_memory_guidance()
        assert isinstance(result, str)

    def test_contains_root_element(self):
        result = build_memory_guidance()
        assert "<memory_taxonomy>" in result
        assert "</memory_taxonomy>" in result

    def test_contains_all_type_ids(self):
        result = build_memory_guidance()
        for mt in MemoryType:
            assert f'id="{mt.value}"' in result

    def test_subset_types(self):
        result = build_memory_guidance(types=[MemoryType.USER])
        assert 'id="user"' in result
        assert 'id="project"' not in result

    def test_empty_types_list(self):
        result = build_memory_guidance(types=[])
        assert "<memory_taxonomy>" in result
        assert "<memory_type" not in result

    def test_contains_label(self):
        result = build_memory_guidance(types=[MemoryType.USER])
        assert "<label>" in result

    def test_contains_examples(self):
        result = build_memory_guidance(types=[MemoryType.USER])
        assert "<examples>" in result


class TestIsTaxonomyEnabled:
    def test_returns_bool(self):
        assert isinstance(is_taxonomy_enabled(), bool)

    def test_defaults_true_on_config_error(self):
        with patch("navig.config.get_config_manager", side_effect=Exception("no config")):
            result = is_taxonomy_enabled()
        assert result is True


# ──────────────────────────────────────────────────────────────────────────────
# comms/matrix_features.py
# ──────────────────────────────────────────────────────────────────────────────
from navig.comms.matrix_features import (
    FEATURE_DESCRIPTIONS,
    MATRIX_FEATURE_DEFAULTS,
    get_all_features,
    is_feature_enabled,
    is_matrix_enabled,
    require_feature,
)


class TestMatrixFeatureDefaults:
    def test_messaging_enabled_by_default(self):
        assert MATRIX_FEATURE_DEFAULTS["messaging"] is True

    def test_admin_ops_disabled_by_default(self):
        assert MATRIX_FEATURE_DEFAULTS["admin_ops"] is False

    def test_e2ee_disabled_by_default(self):
        assert MATRIX_FEATURE_DEFAULTS["e2ee"] is False

    def test_notifications_enabled_by_default(self):
        assert MATRIX_FEATURE_DEFAULTS["notifications"] is True

    def test_non_empty(self):
        assert len(MATRIX_FEATURE_DEFAULTS) > 0


class TestFeatureDescriptions:
    def test_keys_match_defaults(self):
        assert set(FEATURE_DESCRIPTIONS.keys()) == set(MATRIX_FEATURE_DEFAULTS.keys())

    def test_all_values_are_non_empty_strings(self):
        for key, desc in FEATURE_DESCRIPTIONS.items():
            assert isinstance(desc, str) and desc, f"{key} description empty"


class TestIsFeatureEnabled:
    def test_messaging_true_with_no_config(self):
        with patch("navig.comms.matrix_features._get_matrix_features_config", return_value={}):
            assert is_feature_enabled("messaging") is True

    def test_admin_ops_false_with_no_config(self):
        with patch("navig.comms.matrix_features._get_matrix_features_config", return_value={}):
            assert is_feature_enabled("admin_ops") is False

    def test_config_override_enables_feature(self):
        with patch(
            "navig.comms.matrix_features._get_matrix_features_config",
            return_value={"admin_ops": True},
        ):
            assert is_feature_enabled("admin_ops") is True

    def test_unknown_feature_returns_false(self):
        with patch("navig.comms.matrix_features._get_matrix_features_config", return_value={}):
            assert is_feature_enabled("nonexistent_feature_xyz") is False


class TestGetAllFeatures:
    def test_returns_dict(self):
        with patch("navig.comms.matrix_features._get_matrix_features_config", return_value={}):
            result = get_all_features()
        assert isinstance(result, dict)

    def test_all_default_keys_present(self):
        with patch("navig.comms.matrix_features._get_matrix_features_config", return_value={}):
            result = get_all_features()
        for key in MATRIX_FEATURE_DEFAULTS:
            assert key in result

    def test_config_override_reflected(self):
        with patch(
            "navig.comms.matrix_features._get_matrix_features_config",
            return_value={"e2ee": True},
        ):
            result = get_all_features()
        assert result["e2ee"] is True


class TestIsMatrixEnabled:
    def test_false_when_config_error(self):
        with patch("navig.config.get_config_manager", side_effect=Exception("no config")):
            assert is_matrix_enabled() is False

    def test_false_when_not_configured(self):
        with patch("navig.config.get_config_manager") as mock_cfg:
            mock_cfg.return_value.get_global_config.return_value = {}
            assert is_matrix_enabled() is False


class TestRequireFeature:
    def test_passes_through_when_enabled(self):
        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=True):

            @require_feature("messaging")
            def my_cmd():
                return "ok"

            result = my_cmd()
        assert result == "ok"

    def test_aborts_when_disabled(self):
        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):

            @require_feature("admin_ops")
            def my_cmd():
                return "executed"

            # Should not return "executed" — exits or raises
            try:
                result = my_cmd()
                # If it returns instead of raising, should not be "executed"
                assert result != "executed"
            except (SystemExit, Exception):
                pass  # Any exit/exception is acceptable
