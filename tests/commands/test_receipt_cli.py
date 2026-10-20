"""``navig receipt`` — the direct CLI path to a signed ExecutionReceipt.

Receipts were only ever a side effect of missions/block runs; ``receipt record``
makes a verdict (a review sign-off) the same durable, Ed25519-signed artifact,
``verify`` answers with an exit code an agent can gate a landing on, and an
unsigned or unsignable verdict is refused rather than persisted.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig.commands.receipt import receipt_app
from navig.contracts import store as rt_store

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    # The device key is real identity material — never let a test read the
    # operator's own ~/.navig/keys/device.key (pattern from the mission-receipt
    # signing tests).
    monkeypatch.setattr(
        "navig.license.device_keys._device_key_path",
        lambda: tmp_path / "keys" / "device.key",
    )
    rt_store.reset_runtime_store(tmp_path / "rt")


def _record(extra: list[str] | None = None) -> dict:
    args = [
        "record",
        "--mission-id", "review:ledger:abc123def456",
        "--title", "Review of the ledger task",
        "--capability", "review_signoff",
        "--node-id", "claude:reviewer-session",
        "--json",
    ]
    res = runner.invoke(receipt_app, args + (extra or []))
    assert res.exit_code == 0, res.output
    return json.loads(res.output)


def test_record_signs_and_verify_confirms():
    row = _record(["--metadata", json.dumps({"verdict": "approve", "commit": "abc"})])
    assert row["signature"] == "valid"
    assert row["metadata"]["verdict"] == "approve"

    ver = runner.invoke(receipt_app, ["verify", row["receipt_id"], "--json"])
    assert ver.exit_code == 0
    payload = json.loads(ver.output)
    assert payload["signature"] == "valid"
    assert payload["verdict"] == "approve"
    assert payload["mission_id"] == "review:ledger:abc123def456"


def test_reject_maps_to_failed_outcome():
    row = _record(["--outcome", "failed", "--metadata", '{"verdict": "reject"}'])
    assert row["outcome"] == "failed"
    listed = json.loads(runner.invoke(receipt_app, ["list", "--json"]).output)
    assert listed["count"] == 1
    assert listed["receipts"][0]["outcome"] == "failed"
    assert listed["receipts"][0]["metadata"]["verdict"] == "reject"


def test_list_filters_by_mission_glob():
    _record()
    _record2 = runner.invoke(
        receipt_app,
        [
            "record",
            "--mission-id", "audit:something-else",
            "--title", "other",
            "--capability", "audit",
            "--json",
        ],
    )
    assert _record2.exit_code == 0

    only_reviews = json.loads(
        runner.invoke(receipt_app, ["list", "--mission-id", "review:*", "--json"]).output
    )
    assert only_reviews["count"] == 1
    assert only_reviews["receipts"][0]["mission_id"].startswith("review:")


def test_bad_outcome_and_bad_metadata_are_refused():
    bad_outcome = runner.invoke(
        receipt_app,
        ["record", "--mission-id", "m", "--title", "t", "--capability", "c", "--outcome", "approved"],
    )
    assert bad_outcome.exit_code == 1
    assert "Unknown outcome" in bad_outcome.output

    bad_json = runner.invoke(
        receipt_app,
        ["record", "--mission-id", "m", "--title", "t", "--capability", "c", "--metadata", "{oops"],
    )
    assert bad_json.exit_code == 1

    not_object = runner.invoke(
        receipt_app,
        ["record", "--mission-id", "m", "--title", "t", "--capability", "c", "--metadata", "[1]"],
    )
    assert not_object.exit_code == 1

    # Nothing above may have reached the store.
    assert rt_store.get_runtime_store().list_receipts() == []


def test_verify_of_an_unknown_receipt_exits_nonzero():
    res = runner.invoke(receipt_app, ["verify", "does-not-exist", "--json"])
    assert res.exit_code == 1
    payload = json.loads(res.output)
    assert payload["error"] == "receipt not found"
