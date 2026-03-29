from __future__ import annotations

import json
from pathlib import Path

from navig.commands import package as package_cmd


def test_write_autoload_preserves_insertion_order(tmp_path: Path, monkeypatch):
    autoload_file = tmp_path / "packages_autoload.json"
    monkeypatch.setattr(package_cmd, "_autoload_path", lambda: autoload_file)

    package_cmd._write_autoload(["navig-telegram", "navig-commands-core", "navig-telegram", "navig-memory"])

    data = json.loads(autoload_file.read_text(encoding="utf-8"))
    assert data == ["navig-telegram", "navig-commands-core", "navig-memory"]


def test_ensure_runtime_dependencies_reports_missing_package(monkeypatch):
    package_cmd._loaded_packs.clear()
    manifest = {
        "depends_on": {
            "packages": {"navig-commands-core": ">=1.0.0"},
            "pip": [],
        }
    }

    ok = package_cmd._ensure_runtime_dependencies(
        "navig-telegram",
        manifest,
        allow_pip_install=False,
    )

    assert ok is False


def test_ensure_runtime_dependencies_installs_missing_pip(monkeypatch):
    package_cmd._loaded_packs.clear()

    install_calls: list[tuple[str, list[str]]] = []

    def fake_install(pkg_id: str, deps: list[str]) -> bool:
        install_calls.append((pkg_id, deps))
        return True

    state = {"installed": False}

    def fake_find_spec(name: str):
        if name == "python_telegram_bot":
            return object() if state["installed"] else None
        return object()

    monkeypatch.setattr(package_cmd, "_install_pip_dependencies", fake_install)
    monkeypatch.setattr(package_cmd.importlib.util, "find_spec", fake_find_spec)

    manifest = {"depends_on": {"pip": ["python-telegram-bot>=20.0"]}}

    first_ok = package_cmd._ensure_runtime_dependencies(
        "navig-telegram", manifest, allow_pip_install=True
    )
    assert first_ok is False
    assert install_calls == [("navig-telegram", ["python-telegram-bot>=20.0"])]

    state["installed"] = True
    second_ok = package_cmd._ensure_runtime_dependencies(
        "navig-telegram", manifest, allow_pip_install=True
    )
    assert second_ok is True


def test_scoped_sys_path_restores_path(tmp_path: Path):
    scoped = tmp_path / "pkg"
    scoped.mkdir(parents=True, exist_ok=True)

    import sys

    path_str = str(scoped)
    assert path_str not in sys.path

    with package_cmd._scoped_sys_path(scoped):
        assert path_str in sys.path

    assert path_str not in sys.path
