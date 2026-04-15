"""
Tests for navig.memory.taxonomy — structured 4-type memory guidance.
"""
from __future__ import annotations

import pytest

from navig.memory.taxonomy import (
    MEMORY_TAXONOMY,
    MemoryType,
    build_memory_guidance,
    is_taxonomy_enabled,
)


class TestMemoryType:
    def test_all_four_types_exist(self):
        assert MemoryType.USER in MEMORY_TAXONOMY
        assert MemoryType.FEEDBACK in MEMORY_TAXONOMY
        assert MemoryType.PROJECT in MEMORY_TAXONOMY
        assert MemoryType.REFERENCE in MEMORY_TAXONOMY

    def test_each_type_has_required_fields(self):
        required_attrs = {"scope", "label", "description", "when_to_save", "how_to_use"}
        for memory_type, cfg in MEMORY_TAXONOMY.items():
            for attr in required_attrs:
                assert hasattr(cfg, attr), f"{memory_type} config missing attr: {attr}"

    def test_private_types(self):
        assert MEMORY_TAXONOMY[MemoryType.USER].scope == "private"
        assert MEMORY_TAXONOMY[MemoryType.FEEDBACK].scope == "private"

    def test_team_types(self):
        assert MEMORY_TAXONOMY[MemoryType.PROJECT].scope == "team"
        assert MEMORY_TAXONOMY[MemoryType.REFERENCE].scope == "team"


class TestBuildMemoryGuidance:
    def test_returns_string(self):
        guidance = build_memory_guidance()
        assert isinstance(guidance, str)
        assert len(guidance) > 0

    def test_contains_xml_wrapper(self):
        guidance = build_memory_guidance()
        assert "<memory_taxonomy>" in guidance
        assert "</memory_taxonomy>" in guidance

    def test_all_types_present_by_default(self):
        guidance = build_memory_guidance()
        for mt in MemoryType:
            assert mt.value in guidance

    def test_filter_to_single_type(self):
        guidance = build_memory_guidance(types=[MemoryType.USER])
        assert 'id="user"' in guidance
        # Other type ids should not appear in type elements
        assert 'id="project"' not in guidance
        assert 'id="reference"' not in guidance

    def test_filter_to_two_types(self):
        guidance = build_memory_guidance(types=[MemoryType.USER, MemoryType.FEEDBACK])
        assert 'id="user"' in guidance
        assert 'id="feedback"' in guidance
        assert 'id="project"' not in guidance

    def test_empty_type_list_returns_empty(self):
        guidance = build_memory_guidance(types=[])
        # Empty types → wrapper only or empty string; either is acceptable
        assert isinstance(guidance, str)

    def test_guidance_contains_scope(self):
        guidance = build_memory_guidance(types=[MemoryType.USER])
        assert "private" in guidance

    def test_guidance_contains_when_to_save(self):
        guidance = build_memory_guidance(types=[MemoryType.USER])
        user_cfg = MEMORY_TAXONOMY[MemoryType.USER]
        # The when_to_save text should appear in the guidance
        assert user_cfg.when_to_save[:30] in guidance


class TestIsTaxonomyEnabled:
    def test_returns_bool(self):
        result = is_taxonomy_enabled()
        assert isinstance(result, bool)

    def test_false_when_config_says_false(self, monkeypatch):
        """Patch config manager so taxonomy_enabled is False."""
        import unittest.mock as mock
        import navig.config as _cfg_mod

        fake_cm = mock.MagicMock()
        fake_cm.get.return_value = False
        monkeypatch.setattr(_cfg_mod, "get_config_manager", lambda: fake_cm)
        assert is_taxonomy_enabled() is False

    def test_true_when_config_says_true(self, monkeypatch):
        """Patch config manager so taxonomy_enabled is True."""
        import unittest.mock as mock
        import navig.config as _cfg_mod

        fake_cm = mock.MagicMock()
        fake_cm.get.return_value = True
        monkeypatch.setattr(_cfg_mod, "get_config_manager", lambda: fake_cm)
        assert is_taxonomy_enabled() is True
