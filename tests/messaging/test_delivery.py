"""
Tests for navig.messaging.delivery — DeliveryTracker SQLite audit log.
"""
import pytest

from navig.messaging.adapter import ComplianceMode, DeliveryReceipt, DeliveryStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tracker(tmp_path):
    from navig.messaging.delivery import DeliveryTracker

    return DeliveryTracker(db_path=tmp_path / "deliveries.db")


# ---------------------------------------------------------------------------
# Schema creation / basic lifecycle
# ---------------------------------------------------------------------------

class TestDeliveryTrackerSchema:
    def test_init_creates_db_file(self, tmp_path):
        t = _tracker(tmp_path)
        assert (tmp_path / "deliveries.db").exists()

    def test_stats_empty_initially(self, tmp_path):
        t = _tracker(tmp_path)
        assert t.stats() == {}

    def test_recent_empty_initially(self, tmp_path):
        t = _tracker(tmp_path)
        assert t.recent() == []


# ---------------------------------------------------------------------------
# record_send
# ---------------------------------------------------------------------------

class TestRecordSend:
    def test_returns_integer_id(self, tmp_path):
        t = _tracker(tmp_path)
        row_id = t.record_send(adapter="sms", target="+123")
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_second_record_has_higher_id(self, tmp_path):
        t = _tracker(tmp_path)
        id1 = t.record_send(adapter="sms", target="+1")
        id2 = t.record_send(adapter="sms", target="+2")
        assert id2 > id1

    def test_default_status_is_queued(self, tmp_path):
        t = _tracker(tmp_path)
        rid = t.record_send(adapter="email", target="a@b.com")
        row = t.get(rid)
        assert row["status"] == "queued"

    def test_stores_adapter_and_target(self, tmp_path):
        t = _tracker(tmp_path)
        rid = t.record_send(adapter="telegram", target="@alice")
        row = t.get(rid)
        assert row["adapter"] == "telegram"
        assert row["target"] == "@alice"

    def test_stores_contact_alias(self, tmp_path):
        t = _tracker(tmp_path)
        rid = t.record_send(adapter="sms", target="+1", contact_alias="alice")
        row = t.get(rid)
        assert row["contact_alias"] == "alice"

    def test_stores_thread_id(self, tmp_path):
        t = _tracker(tmp_path)
        rid = t.record_send(adapter="sms", target="+1", thread_id=99)
        row = t.get(rid)
        assert row["thread_id"] == 99

    def test_stores_compliance(self, tmp_path):
        t = _tracker(tmp_path)
        rid = t.record_send(
            adapter="email", target="x@y.com",
            compliance=ComplianceMode.EXPERIMENTAL
        )
        row = t.get(rid)
        assert row["compliance"] == ComplianceMode.EXPERIMENTAL.value


# ---------------------------------------------------------------------------
# update_status
# ---------------------------------------------------------------------------

class TestUpdateStatus:
    def test_advance_queued_to_sent(self, tmp_path):
        t = _tracker(tmp_path)
        rid = t.record_send(adapter="sms", target="+1")
        result = t.update_status(rid, DeliveryStatus.SENT)
        assert result is True
        assert t.get(rid)["status"] == "sent"

    def test_advance_sent_to_delivered(self, tmp_path):
        t = _tracker(tmp_path)
        rid = t.record_send(adapter="sms", target="+1")
        t.update_status(rid, DeliveryStatus.SENT)
        result = t.update_status(rid, DeliveryStatus.DELIVERED)
        assert result is True

    def test_invalid_backward_transition_returns_false(self, tmp_path):
        t = _tracker(tmp_path)
        rid = t.record_send(adapter="sms", target="+1")
        t.update_status(rid, DeliveryStatus.SENT)
        # sent → queued is invalid
        result = t.update_status(rid, DeliveryStatus.QUEUED)
        assert result is False

    def test_nonexistent_id_returns_false(self, tmp_path):
        t = _tracker(tmp_path)
        assert t.update_status(9999, DeliveryStatus.SENT) is False

    def test_stores_message_id(self, tmp_path):
        t = _tracker(tmp_path)
        rid = t.record_send(adapter="email", target="a@b.com")
        t.update_status(rid, DeliveryStatus.SENT, message_id="msg-abc")
        assert t.get(rid)["message_id"] == "msg-abc"

    def test_stores_error_on_failed(self, tmp_path):
        t = _tracker(tmp_path)
        rid = t.record_send(adapter="email", target="a@b.com")
        t.update_status(rid, DeliveryStatus.FAILED, error="timeout")
        assert t.get(rid)["error"] == "timeout"


