from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import pytest

pytestmark = pytest.mark.unit


def test_build_public_manifest_has_required_keys():
    from navig.registry.manifest import build_public_manifest

    manifest = build_public_manifest(validate=False)

    assert manifest["schema_version"] == "1.0.0"
    assert isinstance(manifest["commands"], list)
    assert manifest["total"] == len(manifest["commands"])
    assert manifest["total"] > 0

    sample = manifest["commands"][0]
    required = {
        "path",
        "summary",
        "module",
        "handler",
        "status",
        "since",
        "aliases",
        "tags",
        "examples",
    }
    assert required.issubset(sample.keys())


def test_manifest_contains_annotated_host_and_db_commands():
    from navig.registry.manifest import build_public_manifest

    manifest = build_public_manifest(validate=False)
    by_path = {row["path"]: row for row in manifest["commands"]}

    assert "navig host list" in by_path
    assert "navig db query" in by_path
    assert "navig db list" in by_path

    assert "hosts" in by_path["navig host list"]["tags"]
    assert "database" in by_path["navig db query"]["tags"]


def test_markdown_render_contains_reference_sections():
    from navig.registry.manifest import build_public_manifest, render_markdown

    manifest = build_public_manifest(validate=False)
    markdown = render_markdown(manifest)

    assert "# NAVIG Command Reference" in markdown
    assert "## `navig host list`" in markdown or "## `navig db query`" in markdown


def test_export_script_writes_artifacts(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[2]
    export_script = repo_root / "tools" / "export_registry.py"
    spec = importlib.util.spec_from_file_location("export_registry", export_script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    main = module.main

    output_dir = tmp_path / "generated"
    code = main(
        [
            "--validate",
            "--format",
            "both",
            "--deprecations-report",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    commands_json = output_dir / "commands.json"
    commands_md = output_dir / "commands.md"
    deprecations_json = output_dir / "deprecations.json"
    completions_txt = output_dir / "completions" / "commands.txt"

    assert commands_json.exists()
    assert commands_md.exists()
    assert deprecations_json.exists()
    assert completions_txt.exists()

    payload = json.loads(commands_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0.0"
    assert payload["total"] == len(payload["commands"])
    assert payload["total"] > 0
