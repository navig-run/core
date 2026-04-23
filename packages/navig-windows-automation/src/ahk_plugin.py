from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_plugin():
    main_path = Path(__file__).with_name("main.py")
    spec = importlib.util.spec_from_file_location(
        "navig_windows_automation_main", main_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load main.py from {main_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.plugin


plugin = _load_plugin()


if __name__ == "__main__":
    plugin.run()
