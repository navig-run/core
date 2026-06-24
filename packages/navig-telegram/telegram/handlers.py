"""Compatibility wrapper for the pack-local Telegram handler entrypoint."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_pack_handlers():
    handlers_path = Path(__file__).resolve().parents[1] / "tg_handlers.py"
    spec = importlib.util.spec_from_file_location(
        "navig_telegram_pack_handlers", handlers_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load tg_handlers.py from {handlers_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PACK_HANDLERS = _load_pack_handlers()

_get_handler = _PACK_HANDLERS._get_handler
_format_checkdomain = _PACK_HANDLERS._format_checkdomain
cmd_checkdomain = _PACK_HANDLERS.cmd_checkdomain
TELEGRAM_COMMANDS = _PACK_HANDLERS.TELEGRAM_COMMANDS

__all__ = [
    "_get_handler",
    "_format_checkdomain",
    "cmd_checkdomain",
    "TELEGRAM_COMMANDS",
]
