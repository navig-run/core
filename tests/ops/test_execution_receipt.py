"""Tests for navig.contracts.execution_receipt."""
from __future__ import annotations

import json

import pytest

from navig.contracts.execution_receipt import ExecutionReceipt, ReceiptOutcome


# ── helpers ──────────────────────────────────────────────────


def _receipt(**kwargs) -> ExecutionReceipt:
    defaults = dict(
        mission_id="m-001",
        node_id="n-001",
        title="Test Mission",
        capability="bash",
        outcome=ReceiptOutcome.SUCCEEDED,
        completed_at="2024-01-01T00:00:00Z",
    )
    defaults.update(kwargs)
    return ExecutionReceipt(**defaults)


# ── ReceiptOutcome ────────────────────────────────────────────


class TestReceiptOutcome:
    def test_values(self):
        assert ReceiptOutcome.SUCCEEDED.value == "succeeded"
        assert ReceiptOutcome.FAILED.value == "failed"
        assert ReceiptOutcome.CANCELLED.value == "cancelled"
        assert ReceiptOutcome.TIMED_OUT.value == "timed_out"


# ── Construction ──────────────────────────────────────────────


class TestConstruction:
    def test_basic_fields(self):
        r = _receipt()
        assert r.mission_id == "m-001"
        assert r.node_id == "n-001"
        assert r.title == "Test Mission"
        assert r.capability == "bash"
        assert r.outcome == ReceiptOutcome.SUCCEEDED

    def test_receipt_id_auto_generated(self):
        r = _receipt()
        assert r.receipt_id  # non-empty
        assert len(r.receipt_id) == 36  # UUID4 format

    def test_different_receipts_have_unique_ids(self):
        r1 = _receipt()
        r2 = _receipt()
        assert r1.receipt_id != r2.receipt_id

    def test_recorded_at_auto_set(self):
        r = _receipt()
        assert r.recorded_at  # non-empty ISO timestamp

    def test_immutable_assignment_raises(self):
        r = _receipt()
        with pytest.raises((AttributeError, TypeError)):
            r.outcome = ReceiptOutcome.FAILED  # type: ignore

    def test_artifacts_default_empty(self):
        r = _receipt()
        assert r.artifacts == {}

    def test_metadata_default_empty(self):
        r = _receipt()
        assert r.metadata == {}

    def test_optional_fields_default_none(self):
        r = _receipt()
        assert r.error is None
        assert r.started_at is None
        assert r.duration_secs is None


# ── from_mission ──────────────────────────────────────────────


class TestFromMission:
    def test_basic(self):
        r = ExecutionReceipt.from_mission(
            mission_id="m-002",
            node_id="n-002",
            title="Another Mission",
            capability="python",
            outcome=ReceiptOutcome.FAILED,
            completed_at="2024-06-01T12:00:00Z",
        )
        assert r.mission_id == "m-002"
        assert r.outcome == ReceiptOutcome.FAILED

    def test_with_error(self):
        r = ExecutionReceipt.from_mission(
            mission_id="m", node_id="n", title="t", capability="c",
            outcome=ReceiptOutcome.FAILED, completed_at="2024-01-01T00:00:00Z",
            error="Connection refused",
        )
        assert r.error == "Connection refused"

    def test_artifacts_passed_through(self):
        r = ExecutionReceipt.from_mission(
            mission_id="m", node_id="n", title="t", capability="c",
            outcome=ReceiptOutcome.SUCCEEDED, completed_at="2024-01-01T00:00:00Z",
            artifacts={"output": "hello"},
        )
        assert r.artifacts["output"] == "hello"

    def test_none_artifacts_becomes_empty_dict(self):
        r = ExecutionReceipt.from_mission(
            mission_id="m", node_id="n", title="t", capability="c",
            outcome=ReceiptOutcome.SUCCEEDED, completed_at="2024-01-01T00:00:00Z",
            artifacts=None,
        )
        assert r.artifacts == {}


# ── predicates ────────────────────────────────────────────────


class TestPredicates:
    def test_is_success_true_for_succeeded(self):
        assert _receipt(outcome=ReceiptOutcome.SUCCEEDED).is_success is True

    def test_is_success_false_for_failed(self):
        assert _receipt(outcome=ReceiptOutcome.FAILED).is_success is False

    def test_is_failure_true_for_failed(self):
        assert _receipt(outcome=ReceiptOutcome.FAILED).is_failure is True

    def test_is_failure_true_for_timed_out(self):
        assert _receipt(outcome=ReceiptOutcome.TIMED_OUT).is_failure is True

    def test_is_failure_false_for_succeeded(self):
        assert _receipt(outcome=ReceiptOutcome.SUCCEEDED).is_failure is False

    def test_is_failure_false_for_cancelled(self):
        assert _receipt(outcome=ReceiptOutcome.CANCELLED).is_failure is False


# ── serialization ─────────────────────────────────────────────


class TestSerialization:
    def test_to_dict_contains_outcome_as_string(self):
        d = _receipt().to_dict()
        assert d["outcome"] == "succeeded"

    def test_to_dict_contains_all_required_keys(self):
        d = _receipt().to_dict()
        for key in ("mission_id", "node_id", "title", "capability", "outcome", "completed_at"):
            assert key in d

    def test_to_json_is_valid_json(self):
        raw = _receipt().to_json()
        parsed = json.loads(raw)
        assert parsed["outcome"] == "succeeded"

    def test_from_dict_roundtrip(self):
        original = _receipt(outcome=ReceiptOutcome.CANCELLED, error="User cancelled")
        d = original.to_dict()
        restored = ExecutionReceipt.from_dict(d)
        assert restored.outcome == ReceiptOutcome.CANCELLED
        assert restored.error == "User cancelled"
        assert restored.mission_id == original.mission_id

    def test_from_json_roundtrip(self):
        original = _receipt(outcome=ReceiptOutcome.TIMED_OUT)
        raw = original.to_json()
        restored = ExecutionReceipt.from_json(raw)
        assert restored.outcome == ReceiptOutcome.TIMED_OUT
        assert restored.receipt_id == original.receipt_id


# ── repr ──────────────────────────────────────────────────────


class TestRepr:
    def test_repr_contains_outcome(self):
        r = _receipt()
        assert "succeeded" in repr(r)

    def test_repr_contains_prefix_ids(self):
        r = _receipt(mission_id="abcdef12-xxxx", node_id="zzzzabcd-xxxx")
        text = repr(r)
        assert "abcdef12" in text
