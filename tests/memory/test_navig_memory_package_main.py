from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN_PATH = ROOT / "packages" / "navig-memory" / "src" / "main.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_legacy_memory_main_uses_real_store_backend(tmp_path):
    module = _load_module("navig_memory_package_main", MAIN_PATH)
    calls: list[tuple[str, object]] = []

    class FakeStore:
        def store(self, content: str, tags=None):
            calls.append(("store", content, tags))
            return "mem-123"

        def search(self, query: str, limit: int = 10):
            calls.append(("search", query, limit))
            return [{"content": "hello world", "tags": ["fact"]}]

    fake_handler = type(
        "FakeHandler",
        (),
        {
            "_get_store": staticmethod(lambda ctx=None: FakeStore()),
            "_store_path": staticmethod(lambda ctx=None: tmp_path / "memories.json"),
        },
    )()

    module._HANDLER = fake_handler

    remember_result = module.remember("hello world", type="fact")
    recall_result = module.recall("hello")

    assert remember_result == {
        "status": "success",
        "id": "mem-123",
        "path": str(tmp_path / "memories.json"),
    }
    assert recall_result == {
        "results": [{"content": "hello world", "tags": ["fact"]}]
    }
    assert calls == [
        ("store", "hello world", ["fact"]),
        ("search", "hello", 10),
    ]


def test_legacy_memory_main_recall_empty_query_is_empty():
    module = _load_module("navig_memory_package_main_empty", MAIN_PATH)

    assert module.recall("") == {"results": []}


def test_legacy_memory_main_checkpoint_uses_handler(tmp_path):
    module = _load_module("navig_memory_package_main_checkpoint", MAIN_PATH)
    calls: list[dict] = []

    fake_handler = type(
        "FakeHandler",
        (),
        {
            "cmd_memory_checkpoint": staticmethod(
                lambda args: calls.append(args)
                or {
                    "status": "ok",
                    "data": {
                        "id": "cp-123",
                        "path": str(tmp_path / "checkpoints" / "cp-123.json"),
                    },
                }
            ),
        },
    )()

    module._HANDLER = fake_handler

    result = module.checkpoint(str(tmp_path / "workspace"))

    assert result == {
        "status": "success",
        "id": "cp-123",
        "path": str(tmp_path / "checkpoints" / "cp-123.json"),
    }
    assert calls == [{"root_path": str(tmp_path / "workspace")}]
