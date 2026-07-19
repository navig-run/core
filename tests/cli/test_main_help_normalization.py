from __future__ import annotations

import pytest

import navig.main as main_mod

pytestmark = pytest.mark.unit


def test_normalize_help_with_global_flags_prefix():
    # `doctor` has no navig/help/doctor.md guide → legacy rewrite applies.
    argv = ["navig", "--host", "prod", "help", "doctor"]
    normalized = main_mod._normalize_help_compat_args(argv)
    assert normalized == ["navig", "--host", "prod", "doctor", "--help"]


def test_normalize_help_keeps_md_topic_on_help_command():
    # `db` has a navig/help/db.md guide → NOT rewritten; the in-app help
    # command renders the markdown page (the whole point of the guides).
    argv = ["navig", "help", "db"]
    assert main_mod._normalize_help_compat_args(argv) == argv


def test_normalize_help_keeps_md_topic_with_global_flags_prefix():
    argv = ["navig", "--host", "prod", "help", "db"]
    assert main_mod._normalize_help_compat_args(argv) == argv


def test_normalize_help_multi_token_path_still_rewrites():
    # The help command takes a single topic; deeper paths keep the rewrite
    # even when the first token has a guide.
    argv = ["navig", "help", "db", "backup"]
    normalized = main_mod._normalize_help_compat_args(argv)
    assert normalized == ["navig", "db", "backup", "--help"]


def test_normalize_help_index_still_rewrites():
    # index.md is the bare-help landing page, not a guide for `navig index`.
    argv = ["navig", "help", "index"]
    normalized = main_mod._normalize_help_compat_args(argv)
    assert normalized == ["navig", "index", "--help"]


def test_has_help_page_rejects_path_tokens():
    assert main_mod._has_help_page("db") is True
    assert main_mod._has_help_page("index") is False
    assert main_mod._has_help_page("readme") is False
    assert main_mod._has_help_page("../help/db") is False
    assert main_mod._has_help_page("db/../db") is False
    assert main_mod._has_help_page("no_such_topic_xyz") is False


def test_normalize_memory_list_alias_with_global_flags_prefix():
    argv = ["navig", "--app", "portal", "memory", "list"]
    normalized = main_mod._normalize_help_compat_args(argv)
    assert normalized == ["navig", "--app", "portal", "memory", "sessions"]


def test_normalize_trailing_help_with_global_flags_suffix():
    argv = ["navig", "db", "help", "--host", "prod"]
    normalized = main_mod._normalize_help_compat_args(argv)
    assert normalized == ["navig", "db", "--help", "--host", "prod"]
