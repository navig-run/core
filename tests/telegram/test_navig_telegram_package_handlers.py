from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packages" / "navig-telegram"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compat_handlers_reexport_pack_entrypoint():
    pack_handlers = _load_module(
        "navig_telegram_tg_handlers", PACK_ROOT / "tg_handlers.py"
    )
    compat_handlers = _load_module(
        "navig_telegram_compat_handlers", PACK_ROOT / "telegram" / "handlers.py"
    )

    assert compat_handlers._PACK_HANDLERS.__file__ == str(PACK_ROOT / "tg_handlers.py")
    assert compat_handlers.TELEGRAM_COMMANDS["checkdomain"].__name__ == "cmd_checkdomain"
    assert compat_handlers.cmd_checkdomain.__code__.co_code == pack_handlers.cmd_checkdomain.__code__.co_code
    assert compat_handlers._format_checkdomain({"status": "available", "domain": "navig.io"}) == (
        "✅ <b>navig.io</b>\nNo details."
    )
