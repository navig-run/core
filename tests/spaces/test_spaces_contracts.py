"""Tests for navig.spaces.contracts — normalize_space_name, validate_space_name, is_user_space."""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.spaces.contracts import (
    CANONICAL_SPACES,
    SPACE_ALIASES,
    SpaceConfig,
    is_user_space,
    normalize_space_name,
    validate_space_name,
)


# ---------------------------------------------------------------------------
# normalize_space_name
# ---------------------------------------------------------------------------

class TestNormalizeSpaceName:
    def test_none_returns_default(self):
        assert normalize_space_name(None) == "default"

    def test_empty_string_returns_default(self):
        assert normalize_space_name("") == "default"

    def test_whitespace_returns_default(self):
        assert normalize_space_name("   ") == "default"

    def test_canonical_name_returned_as_is(self):
        for name in CANONICAL_SPACES:
            assert normalize_space_name(name) == name

    def test_alias_resolved(self):
        assert normalize_space_name("ops") == "devops"
        assert normalize_space_name("operations") == "devops"

    def test_alias_with_dash_suffix(self):
        assert normalize_space_name("project-space") == "project"
        assert normalize_space_name("devops-space") == "devops"

    def test_case_insensitive(self):
        assert normalize_space_name("DevOps") == "devops"
        assert normalize_space_name("PROJECT") == "project"

    def test_unknown_name_returns_default(self):
        assert normalize_space_name("totally-unknown-xyz") == "default"

    def test_default_in_canonical_spaces(self):
        assert "default" in CANONICAL_SPACES


# ---------------------------------------------------------------------------
# validate_space_name
# ---------------------------------------------------------------------------

class TestValidateSpaceName:
    def test_canonical_names_valid(self):
        for name in CANONICAL_SPACES:
            assert validate_space_name(name) is True

    def test_aliases_valid(self):
        for alias in SPACE_ALIASES:
            assert validate_space_name(alias) is True

    def test_unknown_name_invalid(self):
        assert validate_space_name("my-custom-space-xyz") is False

    def test_empty_invalid(self):
        assert validate_space_name("") is False

    def test_case_insensitive(self):
        assert validate_space_name("DevOps") is True


# ---------------------------------------------------------------------------
# is_user_space
# ---------------------------------------------------------------------------

class TestIsUserSpace:
    def test_canonical_is_not_user_space(self):
        for name in CANONICAL_SPACES:
            assert is_user_space(name) is False

    def test_unknown_is_user_space(self):
        assert is_user_space("my-hobby-space") is True

    def test_alias_not_user_space(self):
        assert is_user_space("ops") is False


# ---------------------------------------------------------------------------
# SpaceConfig
# ---------------------------------------------------------------------------

class TestSpaceConfig:
    def test_construction(self):
        sc = SpaceConfig(
            requested_name="ops",
            canonical_name="devops",
            path=Path("/tmp/spaces/devops"),
            scope="global",
        )
        assert sc.requested_name == "ops"
        assert sc.canonical_name == "devops"
        assert sc.scope == "global"

    def test_frozen(self):
        sc = SpaceConfig("a", "b", Path("/x"), "global")
        with pytest.raises((TypeError, AttributeError)):
            sc.scope = "project"  # type: ignore[misc]
