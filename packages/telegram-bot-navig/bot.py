"""
bot.py — Entry point for the telegram-bot-navig plugin bot.

Token resolution order (first non-empty wins):
  1. NAVIG vault  (navig.vault → provider="telegram", key api_key/token)
  2. env var      TELEGRAM_BOT_TOKEN
  3. config.yaml  telegram.nas_token  (deprecated, read-only fallback)
  4. config.yaml  telegram.bot_token  (deprecated, read-only fallback)
  5. config.yaml  telegram_bot_token  (deprecated, read-only fallback)

Store the token in the vault (recommended):
    navig vault set telegram_bot_token <your-bot-token>

Pip dependencies declared in plugin.json → depends.pip are auto-installed
on every startup so new plugin requirements are always satisfied.

Usage:
    python bot.py
    TELEGRAM_BOT_TOKEN=xxx python bot.py   # override
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent


# ---------------------------------------------------------------------------
# 1. Auto-install pip dependencies declared in plugin.json
# ---------------------------------------------------------------------------


def _auto_install_deps() -> None:
    """
    Read depends.pip from plugin.json and pip-install any missing packages.
    Runs `pip install --quiet --upgrade <pkg>` only when the import fails,
    so subsequent starts are instant.
    """
    manifest = _HERE / "plugin.json"
    if not manifest.exists():
        return

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        pip_deps: list[str] = data.get("depends", {}).get("pip", [])
    except Exception as exc:
        logger.warning("Could not parse plugin.json: %s", exc)
        return

    if not pip_deps:
        return

    to_install: list[str] = []
    for dep in pip_deps:
        # Extract bare package name (strip version specifiers)
        pkg_name = (
            dep.split("[")[0]
            .split(">")[0]
            .split("<")[0]
            .split("=")[0]
            .split("!")[0]
            .strip()
        )
        # Normalise: replace hyphens with underscores for importlib check
        import_name = pkg_name.replace("-", "_").replace(".", "_").lower()
        # Special-case known import-name mismatches
        _IMPORT_MAP = {
            "python_telegram_bot": "telegram",
            "pyyaml": "yaml",
            "yt_dlp": "yt_dlp",
            "pillow": "PIL",
        }
        import_name = _IMPORT_MAP.get(import_name, import_name)
        try:
            __import__(import_name)
        except ImportError:
            to_install.append(dep)

    if not to_install:
        return

    logger.info("Auto-installing missing deps: %s", to_install)
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade"]
            + to_install,
            stdout=subprocess.DEVNULL,
        )
        logger.info("Deps installed successfully.")
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "pip install failed (exit %s) — some plugins may not work.", exc.returncode
        )


# ---------------------------------------------------------------------------
# 2. Token resolution — vault → env → config (deprecated fallback)
# ---------------------------------------------------------------------------


def _get_token() -> str:
    """Resolve Telegram bot token from NAVIG vault, env, or config (deprecated fallback)."""

    # ── 2a. NAVIG Vault ────────────────────────────────────────────────────
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # navig-core root
        from navig.vault import get_vault  # type: ignore[import]

        token = get_vault().get_api_key("telegram") or ""
        if token.strip():
            logger.info("Token resolved from NAVIG vault (provider=telegram)")
            return token.strip()
    except Exception as exc:
        logger.debug("Vault lookup skipped: %s", exc)

    # ── 2b. Environment variable ───────────────────────────────────────────
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        logger.info("Token resolved from TELEGRAM_BOT_TOKEN env var")
        return token

    # ── 2c. ~/.navig/config.yaml (deprecated read-only fallback) ──────────
    # bot_token in config.yaml is deprecated.  No new token writes go here.
    # Existing values are read to ease migration but a warning is logged.
    try:
        import yaml  # installed by auto_install above or already present

        cfg_path = Path.home() / ".navig" / "config.yaml"
        if cfg_path.exists():
            cfg: dict = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

            tg_block = cfg.get("telegram", {})
            for key in ("nas_token", "bot_token", "token", "api_key"):
                val = (
                    tg_block.get(key, "").strip() if isinstance(tg_block, dict) else ""
                )
                if val:
                    logger.warning(
                        "Token resolved from config.yaml telegram.%s (deprecated). "
                        "Move the token to the vault: navig vault set telegram_bot_token <token>",
                        key,
                    )
                    return val

            # legacy flat key
            for key in ("telegram_bot_token", "bot_token"):
                val = str(cfg.get(key, "")).strip()
                if val:
                    logger.warning(
                        "Token resolved from config.yaml %s (deprecated). "
                        "Move the token to the vault: navig vault set telegram_bot_token <token>",
                        key,
                    )
                    return val
    except Exception as exc:
        logger.debug("config.yaml lookup skipped: %s", exc)

    sys.exit(
        "\n[telegram-bot-navig] TELEGRAM_BOT_TOKEN not found.\n\n"
        "Resolution chain tried:\n"
        "  1. NAVIG vault    (navig vault set telegram_bot_token <token>)\n"
        "  2. env TELEGRAM_BOT_TOKEN\n"
        "  3. ~/.navig/config.yaml (deprecated fallback — not recommended)\n\n"
        "Fix: store your bot token in the vault:\n"
        "  navig vault set telegram_bot_token 1234567890:ABC-DEF...\n"
    )


# ---------------------------------------------------------------------------
# 3. Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    # Install deps first — ensures plugin imports work
    _auto_install_deps()

    from plugin_loader import PluginLoader
    from telegram.ext import Application

    token = _get_token()

    app = Application.builder().token(token).build()

    loader = PluginLoader(app, plugins_dir=_HERE / "plugins")
    loader.load_all()

    # ── Webhook or polling mode ────────────────────────────────────────────
    webhook_url = os.environ.get("TELEGRAM_WEBHOOK_URL", "").strip()
    if webhook_url:
        port = int(os.environ.get("PORT", "8443"))
        listen = os.environ.get("WEBHOOK_LISTEN", "0.0.0.0")
        url_path = token  # use token as path to obscure the endpoint
        full_url = f"{webhook_url.rstrip('/')}/{url_path}"
        logger.info("Bot starting in WEBHOOK mode — %s (port %d)", full_url, port)
        app.run_webhook(
            listen=listen,
            port=port,
            url_path=url_path,
            webhook_url=full_url,
            drop_pending_updates=True,
        )
    else:
        logger.info("Bot starting in POLLING mode — press Ctrl+C to stop")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
