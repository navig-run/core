"""
Tests for the unified messaging layer.

Covers: ContactStore, ThreadStore, RoutingEngine, AdapterRegistryManager,
DeliveryTracker, and adapter integration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ── ContactStore tests ────────────────────────────────────────


class TestContactStore:
    """Test navig.store.contacts.ContactStore."""

    def _make_store(self, tmp_path: Path):
        from navig.store.contacts import ContactStore

        return ContactStore(tmp_path / "contacts_test.db")

    def test_add_and_resolve(self, tmp_path):
        store = self._make_store(tmp_path)
        contact = store.add_contact(alias="alice", display_name="Alice B.")
        assert contact is not None
        assert contact.alias == "alice"
        assert contact.display_name == "Alice B."

        resolved = store.resolve_alias("alice")
        assert resolved is not None
        assert resolved.alias == "alice"
        store.close()

    def test_add_route(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_contact(alias="bob", display_name="Bob")
        store.add_route("bob", "sms:+1234567890", priority=0)
        store.add_route("bob", "whatsapp:+1234567890", priority=1)

        contact = store.resolve_alias("bob")
        assert contact is not None
        assert len(contact.routes) == 2
        assert contact.routes[0].network == "sms"
        assert contact.routes[1].network == "whatsapp"
        store.close()

    def test_remove_route(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_contact(alias="carl")
        store.add_route("carl", "discord:carl#1234")
        store.remove_route("carl", "discord:carl#1234")

        contact = store.resolve_alias("carl")
        assert contact is not None
        assert len(contact.routes) == 0
        store.close()

    def test_set_default_network(self, tmp_path):
        store = self._make_store(tmp_path)
        cid = store.add_contact(alias="dana")
        store.set_default_network("dana", "whatsapp")

        contact = store.resolve_alias("dana")
        assert contact is not None
        assert contact.default_network == "whatsapp"
        store.close()

    def test_set_fallbacks(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_contact(alias="eve")
        store.set_fallbacks("eve", ["sms", "discord"])

        contact = store.resolve_alias("eve")
        assert contact is not None
        assert contact.fallbacks == ["sms", "discord"]
        store.close()

    def test_remove_contact(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_contact(alias="frank")
        store.remove_contact("frank")

        assert store.resolve_alias("frank") is None
        store.close()

    def test_list_contacts(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_contact(alias="alice")
        store.add_contact(alias="bob")
        store.add_contact(alias="carl")

        contacts = store.list_contacts(limit=10)
        assert len(contacts) == 3
        store.close()

    def test_search(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_contact(alias="alice", display_name="Alice Wonderland")
        store.add_contact(alias="bob", display_name="Bob Builder")

        results = store.search("alice")
        assert len(results) >= 1
        assert results[0].alias == "alice"
        store.close()

    def test_duplicate_alias_raises(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_contact(alias="dup")
        # Second add should raise IntegrityError (UNIQUE constraint)
        with pytest.raises(Exception):
            store.add_contact(alias="dup")
        store.close()


# ── ThreadStore tests ─────────────────────────────────────────


class TestThreadStore:
    """Test navig.store.threads.ThreadStore."""

    def _make_store(self, tmp_path: Path):
        from navig.store.threads import ThreadStore

        return ThreadStore(tmp_path / "threads_test.db")

    def test_get_or_create(self, tmp_path):
        store = self._make_store(tmp_path)
        t1 = store.get_or_create(adapter="sms", remote_conversation_id="+1234567890")
        assert t1.id >= 1
        assert t1.adapter == "sms"
        assert t1.status == "open"

        # Second call returns same thread
        t2 = store.get_or_create(adapter="sms", remote_conversation_id="+1234567890")
        assert t2.id == t1.id
        store.close()

    def test_get_by_id(self, tmp_path):
        store = self._make_store(tmp_path)
        t = store.get_or_create(adapter="discord", remote_conversation_id="ch-123")
        fetched = store.get_by_id(t.id)
        assert fetched is not None
        assert fetched.adapter == "discord"
        store.close()

    def test_close_and_reopen(self, tmp_path):
        store = self._make_store(tmp_path)
        t = store.get_or_create(adapter="wa", remote_conversation_id="wa-456")
        store.close_thread(t.id)
        fetched = store.get_by_id(t.id)
        assert fetched.status == "closed"

        store.reopen_thread(t.id)
        fetched = store.get_by_id(t.id)
        assert fetched.status == "open"
        store.close()

    def test_link_contact(self, tmp_path):
        store = self._make_store(tmp_path)
        t = store.get_or_create(adapter="tg", remote_conversation_id="tg-789")
        store.link_contact(t.id, "alice")
        fetched = store.get_by_id(t.id)
        assert fetched.contact_alias == "alice"
        store.close()

    def test_list_threads(self, tmp_path):
        store = self._make_store(tmp_path)
        store.get_or_create(adapter="sms", remote_conversation_id="+1")
        store.get_or_create(adapter="sms", remote_conversation_id="+2")
        store.get_or_create(adapter="discord", remote_conversation_id="ch-1")

        all_threads = store.list_threads(limit=10)
        assert len(all_threads) == 3

        sms_only = store.list_threads(adapter="sms", limit=10)
        assert len(sms_only) == 2
        store.close()

    def test_find_by_contact(self, tmp_path):
        store = self._make_store(tmp_path)
        t = store.get_or_create(adapter="wa", remote_conversation_id="wa-100")
        store.link_contact(t.id, "bob")

        threads = store.find_by_contact("bob")
        assert len(threads) == 1
        assert threads[0].contact_alias == "bob"
        store.close()

    def test_count(self, tmp_path):
        store = self._make_store(tmp_path)
        store.get_or_create(adapter="a", remote_conversation_id="1")
        store.get_or_create(adapter="b", remote_conversation_id="2")
        assert store.count() == 2
        store.close()

    def test_touch_updates_last_active(self, tmp_path):
        import time

        store = self._make_store(tmp_path)
        t = store.get_or_create(adapter="x", remote_conversation_id="y")
        first_active = store.get_by_id(t.id).last_active
        time.sleep(0.05)
        store.touch(t.id)
        second_active = store.get_by_id(t.id).last_active
        assert second_active >= first_active
        store.close()


# ── AdapterRegistryManager tests ─────────────────────────────


class TestAdapterRegistryManager:
    """Test navig.messaging.adapter_registry.AdapterRegistryManager."""

    def _make_registry(self):
        from navig.messaging.adapter_registry import AdapterRegistryManager

        return AdapterRegistryManager()

    def _make_dummy_adapter(self, name="test", compliance="official"):
        """Return a minimal object satisfying ChannelAdapter protocol."""
        from navig.messaging.adapter import ComplianceMode, IdentityMode

        class DummyAdapter:
            @property
            def name(self):
                return name

            @property
            def capabilities(self):
                return {"text"}

            @property
            def identity_mode(self):
                return IdentityMode.BOT

            @property
            def compliance(self):
                return ComplianceMode(compliance)

            async def send_message(self, target, text, **kwargs):
                pass

            async def resolve_target(self, raw):
                pass

            async def get_or_create_thread(self, conversation_id):
                pass

            async def receive_webhook(self, payload):
                return []

            async def ingest_event(self, event):
                pass

        return DummyAdapter()

    def test_register_and_get(self):
        reg = self._make_registry()
        adapter = self._make_dummy_adapter("sms")
        reg.register(adapter)
        assert reg.get("sms") is adapter

    def test_get_unknown_returns_none(self):
        reg = self._make_registry()
        assert reg.get("nonexistent") is None

    def test_disable_and_enable(self):
        reg = self._make_registry()
        adapter = self._make_dummy_adapter("sms")
        reg.register(adapter)
        reg.disable("sms")
        assert reg.get("sms") is None
        assert not reg.is_available("sms")

        reg.enable("sms")
        assert reg.get("sms") is adapter

    def test_experimental_auto_disabled(self):
        reg = self._make_registry()
        adapter = self._make_dummy_adapter("wa_web", compliance="experimental")
        reg.register(adapter)
        # Experimental adapters start disabled
        assert not reg.is_available("wa_web")

    def test_list_adapters(self):
        reg = self._make_registry()
        reg.register(self._make_dummy_adapter("a"))
        reg.register(self._make_dummy_adapter("b"))
        entries = reg.list_adapters()
        assert len(entries) == 2
        names = {e["name"] for e in entries}
        assert names == {"a", "b"}

    def test_available_names(self):
        reg = self._make_registry()
        reg.register(self._make_dummy_adapter("sms"))
        reg.register(self._make_dummy_adapter("exp", compliance="experimental"))
        # Only official ones are auto-enabled
        assert reg.available_names() == ["sms"]


# ── DeliveryTracker tests ────────────────────────────────────


class TestDeliveryTracker:
    """Test navig.messaging.delivery.DeliveryTracker."""

    def _make_tracker(self, tmp_path: Path):
        from navig.messaging.delivery import DeliveryTracker

        return DeliveryTracker(tmp_path / "deliveries_test.db")

    def test_record_and_get(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        delivery_id = tracker.record_send(
            adapter="sms",
            target="+1234567890",
            contact_alias="alice",
        )
        assert delivery_id >= 1

        row = tracker.get(delivery_id)
        assert row is not None
        assert row["adapter"] == "sms"
        assert row["target"] == "+1234567890"
        assert row["status"] == "queued"
        tracker.close()

    def test_update_status(self, tmp_path):
        from navig.messaging.adapter import DeliveryStatus

        tracker = self._make_tracker(tmp_path)
        did = tracker.record_send(adapter="wa", target="+999")
        tracker.update_status(did, DeliveryStatus.SENT)

        row = tracker.get(did)
        assert row["status"] == "sent"
        tracker.close()

    def test_forward_only_transition(self, tmp_path):
        from navig.messaging.adapter import DeliveryStatus

        tracker = self._make_tracker(tmp_path)
        did = tracker.record_send(adapter="wa", target="+999")
        tracker.update_status(did, DeliveryStatus.DELIVERED)

        # Cannot go backwards from DELIVERED to SENT
        tracker.update_status(did, DeliveryStatus.SENT)
        row = tracker.get(did)
        # Status should still be delivered
        assert row["status"] == "delivered"
        tracker.close()

    def test_apply_receipt_success(self, tmp_path):
        from navig.messaging.adapter import DeliveryReceipt

        tracker = self._make_tracker(tmp_path)
        did = tracker.record_send(adapter="sms", target="+1")
        receipt = DeliveryReceipt.success(message_id="msg-001")
        tracker.apply_receipt(did, receipt)

        row = tracker.get(did)
        assert row["status"] == "sent"
        assert row["message_id"] == "msg-001"
        tracker.close()

    def test_apply_receipt_failure(self, tmp_path):
        from navig.messaging.adapter import DeliveryReceipt

        tracker = self._make_tracker(tmp_path)
        did = tracker.record_send(adapter="sms", target="+1")
        receipt = DeliveryReceipt.failure("Network timeout")
        tracker.apply_receipt(did, receipt)

        row = tracker.get(did)
        assert row["status"] == "failed"
        assert row["error"] == "Network timeout"
        tracker.close()

    def test_recent(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.record_send(adapter="a", target="1")
        tracker.record_send(adapter="b", target="2")
        tracker.record_send(adapter="c", target="3")

        recent = tracker.recent(limit=2)
        assert len(recent) == 2
        tracker.close()

    def test_stats(self, tmp_path):
        from navig.messaging.adapter import DeliveryReceipt

        tracker = self._make_tracker(tmp_path)
        d1 = tracker.record_send(adapter="sms", target="1")
        d2 = tracker.record_send(adapter="sms", target="2")
        tracker.apply_receipt(d1, DeliveryReceipt.success("ok"))
        tracker.apply_receipt(d2, DeliveryReceipt.failure("err"))

        stats = tracker.stats()
        # stats returns {status_str: count}
        total = sum(stats.values())
        assert total == 2
        tracker.close()


# ── RoutingEngine tests ──────────────────────────────────────


class TestRoutingEngine:
    """Test navig.messaging.routing.RoutingEngine."""

    def _make_engine(self, tmp_path):
        from navig.messaging.adapter_registry import AdapterRegistryManager
        from navig.messaging.routing import RoutingEngine
        from navig.store.contacts import ContactStore
        from navig.store.threads import ThreadStore

        contacts = ContactStore(tmp_path / "contacts_routing_test.db")
        threads = ThreadStore(tmp_path / "threads_routing_test.db")
        adapters = AdapterRegistryManager()

        self._contacts = contacts
        self._threads = threads
        self._adapters = adapters
        return RoutingEngine(contacts, threads, adapters)

    def _register_adapter(self, name: str):
        from navig.messaging.adapter import ComplianceMode, IdentityMode

        class _Adapter:
            @property
            def name(self):
                return name

            @property
            def capabilities(self):
                return {"text"}

            @property
            def identity_mode(self):
                return IdentityMode.BOT

            @property
            def compliance(self):
                return ComplianceMode.OFFICIAL

            async def send_message(self, target, text, **kw):
                pass

            async def resolve_target(self, raw):
                pass

            async def get_or_create_thread(self, cid):
                pass

            async def receive_webhook(self, payload):
                return []

            async def ingest_event(self, event):
                pass

        self._adapters.register(_Adapter())

    def test_explicit_network_address(self, tmp_path):
        engine = self._make_engine(tmp_path)
        self._register_adapter("sms")
        decision = engine.resolve("sms:+1234567890")
        assert decision.adapter_name == "sms"
        assert decision.resolved_target.address == "+1234567890"
        self._contacts.close()
        self._threads.close()

    def test_alias_with_explicit_network(self, tmp_path):
        engine = self._make_engine(tmp_path)
        self._register_adapter("whatsapp")
        self._contacts.add_contact(alias="alice")
        self._contacts.add_route("alice", "whatsapp:+33600000000")

        decision = engine.resolve("@alice", network="whatsapp")
        assert decision.adapter_name == "whatsapp"
        assert decision.resolved_target.address == "+33600000000"
        self._contacts.close()
        self._threads.close()

    def test_alias_default_network(self, tmp_path):
        engine = self._make_engine(tmp_path)
        self._register_adapter("sms")
        self._contacts.add_contact(alias="bob", default_network="sms")
        self._contacts.add_route("bob", "sms:+1111111111")

        decision = engine.resolve("@bob")
        assert decision.adapter_name == "sms"
        self._contacts.close()
        self._threads.close()

    def test_alias_first_route_fallback(self, tmp_path):
        engine = self._make_engine(tmp_path)
        self._register_adapter("discord")
        self._contacts.add_contact(alias="carl")
        self._contacts.add_route("carl", "discord:carl#1234", priority=0)

        decision = engine.resolve("@carl")
        assert decision.adapter_name == "discord"
        self._contacts.close()
        self._threads.close()

    def test_no_route_raises(self, tmp_path):
        from navig.messaging.routing import NoRouteError

        engine = self._make_engine(tmp_path)
        self._contacts.add_contact(alias="nobody")

        with pytest.raises(NoRouteError):
            engine.resolve("@nobody")
        self._contacts.close()
        self._threads.close()

    def test_unknown_alias_raises(self, tmp_path):
        from navig.messaging.routing import NoRouteError

        engine = self._make_engine(tmp_path)
        with pytest.raises(NoRouteError):
            engine.resolve("@ghost")
        self._contacts.close()
        self._threads.close()


# ── Adapter type tests ────────────────────────────────────────


class TestAdapterTypes:
    """Test core types from navig.messaging.adapter."""

    def test_delivery_status_transitions(self):
        from navig.messaging.adapter import DeliveryStatus

        assert DeliveryStatus.QUEUED.can_transition_to(DeliveryStatus.SENT)
        assert DeliveryStatus.SENT.can_transition_to(DeliveryStatus.DELIVERED)
        assert not DeliveryStatus.DELIVERED.can_transition_to(DeliveryStatus.SENT)
        assert DeliveryStatus.QUEUED.can_transition_to(DeliveryStatus.FAILED)

    def test_delivery_receipt_success(self):
        from navig.messaging.adapter import DeliveryReceipt, DeliveryStatus

        r = DeliveryReceipt.success(message_id="abc")
        assert r.ok is True
        assert r.status == DeliveryStatus.SENT
        assert r.message_id == "abc"

    def test_delivery_receipt_failure(self):
        from navig.messaging.adapter import DeliveryReceipt, DeliveryStatus

        r = DeliveryReceipt.failure("timeout")
        assert r.ok is False
        assert r.status == DeliveryStatus.FAILED
        assert r.error == "timeout"

    def test_contact_dataclass(self):
        from navig.messaging.adapter import Contact, Route

        r = Route(network="sms", address="+1", priority=0)
        c = Contact(alias="alice", routes=[r], display_name="Alice")
        assert c.alias == "alice"
        assert c.routes[0].network == "sms"

    def test_thread_dataclass(self):
        from navig.messaging.adapter import Thread

        t = Thread(id=1, adapter="sms", remote_conversation_id="+1", status="open")
        assert t.adapter == "sms"
