from __future__ import annotations

import json
from pathlib import Path

from navig.commands import package as package_cmd
import pytest

pytestmark = pytest.mark.integration


def test_write_autoload_preserves_insertion_order(tmp_path: Path, monkeypatch):
    autoload_file = tmp_path / "packages_autoload.json"
    monkeypatch.setattr(package_cmd, "_autoload_path", lambda: autoload_file)

    package_cmd._write_autoload(
        ["navig-telegram", "navig-commands", "navig-telegram", "navig-memory"]
    )

    data = json.loads(autoload_file.read_text(encoding="utf-8"))
    assert data == ["navig-telegram", "navig-commands", "navig-memory"]


def test_ensure_runtime_dependencies_reports_missing_package(monkeypatch):
    package_cmd._loaded_packs.clear()
    manifest = {
        "depends_on": {
            "packages": {"navig-commands": ">=1.0.0"},
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


def test_write_autoload_canonicalizes_legacy_telegram_aliases(tmp_path: Path, monkeypatch):
    autoload_file = tmp_path / "packages_autoload.json"
    monkeypatch.setattr(package_cmd, "_autoload_path", lambda: autoload_file)

    package_cmd._write_autoload(["telegram-bot-navig", "navig-telegram-handlers"])

    data = json.loads(autoload_file.read_text(encoding="utf-8"))
    assert data == ["navig-telegram"]


def test_read_autoload_canonicalizes_legacy_telegram_aliases(tmp_path: Path, monkeypatch):
    autoload_file = tmp_path / "packages_autoload.json"
    autoload_file.write_text(
        json.dumps(["telegram-bot-navig", "navig-telegram", "navig-telegram-handlers"]),
        encoding="utf-8",
    )
    monkeypatch.setattr(package_cmd, "_autoload_path", lambda: autoload_file)

    ids = package_cmd._read_autoload()
    assert ids == ["navig-telegram"]


def test_runtime_dependencies_accept_loaded_canonical_alias():
    package_cmd._loaded_packs.clear()
    package_cmd._loaded_packs.add("navig-telegram")

    manifest = {"depends_on": {"packages": {"telegram-bot-navig": ">=1.0.0"}, "pip": []}}

    ok = package_cmd._ensure_runtime_dependencies(
        "consumer-pack",
        manifest,
        allow_pip_install=False,
    )
    assert ok is True


def test_package_init_scaffolds_workflow_package(tmp_path: Path):
    package_cmd.package_init(
        name="demo-workflow",
        pkg_type="workflows",
        directory=str(tmp_path),
        force=False,
    )

    pkg_dir = tmp_path / "demo-workflow"
    assert (pkg_dir / "navig.package.json").exists()
    assert (pkg_dir / "workflow.yaml").exists()

    manifest = json.loads((pkg_dir / "navig.package.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "demo-workflow"
    assert manifest["type"] == "workflows"
    assert manifest["entry"] == "workflow.yaml"


def test_package_init_scaffolds_commands_package(tmp_path: Path):
    package_cmd.package_init(
        name="demo-commands",
        pkg_type="commands",
        directory=str(tmp_path),
        force=False,
    )

    pkg_dir = tmp_path / "demo-commands"
    assert (pkg_dir / "navig.package.json").exists()
    assert (pkg_dir / "handler.py").exists()
    assert (pkg_dir / "commands" / "__init__.py").exists()
    assert (pkg_dir / "commands" / "hello.py").exists()


def test_audit_manifest_reports_missing_fields():
    errors, warnings = package_cmd._audit_manifest({"id": "x"})
    assert any("missing required field 'name'" in item for item in errors)
    assert any("missing recommended field 'entry'" in item for item in warnings)


def test_audit_packages_aggregates_findings(monkeypatch):
    monkeypatch.setattr(
        package_cmd,
        "_discover_packages",
        lambda: {
            "demo": {
                "manifest": {
                    "id": "demo",
                    "name": "Demo",
                    "version": "1.0.0",
                    "description": "Demo package",
                    "provides": ["commands"],
                    "type": "commands",
                    "entry": "handler.py",
                    "depends_on": {"packages": {}, "pip": []},
                    "hooks": [],
                },
                "label": "builtin",
                "path": Path("/tmp/demo"),
            },
            "broken": {
                "manifest": {"id": "broken"},
                "label": "user",
                "path": Path("/tmp/broken"),
            },
        },
    )

    report = package_cmd._audit_packages()
    indexed = {item["id"]: item for item in report}
    assert indexed["demo"]["ok"] is True
    assert indexed["broken"]["ok"] is False
    assert indexed["broken"]["errors"]
