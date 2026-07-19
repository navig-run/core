"""MCP tool bundle: NAVIG Blocks — list, inspect, and apply outcomes.

Exposes the block engine to any MCP client (Claude Code / Cursor / VS Code Copilot /
Claude Desktop). This is how a foreign agent *runs an outcome*: `navig_block_apply`
drives the same runner as `navig apply`, non-interactively.

Non-interactive contract (MCP has no TTY):
  * secrets resolve from env / vault only — never a prompt (a missing secret fails
    the tool with a clear message rather than hanging);
  * destructive steps require their step ids in `approvals` (a blanket run is not
    enough — same policy as the CLI's `--approve`);
  * the tool returns the redacted receipt so the caller has verifiable proof.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def register(server: Any) -> None:
    """Register block tools (schemas + handlers) on the MCP server."""
    server.tools.update(
        {
            "navig_block_list": {
                "name": "navig_block_list",
                "description": (
                    "List installed NAVIG blocks (installable, verifiable outcomes you apply). "
                    "Returns id, version, category, verify kind, whether paid, and the input schema."
                ),
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            "navig_block_show": {
                "name": "navig_block_show",
                "description": "Show one block's full spec: typed inputs, ordered steps with computed risk, and the verify criterion.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"id": {"type": "string", "description": "Block id"}},
                    "required": ["id"],
                },
            },
            "navig_block_apply": {
                "name": "navig_block_apply",
                "description": (
                    "Apply a block — run its outcome end-to-end and return a verified receipt. "
                    "Secrets resolve from env/vault only. Destructive steps require their step ids "
                    "in 'approvals'. Use dry_run to preview the plan without executing."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Block id (must be installed)"},
                        "inputs": {"type": "object", "description": "Key/value map of the block's non-secret inputs"},
                        "approvals": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Step ids to approve (required for destructive steps)",
                        },
                        "dry_run": {"type": "boolean", "description": "Preview the plan; resolve no secrets, no writes", "default": False},
                        "workdir": {"type": "string", "description": "Working directory (default: current space root)"},
                    },
                    "required": ["id"],
                },
            },
        }
    )
    server._tool_handlers.update(
        {
            "navig_block_list": _tool_block_list,
            "navig_block_show": _tool_block_show,
            "navig_block_apply": _tool_block_apply,
        }
    )


def _input_schema(block) -> list[dict]:
    return [
        {
            "key": i.key,
            "type": i.type,
            "required": i.required,
            "secret": i.is_secret,
            "label": i.label,
            "values": i.values or None,
        }
        for i in block.inputs
    ]


def _tool_block_list(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    from navig.blocks import discover_blocks

    blocks = discover_blocks()
    return {
        "blocks": [
            {
                "id": b.id,
                "name": b.name,
                "version": b.version,
                "category": b.category,
                "description": b.description,
                "verify": b.verify.kind,
                "paid": bool(b.marketplace),
                "inputs": _input_schema(b),
            }
            for b in sorted(blocks, key=lambda x: x.id)
        ]
    }


def _tool_block_show(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    from navig.blocks import capability_risk, find_block
    from navig.blocks.policy import unmet_requirements

    b = find_block(str(args.get("id", "")))
    if b is None:
        return {"error": f"block '{args.get('id')}' not found"}
    return {
        "id": b.id,
        "name": b.name,
        "version": b.version,
        "category": b.category,
        "license": b.license,
        "description": b.description,
        "digest": b.digest,
        "inputs": _input_schema(b),
        "steps": [
            {"id": s.id, "kind": s.kind, "risk": capability_risk(s.capabilities), "capabilities": s.capabilities}
            for s in b.steps
        ],
        "verify": {"kind": b.verify.kind, "level": b.verify.level},
        "requires": b.requires or {},
        "unmet_requirements": unmet_requirements(b.requires),
        "paid": bool(b.marketplace),
    }


def _mcp_secret_resolver(inp):
    """Resolve a secret input in a non-interactive MCP context (no prompt)."""
    env_key = f"NAVIG_BLOCK_SECRET__{inp.key.upper()}"
    if env_key in os.environ:
        return os.environ[env_key]
    if inp.vault:
        try:
            # `get_secret` never existed as a module function — this import always failed,
            # so vaulted MCP block inputs silently resolved to nothing. reveal_secret is the
            # canonical reader (str, "" if missing, never raises).
            from navig.vault import get_vault, reveal_secret

            name = inp.vault.strip("{} ").split(".", 1)[-1]
            val = reveal_secret(get_vault(), name)
            if val:
                return str(val)
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError(
        f"secret input '{inp.key}' is unavailable in an MCP context — "
        f"set env NAVIG_BLOCK_SECRET__{inp.key.upper()} or store it in the vault"
    )


def _tool_block_apply(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    from navig.blocks import build_receipt, find_block, persist_receipt, validate_block
    from navig.blocks.runner import apply_block
    from navig.platform.paths import find_app_root

    block = find_block(str(args.get("id", "")))
    if block is None:
        return {"error": f"block '{args.get('id')}' not installed. Install it with `navig install add block:...`"}

    problems = validate_block(block)
    if problems:
        return {"error": "block is invalid", "problems": problems}

    inputs = dict(args.get("inputs") or {})
    approvals = set(args.get("approvals") or [])
    dry_run = bool(args.get("dry_run", False))
    wd = Path(args["workdir"]) if args.get("workdir") else (find_app_root() or Path.cwd())

    try:
        run = apply_block(
            block, inputs, yes=True, dry_run=dry_run, approvals=approvals, workdir=wd,
            secret_resolver=_mcp_secret_resolver,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"apply failed: {exc}"}

    if dry_run:
        return {
            "outcome": run.outcome,
            "plan": [{"id": s.id, "kind": s.kind, "risk": s.risk} for s in run.steps],
        }

    receipt = build_receipt(block, run, inputs, trust="first-party")
    path = persist_receipt(receipt)
    return {
        "outcome": run.outcome,
        "verification_level": run.verification_level,
        "steps": [{"id": s.id, "kind": s.kind, "status": s.status, "risk": s.risk} for s in run.steps],
        "outputs": run.outputs,
        "error": run.error,
        "receipt_id": receipt.receipt_id,
        "receipt_path": str(path),
    }
