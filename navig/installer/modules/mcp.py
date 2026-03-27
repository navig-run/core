"""Installer module: MCP configuration — ensure MCPManager config is initialized.

This module verifies that the MCP config file exists and the MCPManager is
importable.  It does NOT install specific MCP servers; that is left to the
operator via ``navig mcp install <name>``.

Included in: architect, system_standard, system_deep profiles.
"""

from __future__ import annotations

from pathlib import Path

from navig.installer.contracts import Action, InstallerContext, ModuleState, Result

name = "mcp"
description = "Initialize MCP configuration directory"


# ── helpers ──────────────────────────────────────────────────────────────────


def _mcp_config_path(ctx: InstallerContext) -> Path:
    return ctx.config_dir / "mcp_servers.yaml"


# ── module API ────────────────────────────────────────────────────────────────


def plan(ctx: InstallerContext) -> list[Action]:
    if _mcp_config_path(ctx).exists():
        return []
    return [
        Action(
            id="mcp.init_config",
            description="mcp: create mcp_servers.yaml",
            module=name,
            data={"config_path": str(_mcp_config_path(ctx))},
            reversible=True,
        )
    ]


def apply(action: Action, ctx: InstallerContext) -> Result:
    # Verify MCPManager import is possible
    try:
        from navig.mcp_manager import MCPManager  # noqa: F401 (import check only)
    except ImportError as exc:
        return Result(
            action_id=action.id,
            state=ModuleState.SKIPPED,
            message=f"MCPManager unavailable: {exc}",
        )

    config_path = Path(action.data["config_path"])

    # Stub config only if absent (idempotent)
    if config_path.exists():
        return Result(
            action_id=action.id,
            state=ModuleState.SKIPPED,
            message="mcp_servers.yaml already exists",
        )

    try:
        config_path.write_text(
            "# NAVIG MCP server registry\n# Add servers here or use: navig mcp install <name>\nservers: []\n",
            encoding="utf-8",
        )
        return Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            message="mcp_servers.yaml created",
            undo_data={"config_path": str(config_path), "created": True},
        )
    except Exception as exc:  # noqa: BLE001
        return Result(
            action_id=action.id,
            state=ModuleState.FAILED,
            message=str(exc),
        )


def rollback(action: Action, result: Result, ctx: InstallerContext) -> None:
    """Remove stub config file only if we created it."""
    undo = result.undo_data or {}
    if not undo.get("created"):
        return
    try:
        Path(undo["config_path"]).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
