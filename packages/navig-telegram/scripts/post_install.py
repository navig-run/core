"""
navig-telegram post_install.py
Runs once after the pack is installed.
Checks for the Telegram bot token and prints a setup message.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _check_token() -> str | None:
    """Look for TELEGRAM_BOT_TOKEN in the NAVIG vault or env."""
    # 1. NAVIG vault (primary, secure)
    try:
        from navig.vault.core_v2 import get_vault_v2  # type: ignore[import]

        vault = get_vault_v2()
        if vault is not None:
            import json

            raw = vault.get("telegram_bot_token")
            if raw:
                data = json.loads(raw) if isinstance(raw, (str, bytes)) else {}
                token = (data.get("value") or "").strip()
                if token:
                    return token
    except Exception as exc:  # noqa: BLE001
        logger.debug("Vault lookup skipped during post_install: %s", exc)

    # 2. Environment variable (CI, containers, legacy .env)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token

    # 3. ~/.navig/config.yaml (deprecated read-only fallback — migration aid)
    config_yaml = Path.home() / ".navig" / "config.yaml"
    if config_yaml.exists():
        try:
            import yaml

            cfg = yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
            token = cfg.get("telegram", {}).get("bot_token", "")
            if token:
                return token
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    return None


def main() -> None:
    print("navig-telegram post_install")
    print("\u2501" * 40)

    token = _check_token()

    if token:
        # Mask all but last 4 chars
        masked = "*" * max(0, len(token) - 4) + token[-4:]
        print(f"  \u2713  Bot token found: {masked}")
    else:
        print("  \u26a0  No Telegram bot token found.")
        print()
        print("  To configure, run the interactive wizard:")
        print("     navig init")
        print()
        print("  Or store the token directly in the vault (recommended):")
        print("     navig vault set telegram_bot_token <your-token>")
        print()
        print("  Get a token from @BotFather on Telegram.")

    print()
    print("  Start the bot:  navig telegram start")
    print()


if __name__ == "__main__":
    main()