# ---------------------------------------------------------------------------
# apply_receipt
# ---------------------------------------------------------------------------

class TestApplyReceipt:
    def test_apply_sent_receipt(self, tmp_path):
        t = _tracker(tmp_path)
        rid = t.record_send(adapter="sms", target="+1")
        receipt = DeliveryReceipt(ok=True, status=DeliveryStatus.SENT, message_id="rx1")
        result = t.apply_receipt(rid, receipt)
        assert result is True
        assert t.get(rid)["status"] == "sent"

    def test_apply_failed_receipt(self, tmp_path):
        t = _tracker(tmp_path)
        rid = t.record_send(adapter="sms", target="+1")
        receipt = DeliveryReceipt(ok=False, status=DeliveryStatus.FAILED, error="err")
        result = t.apply_receipt(rid, receipt)
        assert result is True
        assert t.get(rid)["status"] == "failed"


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

class TestGet:
    def test_get_returns_dict(self, tmp_path):
        t = _tracker(tmp_path)
        rid = t.record_send(adapter="sms", target="+1")
        row = t.get(rid)
        assert isinstance(row, dict)
        assert "id" in row

    def test_get_nonexistent_returns_none(self, tmp_path):
        t = _tracker(tmp_path)
        assert t.get(9999) is None


# ---------------------------------------------------------------------------
# recent
# ---------------------------------------------------------------------------

class TestRecent:
    def test_recent_returns_all(self, tmp_path):
        t = _tracker(tmp_path)
        for i in range(3):
            t.record_send(adapter="sms", target=f"+{i}")
        rows = t.recent()
        assert len(rows) == 3

    def test_recent_filter_by_adapter(self, tmp_path):
        t = _tracker(tmp_path)
        t.record_send(adapter="sms", target="+1")
        t.record_send(adapter="email", target="a@b.com")
        rows = t.recent(adapter="sms")
        assert all(r["adapter"] == "sms" for r in rows)
        assert len(rows) == 1

    def test_recent_filter_by_contact_alias(self, tmp_path):
        t = _tracker(tmp_path)
        t.record_send(adapter="sms", target="+1", contact_alias="alice")
        t.record_send(adapter="sms", target="+2", contact_alias="bob")
        rows = t.recent(contact_alias="alice")
        assert len(rows) == 1
        assert rows[0]["contact_alias"] == "alice"

    def test_recent_limit(self, tmp_path):
        t = _tracker(tmp_path)
        for i in range(10):
            t.record_send(adapter="sms", target=f"+{i}")
        rows = t.recent(limit=3)
        assert len(rows) == 3

    def test_recent_filter_by_status(self, tmp_path):
        t = _tracker(tmp_path)
        r1 = t.record_send(adapter="sms", target="+1")
        t.record_send(adapter="sms", target="+2")
        t.update_status(r1, DeliveryStatus.SENT)
        rows = t.recent(status="sent")
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_counts_by_status(self, tmp_path):
        t = _tracker(tmp_path)
        t.record_send(adapter="sms", target="+1")
        t.record_send(adapter="sms", target="+2")
        r3 = t.record_send(adapter="sms", target="+3")
        t.update_status(r3, DeliveryStatus.SENT)
        s = t.stats()
        assert s.get("queued", 0) == 2
        assert s.get("sent", 0) == 1
