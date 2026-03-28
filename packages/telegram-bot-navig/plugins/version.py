"""
version.py — /version command plugin for telegram-bot-navig.

Replies with the current NAVIG version read from the installed package
metadata (importlib.metadata → pyproject.toml).  Falls back gracefully
to "Version unavailable." if the package is not properly installed.
"""

from __future__ import annotations

import logging

from plugin_base import BotPlugin, PluginMeta
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def _get_navig_version() -> str:
    """Return the installed navig package version, or empty string on failure."""
    try:
        from importlib.metadata import version as _pkg_version  # noqa: PLC0415

        return _pkg_version("navig")
    except Exception:
        return ""


class VersionPlugin(BotPlugin):
    """Replies to /version with the current NAVIG version string."""

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="version",
            description="Show the current NAVIG version.",
            version="1.0.0",
        )

    @property
    def command(self) -> str:
        return "version"

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        ver = _get_navig_version()
        if ver:
            text = f"🧭 *Navig* — version `{ver}`"
        else:
            text = "Version unavailable."

        await update.message.reply_text(text, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Required factory
# ---------------------------------------------------------------------------


def create() -> VersionPlugin:
    """Return a fresh VersionPlugin instance."""
    return VersionPlugin()
