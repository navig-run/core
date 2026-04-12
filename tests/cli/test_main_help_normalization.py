from __future__ import annotations

import navig.main as main_mod
import pytest

pytestmark = pytest.mark.unit


def test_normalize_help_with_global_flags_prefix():
    argv = ["navig", "--host", "prod", "help", "db"]
    normalized = main_mod._normalize_help_compat_args(argv)
    assert normalized == ["navig", "--host", "prod", "db", "--help"]


def test_normalize_memory_list_alias_with_global_flags_prefix():
    argv = ["navig", "--app", "portal", "memory", "list"]
    normalized = main_mod._normalize_help_compat_args(argv)
    assert normalized == ["navig", "--app", "portal", "memory", "sessions"]


def test_normalize_trailing_help_with_global_flags_suffix():
    argv = ["navig", "db", "help", "--host", "prod"]
    normalized = main_mod._normalize_help_compat_args(argv)
    assert normalized == ["navig", "db", "--help", "--host", "prod"]
