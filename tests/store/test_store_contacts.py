"""Tests for store/contacts.py — pure helpers and ContactStore CRUD."""
from __future__ import annotations

import pytest

from navig.store.contacts import (
    ContactStore,
    _parse_route_string,
    normalize_phone,
)


# ──────────────────────────────────────────────────────────────────────────────
# normalize_phone
# ──────────────────────────────────────────────────────────────────────────────


class TestNormalizePhone:
    def test_strips_hyphens(self):
        assert normalize_phone("123-456-7890") == "1234567890"

    def test_strips_spaces(self):
        assert normalize_phone("123 456 7890") == "1234567890"

    def test_strips_parens(self):
        assert normalize_phone("(123)456-7890") == "1234567890"

    def test_preserves_leading_plus(self):
        assert normalize_phone("+33 6 12 34 56 78") == "+33612345678"

    def test_empty_string(self):
        assert normalize_phone("") == ""

    def test_none_returns_empty(self):
        assert normalize_phone(None) == ""  # type: ignore[arg-type]

    def test_only_non_digits(self):
        assert normalize_phone("---") == ""

    def test_plain_digits_unchanged(self):
        assert normalize_phone("0612345678") == "0612345678"


# ──────────────────────────────────────────────────────────────────────────────
# _parse_route_string
# ──────────────────────────────────────────────────────────────────────────────


class TestParseRouteString:
    def test_simple(self):
        network, address = _parse_route_string("whatsapp:+33612345678")
        assert network == "whatsapp"
        assert address == "+33612345678"

    def test_lowercases_network(self):
        network, _ = _parse_route_string("Discord:123456789")
        assert network == "discord"

    def test_no_colon_raises(self):
        with pytest.raises(ValueError, match="network:address"):
            _parse_route_string("nodivider")

    def test_empty_network_raises(self):
        with pytest.raises(ValueError):
            _parse_route_string(":address")

    def test_empty_address_raises(self):
        with pytest.raises(ValueError):
            _parse_route_string("network:")

    def test_colon_in_address_kept(self):
        # telegram:user:123 → network=telegram, address=user:123
        network, address = _parse_route_string("telegram:user:123")
        assert network == "telegram"
        assert address == "user:123"

    def test_strips_whitespace(self):
        network, address = _parse_route_string("  email : alice@example.com ")
        assert network == "email"
        assert address == "alice@example.com"


# ──────────────────────────────────────────────────────────────────────────────
# ContactStore
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    return ContactStore(db_path=tmp_path / "contacts.db")


class TestContactStoreAdd:
    def test_add_returns_contact(self, store):
        c = store.add_contact("alice", "Alice Dupont")
        assert c.alias == "alice"
        assert c.display_name == "Alice Dupont"

    def test_add_with_routes(self, store):
        c = store.add_contact(
            "bob", "Bob", routes=["whatsapp:+33600000000", "discord:99999"]
        )
        assert len(c.routes) == 2
        networks = {r.network for r in c.routes}
        assert "whatsapp" in networks
        assert "discord" in networks

    def test_add_strips_at_prefix(self, store):
        c = store.add_contact("@charlie", "Charlie")
        assert c.alias == "charlie"

    def test_add_with_default_network(self, store):
        c = store.add_contact("dave", routes=["telegram:@dave"], default_network="telegram")
        assert c.default_network == "telegram"

    def test_duplicate_alias_raises(self, store):
        store.add_contact("eve", "Eve")
        with pytest.raises(Exception):
            store.add_contact("eve", "Eve Duplicate")


class TestContactStoreResolve:
    def test_resolve_existing(self, store):
        store.add_contact("frank", "Frank")
        c = store.resolve_alias("frank")
        assert c is not None
        assert c.display_name == "Frank"

    def test_resolve_missing_returns_none(self, store):
        assert store.resolve_alias("ghost") is None

    def test_resolve_case_insensitive(self, store):
        store.add_contact("grace", "Grace")
        c = store.resolve_alias("GRACE")
        assert c is not None

    def test_resolve_strips_at(self, store):
        store.add_contact("hank", "Hank")
        c = store.resolve_alias("@hank")
        assert c is not None


class TestContactStoreRemove:
    def test_remove_existing(self, store):
        store.add_contact("ivan", "Ivan")
        assert store.remove_contact("ivan") is True
        assert store.resolve_alias("ivan") is None

    def test_remove_missing_returns_false(self, store):
        assert store.remove_contact("nobody") is False


class TestContactStoreList:
    def test_empty_list(self, store):
        assert store.list_contacts() == []

    def test_lists_added_contacts(self, store):
        store.add_contact("jack", "Jack")
        store.add_contact("kate", "Kate")
        contacts = store.list_contacts()
        aliases = {c.alias for c in contacts}
        assert "jack" in aliases
        assert "kate" in aliases

    def test_ordered_by_alias(self, store):
        store.add_contact("zebra", "Z")
        store.add_contact("alpha", "A")
        contacts = store.list_contacts()
        assert contacts[0].alias == "alpha"
        assert contacts[1].alias == "zebra"


class TestContactStoreSearch:
    def test_search_by_alias(self, store):
        store.add_contact("louis", "Louis")
        store.add_contact("laura", "Laura")
        results = store.search("loui")
        assert any(c.alias == "louis" for c in results)

    def test_search_by_display_name(self, store):
        store.add_contact("mike", "Michael Scott")
        results = store.search("Michael")
        assert any(c.alias == "mike" for c in results)

    def test_search_no_match(self, store):
        assert store.search("xyznomatch") == []


class TestContactStoreUpdate:
    def test_update_display_name(self, store):
        store.add_contact("nina", "Nina")
        updated = store.update_contact("nina", display_name="Nina Updated")
        assert updated is True
        c = store.resolve_alias("nina")
        assert c.display_name == "Nina Updated"

    def test_update_nonexistent_returns_false(self, store):
        updated = store.update_contact("ghost_user", display_name="X")
        assert updated is False
