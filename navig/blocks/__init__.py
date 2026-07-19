"""NAVIG Blocks — installable, sellable, machine-executable outcomes.

A **block** packages a procedure ("publish this build to Steam", "harden this
VPS") as a signed, versioned, verifiable unit an agent can *apply* to real
infrastructure. It is the execution-and-commerce layer on top of skills:

    prompt      →  text you paste
    skill       →  knowledge the agent pulls in
    block       →  an outcome the agent applies, with inputs, verification,
                   provenance, and a receipt

Public API::

    from navig.blocks import (
        Block, parse_block_file, discover_blocks, find_block, validate_block,
        apply_block, build_receipt, persist_receipt,
    )

See ``loader`` (static), ``policy`` (trust/risk/secrets/lockfile),
``runner`` (execution + journal + verify), ``receipts`` (audit artifact).
"""

from __future__ import annotations

from navig.blocks.loader import (
    SPEC_VERSION,
    Block,
    BlockError,
    BlockInput,
    BlockStep,
    BlockVerify,
    blocks_by_id,
    compute_digest,
    discover_blocks,
    find_block,
    get_block_dirs,
    lint_block,
    normalize_legacy_asset,
    parse_block_file,
    validate_block,
    write_skill_shim,
)
from navig.blocks.policy import (
    PolicyError,
    TrustError,
    capability_risk,
    check_install_trust,
    read_lockfile,
    write_lock_entry,
)
from navig.blocks.receipts import build_receipt, persist_receipt
from navig.blocks.runner import BlockRunResult, apply_block

__all__ = [
    "SPEC_VERSION",
    "Block",
    "BlockError",
    "BlockInput",
    "BlockStep",
    "BlockVerify",
    "BlockRunResult",
    "PolicyError",
    "TrustError",
    "apply_block",
    "blocks_by_id",
    "build_receipt",
    "capability_risk",
    "check_install_trust",
    "compute_digest",
    "discover_blocks",
    "find_block",
    "get_block_dirs",
    "lint_block",
    "normalize_legacy_asset",
    "parse_block_file",
    "persist_receipt",
    "read_lockfile",
    "validate_block",
    "write_lock_entry",
    "write_skill_shim",
]
