"""
Batch 34 — navig/messaging/adapter.py

Covers:
  DeliveryStatus: values, can_transition_to() (forward, backward, to FAILED)
  ComplianceMode: values
  IdentityMode: values
  ResolvedTarget: frozen dataclass fields, immutability
  DeliveryReceipt: defaults, auto-timestamp, .success(), .failure()
  InboundEvent: fields, auto-timestamp, frozen immutability
  Thread: mutable dataclass defaults and fields
"""

from __future__ import annotations

import time

import pytest

from navig.messaging.adapter import (
    ComplianceMode,
    DeliveryReceipt,
    DeliveryStatus,
    IdentityMode,
    InboundEvent,
    ResolvedTarget,
    Thread,
)


# ---------------------------------------------------------------------------
# DeliveryStatus
# ---------------------------------------------------------------------------

class TestDeliveryStatus:
    def test_values(self):
        assert DeliveryStatus.QUEUED == "queued"
        assert DeliveryStatus.SENT == "sent"
        assert DeliveryStatus.DELIVERED == "delivered"
        assert DeliveryStatus.READ == "read"
        assert DeliveryStatus.FAILED == "failed"

    # Forward transitions
    def test_queued_to_sent(self):
        assert DeliveryStatus.QUEUED.can_transition_to(DeliveryStatus.SENT) is True

    def test_queued_to_delivered(self):
        assert DeliveryStatus.QUEUED.can_transition_to(DeliveryStatus.DELIVERED) is True

    def test_sent_to_delivered(self):
        assert DeliveryStatus.SENT.can_transition_to(DeliveryStatus.DELIVERED) is True

    def test_delivered_to_read(self):
        assert DeliveryStatus.DELIVERED.can_transition_to(DeliveryStatus.READ) is True

    # Backward transitions are not allowed
    def test_sent_to_queued_refused(self):
        assert DeliveryStatus.SENT.can_transition_to(DeliveryStatus.QUEUED) is False

    def test_delivered_to_sent_refused(self):
        assert DeliveryStatus.DELIVERED.can_transition_to(DeliveryStatus.SENT) is False

    def test_read_to_delivered_refused(self):
        assert DeliveryStatus.READ.can_transition_to(DeliveryStatus.DELIVERED) is False

    # Any → FAILED is always allowed
    def test_queued_to_failed(self):
        assert DeliveryStatus.QUEUED.can_transition_to(DeliveryStatus.FAILED) is True

    def test_read_to_failed(self):
        assert DeliveryStatus.READ.can_transition_to(DeliveryStatus.FAILED) is True

    def test_sent_to_failed(self):
        assert DeliveryStatus.SENT.can_transition_to(DeliveryStatus.FAILED) is True

    # Same-state transitions are not "forward"
    def test_queued_to_queued_refused(self):
        assert DeliveryStatus.QUEUED.can_transition_to(DeliveryStatus.QUEUED) is False


# ---------------------------------------------------------------------------
# ComplianceMode / IdentityMode
# ---------------------------------------------------------------------------

class TestEnums:
    def test_compliance_official(self):
        assert ComplianceMode.OFFICIAL == "official"

    def test_compliance_experimental(self):
        assert ComplianceMode.EXPERIMENTAL == "experimental"

    def test_compliance_disabled(self):
        assert ComplianceMode.DISABLED == "disabled"

    def test_identity_bot(self):
        assert IdentityMode.BOT == "bot"

    def test_identity_business(self):
        assert IdentityMode.BUSINESS == "business"

    def test_identity_bridge_user(self):
        assert IdentityMode.BRIDGE_USER == "bridge_user"


# ---------------------------------------------------------------------------
# ResolvedTarget
# ---------------------------------------------------------------------------

