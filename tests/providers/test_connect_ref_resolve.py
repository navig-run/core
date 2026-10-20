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
        {"connection_id": "def67890-bbbb", "name": "Claude — studio@example.com"},
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


# ── `navig connect list` footer / ID column ────────────────────────────────


def test_short_ref_keeps_configured_ids_whole_and_resolvable(monkeypatch):
    # `configured:groq`[:8] == "configur" — shared by every BYOK row, so it was
    # an ID column you could not type back. The shown ref must resolve.
    rows = [
        {"connection_id": "configured:groq", "name": "Groq"},
        {"connection_id": "configured:openai", "name": "OpenAI"},
        {"connection_id": "223aafe7-1111", "name": "Claude — a@b.c"},
    ]
    monkeypatch.setattr(cc, "list_connections", lambda: rows)
    for r in rows:
        assert cc._resolve_connection_ref(cc._short_ref(r["connection_id"])) == r["connection_id"]
    assert cc._short_ref("223aafe7-1111") == "223aafe7"


def test_next_steps_names_the_reconnect_command_for_broken_rows():
    rows = [
        {"connection_id": "a", "template_id": "claude-max", "ui_state": "needs_reauth"},
        {"connection_id": "b", "template_id": "claude-max", "ui_state": "needs_reauth"},
        {"connection_id": "c", "template_id": "groq", "ui_state": "ready"},
    ]
    text = "\n".join(cc._next_steps(rows, default_id="a"))
    assert "reconnect claude-max: [bold]navig connect login claude-max" in text
    assert text.count("reconnect claude-max") == 1  # one line per template, not per row
    assert "reconnect claude-max" in text
    assert "navig connect default <id>" in text
    assert "no default yet" not in text


def test_next_steps_flags_a_missing_default():
    rows = [{"connection_id": "c", "template_id": "groq", "ui_state": "ready"}]
    text = "\n".join(cc._next_steps(rows, default_id=None))
    assert "no default yet" in text
    assert "reconnect" not in text


def test_next_steps_warns_when_the_default_itself_is_broken():
    rows = [{"connection_id": "a", "template_id": "claude-max", "ui_state": "needs_reauth"}]
    assert "default needs reauth" in "\n".join(cc._next_steps(rows, default_id="a"))

