"""Build and persist the execution receipt for a block run.

A receipt is a terminal artifact: it is written only after the run reaches a
terminal state, and it binds *what ran* to *what was proven* — the block digest,
runner version, verification level, evidence, redacted inputs/outputs, and child
receipts. The ``signature`` field is present but ``null`` in Stage 1; device
signing lands in Stage 3 without changing the shape.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from navig.blocks.loader import Block
from navig.blocks.policy import Redactor
from navig.blocks.runner import BlockRunResult
from navig.contracts.execution_receipt import ExecutionReceipt, ReceiptOutcome

RUNNER_VERSION = "blocks/1"

_OUTCOME_MAP = {
    "succeeded": ReceiptOutcome.SUCCEEDED,
    "failed": ReceiptOutcome.FAILED,
    "cancelled": ReceiptOutcome.CANCELLED,
    "partial": ReceiptOutcome.FAILED,
}


def _duration(started: str, completed: str) -> float | None:
    try:
        s = datetime.fromisoformat(started)
        c = datetime.fromisoformat(completed)
        return max(0.0, (c - s).total_seconds())
    except (ValueError, TypeError):
        return None


def build_receipt(
    block: Block,
    run: BlockRunResult,
    inputs: dict[str, Any],
    *,
    trust: str,
    redactor: Redactor | None = None,
) -> ExecutionReceipt:
    """Create the immutable receipt for a finished run (inputs auto-redacted)."""
    redactor = redactor or Redactor()
    # Redact secret input *values* by key, then deep-scrub any known secret string.
    secret_keys = block.secret_keys()
    safe_inputs = {
        k: ("‹secret›" if k in secret_keys else v) for k, v in inputs.items()
    }
    safe_inputs = redactor.scrub_obj(safe_inputs)

    steps = [
        {
            "id": s.id,
            "kind": s.kind,
            "status": s.status,
            "risk": s.risk,
            "mode": s.mode,
            "detail": s.detail,
            "verify_passed": s.verify_passed,
            "child_receipt": s.child_receipt,
        }
        for s in run.steps
    ]

    artifacts = {
        "block_digest": run.block_digest,
        "runner_version": RUNNER_VERSION,
        "trust": trust,
        "verification_level": run.verification_level,
        "inputs": safe_inputs,
        "outputs": redactor.scrub_obj(run.outputs),
        "steps": steps,
        "evidence": redactor.scrub_obj(run.evidence),
        "chain": [s.child_receipt for s in run.steps if s.child_receipt],
        "run_id": run.run_id,
        "journal": run.journal_path,
        "signature": None,  # device signing: Stage 3
        "signer_kind": None,
    }

    receipt = ExecutionReceipt(
        mission_id=f"block:{block.id}:{run.run_id}",
        node_id="local",
        title=block.name,
        capability=f"block:{block.id}",
        outcome=_OUTCOME_MAP.get(run.outcome, ReceiptOutcome.FAILED),
        completed_at=run.completed_at or run.started_at,
        started_at=run.started_at,
        duration_secs=_duration(run.started_at, run.completed_at),
        error=redactor.scrub(run.error) if run.error else None,
        artifacts=artifacts,
        metadata={"block_id": block.id, "block_version": block.version},
    )
    _sign(receipt)  # attach a device Ed25519 signature (best-effort)
    return receipt


def _sign(receipt: ExecutionReceipt) -> None:
    """Attach a device signature in-place. The frozen dataclass forbids field
    reassignment but the ``artifacts`` dict is mutable, so we update it there."""
    try:
        from navig.license.device_keys import sign_receipt_dict

        sig = sign_receipt_dict(receipt.to_dict())
        receipt.artifacts.update(sig)
    except Exception as exc:  # noqa: BLE001 — signing is best-effort; unsigned stays valid
        logger.debug("blocks.receipts: signing skipped: {}", exc)


def receipts_dir() -> Path:
    from navig.platform.paths import config_dir

    d = config_dir() / "runtime" / "receipts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def persist_receipt(receipt: ExecutionReceipt) -> Path:
    """Write the standalone receipt JSON and best-effort append to the RuntimeStore."""
    path = receipts_dir() / f"{receipt.receipt_id}.json"
    path.write_text(receipt.to_json(), encoding="utf-8")

    try:
        from navig.contracts.store import RuntimeStore  # type: ignore

        store = RuntimeStore()
        store.record_receipt(receipt)
        store.flush()
    except Exception as exc:  # noqa: BLE001
        logger.debug("blocks.receipts: RuntimeStore append skipped: {}", exc)

    return path