class TestResolvedTarget:
    def test_fields(self):
        rt = ResolvedTarget(adapter="telegram", address="123456789")
        assert rt.adapter == "telegram"
        assert rt.address == "123456789"

    def test_display_hint_default_empty(self):
        rt = ResolvedTarget(adapter="discord", address="987")
        assert rt.display_hint == ""

    def test_display_hint_set(self):
        rt = ResolvedTarget(adapter="whatsapp", address="+1555", display_hint="Alice")
        assert rt.display_hint == "Alice"

    def test_immutable(self):
        rt = ResolvedTarget(adapter="sms", address="555")
        with pytest.raises((AttributeError, TypeError)):
            rt.adapter = "changed"  # type: ignore


# ---------------------------------------------------------------------------
# DeliveryReceipt
# ---------------------------------------------------------------------------

class TestDeliveryReceipt:
    def test_success_classmethod(self):
        r = DeliveryReceipt.success(message_id="msg-001")
        assert r.ok is True
        assert r.message_id == "msg-001"
        assert r.status == DeliveryStatus.SENT
        assert r.error is None

    def test_failure_classmethod(self):
        r = DeliveryReceipt.failure("Connection refused")
        assert r.ok is False
        assert r.status == DeliveryStatus.FAILED
        assert r.error == "Connection refused"

    def test_auto_timestamp_nonzero(self):
        before = time.time()
        r = DeliveryReceipt.success()
        after = time.time()
        assert before <= r.timestamp <= after

    def test_explicit_timestamp_kept(self):
        r = DeliveryReceipt(ok=True, timestamp=1234567890.0)
        assert r.timestamp == 1234567890.0

    def test_success_with_delivered_status(self):
        r = DeliveryReceipt.success(status=DeliveryStatus.DELIVERED)
        assert r.status == DeliveryStatus.DELIVERED

    def test_defaults_message_id_none(self):
        r = DeliveryReceipt(ok=True)
        assert r.message_id is None

    def test_immutable(self):
        r = DeliveryReceipt(ok=True)
        with pytest.raises((AttributeError, TypeError)):
            r.ok = False  # type: ignore


# ---------------------------------------------------------------------------
# InboundEvent
# ---------------------------------------------------------------------------

class TestInboundEvent:
    def _make(self, **kwargs):
        defaults = dict(
            adapter="telegram",
            remote_conversation_id="conv-001",
            sender="user123",
            text="Hello",
        )
        defaults.update(kwargs)
        return InboundEvent(**defaults)

    def test_basic_fields(self):
        ev = self._make()
        assert ev.adapter == "telegram"
        assert ev.sender == "user123"
        assert ev.text == "Hello"

    def test_auto_timestamp(self):
        before = time.time()
        ev = self._make()
        after = time.time()
        assert before <= ev.timestamp <= after

    def test_explicit_timestamp_kept(self):
        ev = self._make(timestamp=9999.0)
        assert ev.timestamp == 9999.0

    def test_attachments_default_empty(self):
        ev = self._make()
        assert ev.attachments == []

    def test_raw_default_empty(self):
        ev = self._make()
        assert ev.raw == {}

    def test_empty_text_default(self):
        ev = self._make(text="")
        assert ev.text == ""

    def test_immutable(self):
        ev = self._make()
        with pytest.raises((AttributeError, TypeError)):
            ev.sender = "other"  # type: ignore


# ---------------------------------------------------------------------------
# Thread
# ---------------------------------------------------------------------------

class TestThread:
    def _make(self, **kwargs):
        defaults = dict(
            id=1,
            adapter="discord",
            remote_conversation_id="ch-001",
        )
        defaults.update(kwargs)
        return Thread(**defaults)

    def test_basic_fields(self):
        t = self._make()
        assert t.id == 1
        assert t.adapter == "discord"
        assert t.remote_conversation_id == "ch-001"

    def test_status_default_open(self):
        t = self._make()
        assert t.status == "open"

    def test_contact_alias_default_none(self):
        t = self._make()
        assert t.contact_alias is None

    def test_meta_default_empty(self):
        t = self._make()
        assert t.meta == {}

    def test_mutable(self):
        t = self._make()
        t.status = "closed"
        assert t.status == "closed"
