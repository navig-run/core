"""
`navig connect remove` reference resolution: a full id, a unique id-prefix (as
shown by `navig connect list`), or an exact name — never the wrong connection.
"""

from __future__ import annotations

import pytest

from navig.commands import connect_cmd as cc
from navig.providers.connection_types import ConnectionError as ConnError


def _conns():
    return [
        {"connection_id": "abc12345-aaaa", "name": "Claude (Pro/Max subscription)"},
        {"connection_id": "def67890-bbbb", "name": "Claude — cybesis@gmail.com"},
        {"connection_id": "abcffff0-cccc", "name": "OpenAI"},
    ]


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr(cc, "list_connections", _conns)


def test_resolve_exact_id():
    assert cc._resolve_connection_ref("def67890-bbbb") == "def67890-bbbb"


def test_resolve_unique_prefix():
    # The short 8-char id from `connect list` uniquely resolves.
    assert cc._resolve_connection_ref("def67890") == "def67890-bbbb"


def test_resolve_exact_name_case_insensitive():
    assert cc._resolve_connection_ref("OpenAI") == "abcffff0-cccc"
    assert cc._resolve_connection_ref("claude (pro/max subscription)") == "abc12345-aaaa"


def test_ambiguous_prefix_raises():
    # "abc" matches both the Claude subscription and OpenAI → refuse (don't guess).
    with pytest.raises(ConnError):
        cc._resolve_connection_ref("abc")


def test_no_match_raises():
    with pytest.raises(ConnError):
        cc._resolve_connection_ref("zzz")


def test_empty_raises():
    with pytest.raises(ConnError):
        cc._resolve_connection_ref("   ")
