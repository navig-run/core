"""
scripts/post_install.py — NAVIG post-install hook for telegram-bot-navig.

Called automatically by `navig plugin install telegram-bot-navig` after the
plugin files are written.  Verifies that python-telegram-bot is importable and
prints a setup reminder for the required config keys.

Exit code 0 on success; non-zero causes the installer to show a warning.
"""

from __future__ import annotations

import sys


def main() -> int:
    print("telegram-bot-navig: running post-install checks…")

    # Verify python-telegram-bot is available (installed from plugin.json pip dep)
    try:
        import telegram  # noqa: F401

        print(f"  ✅ python-telegram-bot found (version: {telegram.__version__})")
    except ImportError:
        print(
            "  ⚠️  python-telegram-bot not found.\n"
            "      Install it with:  pip install 'python-telegram-bot>=20.0'\n"
            "      or let NAVIG handle it:  navig plugin sync-deps telegram-bot-navig",
            file=sys.stderr,
        )
        return 1

    # Remind user about required config
    print()
    print("  Next steps:")
    print("  1. Add your bot token to ~/.navig/config.yaml:")
    print("       telegram_bot_token: <your BotFather token>")
    print("  2. (Optional) set a target chat ID:")
    print("       telegram_chat_id: <chat or channel ID>")
    print("  3. Start the bot:  python plugins/telegram-bot-navig/bot.py")
    print()
    print("  telegram-bot-navig post-install complete ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
