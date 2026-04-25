from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HANDLER_PATH = ROOT / "packages" / "navig-telegram" / "handler.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_handler_scopes_src_path_for_load_and_unload(tmp_path):
    handler = _load_module("navig_telegram_package_handler", HANDLER_PATH)
    pack_root = tmp_path / "navig-telegram"
    src_dir = pack_root / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "telegram_worker.py").write_text(
        "\n".join(
            [
                "calls = []",
                "def start(config, packages_dir):",
                "    calls.append(('start', config, str(packages_dir)))",
                "def stop():",
                "    calls.append(('stop',))",
            ]
        ),
        encoding="utf-8",
    )

    ctx = handler.PluginContext(
        pack_id="navig-telegram",
        version="1.0.0",
        store_path=pack_root,
        config={"token": "demo"},
    )
    src_path = str(src_dir)

    assert src_path not in sys.path

    handler.on_load(ctx)
    telegram_worker = sys.modules["telegram_worker"]
    assert telegram_worker.calls == [("start", {"token": "demo"}, str(pack_root.parent))]
    assert src_path not in sys.path

    sys.modules.pop("telegram_worker", None)
    handler.on_unload(ctx)
    assert src_path not in sys.path
