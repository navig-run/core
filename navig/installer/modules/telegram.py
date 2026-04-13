"""Installer module: Telegram bot token — dual-write (vault / .env).

Token source priority (non-interactive):
  1. ctx.extra["telegram_bot_token"]           (programmatic / test injection)
  2. env var NAVIG_TELEGRAM_BOT_TOKEN          (set by the operator before running install)
  3. env var TELEGRAM_BOT_TOKEN                (compatibility fallback)

If no token is present the module emits a single SKIPPED action so the
operator profile continues without error.  Telegram is always optional.

The token is stored only in the vault (primary) and .env (legacy daemon
loader).  Writing the token to config.yaml in plaintext is deprecated and
has been removed.  Use `navig vault set telegram_bot_token <token>` to
store or update the token at any time.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from navig.core.file_permissions import set_owner_only_file_permissions
from navig.installer.contracts import Action, InstallerContext, ModuleState, Result

name = "telegram"
description = "Configure Telegram bot token (vault + .env)"


# ── helpers ──────────────────────────────────────────────────────────────────


def _marker(ctx: InstallerContext) -> Path:
    return ctx.config_dir / ".telegram_configured"


def _token_from_ctx(ctx: InstallerContext) -> str:
    """Return token string or '' if not available."""
    token = (
        ctx.extra.get("telegram_bot_token")
        or os.environ.get("NAVIG_TELEGRAM_BOT_TOKEN", "")
        or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    )
    return (token or "").strip()


# ── module API ────────────────────────────────────────────────────────────────


def plan(ctx: InstallerContext) -> list[Action]:
    if _marker(ctx).exists():
        return []

    token = _token_from_ctx(ctx)
    if not token:
        return [
            Action(
                id="telegram.skip",
                description="telegram: no token provided — skipping",
                module=name,
                data={"skipped": True},
                reversible=False,
            )
        ]

    return [
        Action(
            id="telegram.write",
            description="telegram: write bot token (vault + .env)",
            module=name,
            data={"token": token},
            reversible=True,
        )
    ]


def apply(action: Action, ctx: InstallerContext) -> Result:
    # Placeholder / no-token action
    if action.data.get("skipped"):
        return Result(
            action_id=action.id,
            state=ModuleState.SKIPPED,
            message="no token provided",
        )

    token: str = action.data["token"]
    writes: list[str] = []

    # 1. Vault (primary, secure)
    try:
        from navig.vault.core import get_vault  # type: ignore[import]

        vault = get_vault()
        if vault is not None:
            vault.put(
                "telegram_bot_token",
                json.dumps({"value": token}).encode(),
            )
            writes.append("vault")
    except Exception:  # noqa: BLE001
        pass

    # 2. .env file (legacy; used by daemon env loading)
    env_path = ctx.config_dir / ".env"
    try:
        existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        lines = [ln for ln in existing.splitlines() if not ln.startswith("TELEGRAM_BOT_TOKEN=")]
        lines.append(f"TELEGRAM_BOT_TOKEN={token}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        set_owner_only_file_permissions(env_path)
        writes.append(".env")
    except Exception:  # noqa: BLE001
        pass

    _marker(ctx).write_text("1", encoding="utf-8")

    return Result(
        action_id=action.id,
        state=ModuleState.APPLIED,
        message=f"token saved ({', '.join(writes) or 'nowhere'})",
        undo_data={
            "env_path": str(env_path),
            "token": token,
        },
    )


def rollback(action: Action, result: Result, ctx: InstallerContext) -> None:
    """Remove marker and scrub token from .env."""
    _marker(ctx).unlink(missing_ok=True)

    undo = result.undo_data or {}

    # Scrub .env
    env_path = Path(undo.get("env_path", ctx.config_dir / ".env"))
    try:
        if env_path.exists():
            lines = [
                ln
                for ln in env_path.read_text(encoding="utf-8").splitlines()
                if not ln.startswith("TELEGRAM_BOT_TOKEN=")
            ]
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
