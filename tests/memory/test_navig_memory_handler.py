from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
HANDLER_PATH = ROOT / "packages" / "navig-memory" / "handler.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_store_path_uses_dict_store_dir(tmp_path):
    module = _load_module("navig_memory_handler_dict", HANDLER_PATH)

    path = module._store_path({"store_dir": tmp_path})

    assert path == tmp_path / "memories.json"


def test_store_path_uses_attribute_store_dir(tmp_path):
    module = _load_module("navig_memory_handler_attr", HANDLER_PATH)

    path = module._store_path(SimpleNamespace(store_dir=tmp_path))

    assert path == tmp_path / "memories.json"
