"""
bot.py — Entry point for the telegram-bot-navig plugin bot.

Token resolution order (first non-empty wins):
  1. NAVIG vault  (navig.vault → provider="telegram", key api_key/token)
  2. config.yaml  telegram.nas_token
  3. config.yaml  telegram.bot_token
  4. config.yaml  telegram_bot_token  (legacy flat key)
  5. env var      TELEGRAM_BOT_TOKEN

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
        pkg_name = dep.split("[")[0].split(">")[0].split("<")[0].split("=")[0].split("!")[0].strip()
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
            [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade"] + to_install,
            stdout=subprocess.DEVNULL,
        )
        logger.info("Deps installed successfully.")
    except subprocess.CalledProcessError as exc:
        logger.warning("pip install failed (exit %s) — some plugins may not work.", exc.returncode)


# ---------------------------------------------------------------------------
# 2. Token resolution — vault → config → env
# ---------------------------------------------------------------------------

def _get_token() -> str:
    """Resolve Telegram bot token from NAVIG vault, config, or env."""

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

    # ── 2b. ~/.navig/config.yaml ───────────────────────────────────────────
    try:
        import yaml  # installed by auto_install above or already present
        cfg_path = Path.home() / ".navig" / "config.yaml"
        if cfg_path.exists():
            cfg: dict = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

            # telegram.nas_token  (current navig config layout)
            tg_block = cfg.get("telegram", {})
            for key in ("nas_token", "bot_token", "token", "api_key"):
                val = tg_block.get(key, "").strip() if isinstance(tg_block, dict) else ""
                if val:
                    logger.info("Token resolved from config.yaml telegram.%s", key)
                    return val

            # legacy flat key
            for key in ("telegram_bot_token", "bot_token"):
                val = str(cfg.get(key, "")).strip()
                if val:
                    logger.info("Token resolved from config.yaml %s", key)
                    return val
    except Exception as exc:
        logger.debug("config.yaml lookup skipped: %s", exc)

    # ── 2c. Environment variable ───────────────────────────────────────────
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        logger.info("Token resolved from TELEGRAM_BOT_TOKEN env var")
        return token

    sys.exit(
        "\n[telegram-bot-navig] TELEGRAM_BOT_TOKEN not found.\n\n"
        "Resolution chain tried:\n"
        "  1. NAVIG vault    (navig vault get telegram)\n"
        "  2. ~/.navig/config.yaml → telegram.nas_token / telegram.bot_token\n"
        "  3. env TELEGRAM_BOT_TOKEN\n\n"
        "Fix: add your bot token to ~/.navig/config.yaml:\n"
        "  telegram:\n"
        "    nas_token: \"1234567890:ABC-DEF...\"\n"
    )


# ---------------------------------------------------------------------------
# 3. Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Install deps first — ensures plugin imports work
    _auto_install_deps()

    from telegram.ext import Application
    from plugin_loader import PluginLoader

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
