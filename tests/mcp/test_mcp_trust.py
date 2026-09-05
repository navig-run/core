"""Unit tests for :mod:`navig.mcp.trust` — scope and deployment config.

Classification itself is pinned against the approval gate in
``tests/quality/test_external_tool_gate_parity.py``; this file covers the parts that
have no counterpart on the gate side.
"""

from __future__ import annotations

import pytest

from navig.mcp.trust import (
    ServerTrust,
    allowed_tools_for_server,
    catalog_revision,
    honor_read_only_hint,
    namespaced_tool_name,
    tool_is_in_scope,
    trust_for_server,
)


@pytest.fixture
def trust_config(monkeypatch):
    """Patch the one config read, so no test here touches the operator's real config."""
    section: dict = {}

    def _fake_get(key, default=None):
        return section if key == "mcp.trust" else default

    class _CM:
        get = staticmethod(_fake_get)

    monkeypatch.setattr("navig.config.get_config_manager", lambda *a, **k: _CM())
    return section


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_no_restriction_allows_everything() -> None:
    assert tool_is_in_scope("anything", None) is True


def test_a_list_restricts_to_those_tools() -> None:
    allowed = ("read", "write")
    assert tool_is_in_scope("read", allowed) is True
    assert tool_is_in_scope("delete", allowed) is False


def test_an_empty_restriction_denies_everything() -> None:
    """Fail-closed, and the whole point of the type: a configured-but-unusable
    allowlist must not collapse into 'not configured', or a typo becomes full access."""
    assert tool_is_in_scope("read", ()) is False


def test_an_unconfigured_server_is_unrestricted(trust_config) -> None:
    assert allowed_tools_for_server("acme") is None


def test_a_tier_only_entry_is_unrestricted(trust_config) -> None:
    """The short spelling must keep working now that the long one exists."""
    trust_config["servers"] = {"acme": "vetted"}
    assert allowed_tools_for_server("acme") is None
    assert trust_for_server("acme") is ServerTrust.VETTED


def test_the_long_form_carries_both(trust_config) -> None:
    trust_config["servers"] = {"acme": {"tier": "vetted", "tools": ["a", "b"]}}
    assert trust_for_server("acme") is ServerTrust.VETTED
    assert allowed_tools_for_server("acme") == ("a", "b")


def test_a_comma_string_is_accepted(trust_config) -> None:
    """`navig config set` cannot write a YAML list; a comma string is what it produces."""
    trust_config["servers"] = {"acme": {"tools": "a, b ,c"}}
    assert allowed_tools_for_server("acme") == ("a", "b", "c")


@pytest.mark.parametrize("junk", [123, True, {"a": 1}, None])
def test_an_unusable_tools_value_denies_rather_than_opens(trust_config, junk) -> None:
    trust_config["servers"] = {"acme": {"tools": junk}}
    allowed = allowed_tools_for_server("acme")
    assert allowed == (), (
        "A `tools` key that names nothing must deny, not fall back to unrestricted."
    )
    assert tool_is_in_scope("anything", allowed) is False


def test_an_empty_list_denies(trust_config) -> None:
    trust_config["servers"] = {"acme": {"tools": []}}
    assert allowed_tools_for_server("acme") == ()


def test_blank_entries_are_dropped(trust_config) -> None:
    trust_config["servers"] = {"acme": {"tools": ["a", "  ", ""]}}
    assert allowed_tools_for_server("acme") == ("a",)


# ---------------------------------------------------------------------------
# Trust tier
# ---------------------------------------------------------------------------


def test_trust_defaults_to_byo(trust_config) -> None:
    assert trust_for_server("acme") is ServerTrust.BYO


def test_a_per_server_tier_wins_over_the_default(trust_config) -> None:
    trust_config["default"] = "byo"
    trust_config["servers"] = {"acme": "vetted"}
    assert trust_for_server("acme") is ServerTrust.VETTED
    assert trust_for_server("globex") is ServerTrust.BYO


@pytest.mark.parametrize("bogus", ["trusted", "yes", "TRUE", "", "vetted!", 1, None])
def test_an_unrecognised_tier_falls_back_to_byo(trust_config, bogus) -> None:
    """Same rule `coerce_bool` applies to an unknown token: ambiguity never resolves to
    the more permissive reading."""
    trust_config["servers"] = {"acme": bogus}
    assert trust_for_server("acme") is ServerTrust.BYO


def test_a_dict_entry_with_a_bogus_tier_falls_back_to_byo(trust_config) -> None:
    trust_config["servers"] = {"acme": {"tier": "trusted", "tools": ["a"]}}
    assert trust_for_server("acme") is ServerTrust.BYO
    assert allowed_tools_for_server("acme") == ("a",)


def test_tier_is_case_and_space_insensitive(trust_config) -> None:
    trust_config["servers"] = {"acme": "  Vetted "}
    assert trust_for_server("acme") is ServerTrust.VETTED


def test_unreadable_config_does_not_open_the_gate(monkeypatch) -> None:
    """A config read that raises must not be read as 'vetted'."""

    def _boom(*a, **k):
        raise RuntimeError("config on fire")

    monkeypatch.setattr("navig.config.get_config_manager", _boom)
    assert trust_for_server("acme") is ServerTrust.BYO
    assert honor_read_only_hint() is True
    assert allowed_tools_for_server("acme") is None


@pytest.mark.parametrize(
    "raw,expected",
    [(True, True), ("true", True), (False, False), ("false", False), ("off", False)],
)
def test_honor_read_only_hint_is_coerced(trust_config, raw, expected) -> None:
    """`navig config set` stores raw strings, so a bare `if value:` would read the
    string "false" as True — the exact trap coerce_bool exists for."""
    trust_config["honor_read_only_hint"] = raw
    assert honor_read_only_hint() is expected


def test_honor_read_only_hint_defaults_true(trust_config) -> None:
    assert honor_read_only_hint() is True


# ---------------------------------------------------------------------------
# Naming and catalog fingerprint
# ---------------------------------------------------------------------------


def test_namespaced_name_is_stable() -> None:
    assert namespaced_tool_name("acme", "read") == "mcp__acme__read"


def test_catalog_revision_is_order_independent() -> None:
    catalog = [{"name": "a"}, {"name": "b", "annotations": {"readOnlyHint": True}}]
    assert catalog_revision(catalog) == catalog_revision(list(reversed(catalog)))


def test_catalog_revision_ignores_description_edits() -> None:
    assert catalog_revision([{"name": "a", "description": "x"}]) == catalog_revision(
        [{"name": "a", "description": "x, nicely"}]
    )


def test_catalog_revision_reacts_to_a_new_tool() -> None:
    assert catalog_revision([{"name": "a"}]) != catalog_revision(
        [{"name": "a"}, {"name": "b"}]
    )
