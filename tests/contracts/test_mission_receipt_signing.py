"""Mission receipts are device-signed, and the signature covers what the mission produced.

Block receipts were already signed; mission receipts (every remediation, every
proactive run, every cycle of a standing goal) were not, so "proven by a verified
receipt" was true for one path and silently false for the other. These tests pin:
signed on both creation paths, still valid after a disk round-trip, tampering
detected, an unsigned legacy receipt reads as unsigned (never as tampered), and
`navig block verify-receipt <id>` finds a mission receipt in the store.
"""

from __future__ import annotations

import json

import pytest

from navig.contracts.execution_receipt import (
    ExecutionReceipt,
    ReceiptOutcome,
    result_digest,
)
from navig.contracts.mission import Mission
from navig.contracts.store import RuntimeStore
from navig.license.device_keys import verify_receipt_dict


@pytest.fixture(autouse=True)
def _isolated_device_key(tmp_path, monkeypatch):
    # The device key is real identity material — never let a test create or read
    # the operator's own ~/.navig/keys/device.key.
    monkeypatch.setattr(
        "navig.license.device_keys._device_key_path",
        lambda: tmp_path / "keys" / "device.key",
    )


def _started_mission(store: RuntimeStore, **kwargs) -> Mission:
    m = Mission(title="check disks", capability="agentic", **kwargs)
    store.create_mission(m)
    m.start()
    return m


def test_complete_mission_receipt_is_signed_and_covers_the_result(tmp_path):
    store = RuntimeStore(tmp_path / "rt")
    m = _started_mission(store)

    receipt = store.complete_mission(m.mission_id, succeeded=True, result={"free_gb": 42})

    ok, reason = verify_receipt_dict(receipt.to_dict())
    assert ok is True, reason
    assert receipt.artifacts["signer_kind"] == "device"
    assert receipt.artifacts["result_digest"] == result_digest({"free_gb": 42})


def test_terminal_path_receipt_is_signed(tmp_path):
    store = RuntimeStore(tmp_path / "rt")
    m = _started_mission(store)
    m.timeout()

    receipt = store.record_receipt_from_mission(m)

    assert receipt.outcome == ReceiptOutcome.TIMED_OUT
    ok, reason = verify_receipt_dict(receipt.to_dict())
    assert ok is True, reason


def test_signature_survives_a_disk_round_trip(tmp_path):
    store = RuntimeStore(tmp_path / "rt")
    m = _started_mission(store)
    receipt = store.complete_mission(m.mission_id, succeeded=True, result="3 drives, all healthy")
    store.flush()

    reloaded = RuntimeStore(tmp_path / "rt").get_receipt(receipt.receipt_id)

    assert reloaded is not None
    ok, reason = verify_receipt_dict(reloaded.to_dict())
    assert ok is True, reason


@pytest.mark.parametrize(
    "field,value",
    [("outcome", "failed"), ("title", "something else"), ("error", "injected")],
)
def test_tampering_with_the_receipt_is_detected(tmp_path, field, value):
    store = RuntimeStore(tmp_path / "rt")
    m = _started_mission(store)
    data = store.complete_mission(m.mission_id, succeeded=True, result="ok").to_dict()

    data[field] = value

    ok, reason = verify_receipt_dict(data)
    assert ok is False
    assert "tampered" in reason


def test_swapping_the_result_digest_is_detected(tmp_path):
    store = RuntimeStore(tmp_path / "rt")
    m = _started_mission(store)
    data = store.complete_mission(m.mission_id, succeeded=True, result="real output").to_dict()

    data["artifacts"]["result_digest"] = result_digest("forged output")

    assert verify_receipt_dict(data)[0] is False


def test_legacy_unsigned_receipt_reads_as_unsigned_not_tampered():
    legacy = ExecutionReceipt(
        mission_id="m-old",
        node_id="",
        title="from before signing",
        capability="agentic",
        outcome=ReceiptOutcome.SUCCEEDED,
        completed_at="2026-01-01T00:00:00Z",
    )

    assert verify_receipt_dict(legacy.to_dict()) == (None, "unsigned")


def test_no_result_means_no_digest(tmp_path):
    store = RuntimeStore(tmp_path / "rt")
    m = _started_mission(store)

    receipt = store.complete_mission(m.mission_id, succeeded=False, error="boom")

    assert "result_digest" not in receipt.artifacts
    assert verify_receipt_dict(receipt.to_dict())[0] is True


def test_verify_receipt_command_finds_a_mission_receipt_by_id(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from navig.commands.block import block_app

    store_dir = tmp_path / "rt"
    store = RuntimeStore(store_dir)
    m = _started_mission(store)
    receipt = store.complete_mission(m.mission_id, succeeded=True, result="ok")
    store.flush()
    monkeypatch.setattr("navig.contracts.store._default_store_dir", lambda: store_dir)

    result = CliRunner().invoke(block_app, ["verify-receipt", receipt.receipt_id, "--json"])

    assert result.exit_code == 0, result.output
    verdict = json.loads(result.output)
    assert verdict["signature"] == "valid"
    assert verdict["receipt_id"] == receipt.receipt_id


def test_verify_receipt_command_fails_on_an_unknown_id(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from navig.commands.block import block_app

    monkeypatch.setattr("navig.contracts.store._default_store_dir", lambda: tmp_path / "rt")

    result = CliRunner().invoke(block_app, ["verify-receipt", "no-such-receipt", "--json"])

    assert result.exit_code == 1
