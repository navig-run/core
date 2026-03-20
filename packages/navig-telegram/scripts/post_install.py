"""
navig-telegram post_install.py
Runs once after the pack is installed.
Checks for the Telegram bot token and prints a setup message.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _check_token() -> str | None:
    """Look for TELEGRAM_BOT_TOKEN in .navig/config.json, ~/.navig/config.yaml, or env."""
    # 1. Environment variable (CI, containers)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token

    # 2. ~/.navig/config.yaml
    config_yaml = Path.home() / ".navig" / "config.yaml"
    if config_yaml.exists():
        try:
            import yaml
            cfg = yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
            token = cfg.get("telegram", {}).get("bot_token", "")
            if token:
                return token
        except Exception:
            pass

    # 3. .navig/config.json (project-local)
    config_json = Path(".navig") / "config.json"
    if config_json.exists():
        try:
            import json
            cfg = json.loads(config_json.read_text(encoding="utf-8"))
            token = cfg.get("telegram", {}).get("bot_token", "")
            if token:
                return token
        except Exception:
            pass

    return None


def main() -> None:
    print("navig-telegram post_install")
    print("━" * 40)

    token = _check_token()

    if token:
        # Mask all but last 4 chars
        masked = "*" * max(0, len(token) - 4) + token[-4:]
        print(f"  ✓  Bot token found: {masked}")
    else:
        print("  ⚠  No Telegram bot token found.")
        print()
        print("  To configure, choose ONE option:")
        print()
        print("  A) Environment variable:")
        print("     set TELEGRAM_BOT_TOKEN=<your-token>")
        print()
        print("  B) ~/.navig/config.yaml:")
        print("     telegram:")
        print("       bot_token: <your-token>")
        print()
        print("  C) .navig/config.json in your project:")
        print('     {"telegram": {"bot_token": "<your-token>"}}')
        print()
        print("  Get a token from @BotFather on Telegram.")

    print()
    print("  Start the bot:  navig telegram start")
    print()


if __name__ == "__main__":
    main()