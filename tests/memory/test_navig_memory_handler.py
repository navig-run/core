from __future__ import annotations

import importlib.util
import json
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


def test_memory_checkpoint_writes_snapshot_file(tmp_path, monkeypatch):
    module = _load_module("navig_memory_handler_checkpoint", HANDLER_PATH)
    checkpoint_dir = tmp_path / "checkpoints"
    monkeypatch.setattr(module, "_checkpoint_path", lambda ctx=None: checkpoint_dir)
    monkeypatch.setattr(module, "_store_path", lambda ctx=None: tmp_path / "memories.json")
    monkeypatch.setattr(
        module,
        "_latest_conversation_snapshot",
        lambda: {"session_key": "sess-1", "message_count": 2},
    )

    result = module.cmd_memory_checkpoint({"root_path": str(tmp_path / "workspace")})

    assert result["status"] == "ok"
    checkpoint_file = Path(result["data"]["path"])
    assert checkpoint_file.exists()

    payload = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    assert payload["workspace_root"] == str(tmp_path / "workspace")
    assert payload["memory_store"] == str(tmp_path / "memories.json")
    assert payload["latest_session"] == {"session_key": "sess-1", "message_count": 2}
