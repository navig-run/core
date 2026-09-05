"""`--dry-run` must show what would actually be created.

The command listed `template_data.get("files", [])` — a key the template schema does not
have. `validate_template` requires `structure`, and "files" appeared nowhere else in the
subsystem, so the loop was ALWAYS empty: every dry run, for every template, printed the
header "Files to be created:" and then nothing.

`Scaffolder.preview` renders for real into a staging directory and reports what came
out, so the list reflects conditions and rendered path names — and a template that would
fail raises, which is the whole reason to run `--dry-run` before committing to a target.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from navig.core.scaffolder import Scaffolder

_TEMPLATE = {
    "structure": [
        {
            "type": "directory",
            "path": "src",
            "children": [{"path": "app.py", "content": "print({{ port }})"}],
        },
        {"path": "README.md", "content": "# {{ project_name }}"},
        {"path": "optional.txt", "content": "x", "condition": "false"},
    ]
}


# ── preview ────────────────────────────────────────────────────────────────────


def test_preview_lists_files_and_directories_with_their_kind():
    entries = Scaffolder().preview(_TEMPLATE, {"project_name": "demo", "port": 8080})

    assert entries == [
        ("README.md", "file"),
        ("src", "directory"),
        ("src/app.py", "file"),
    ]


def test_preview_reflects_conditions():
    """`optional.txt` is guarded by `condition: false`, so it must not be listed —
    a preview built from the raw template text could not know that."""
    listed = [rel for rel, _ in Scaffolder().preview(_TEMPLATE, {"project_name": "d"})]

    assert "optional.txt" not in listed


def test_preview_reflects_rendered_path_names():
    """Paths are Jinja too, so the preview must show the name the user will get."""
    entries = Scaffolder().preview(
        {"structure": [{"path": "{{ project_name }}.md", "content": "x"}]},
        {"project_name": "demo"},
    )

    assert entries == [("demo.md", "file")]


def test_preview_writes_nothing(tmp_path: Path):
    """It stages into a temp dir that is torn down; the cwd and any target are
    untouched."""
    before = sorted(p.name for p in tmp_path.iterdir())
    Scaffolder().preview(_TEMPLATE, {"project_name": "demo", "port": 1})

    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_preview_raises_when_generation_would_fail(tmp_path: Path):
    """A dry run that cannot answer "will this work?" is not worth running."""
    with pytest.raises(Exception):
        Scaffolder().preview(
            {"structure": [{"path": "a.txt", "source": "missing.tpl"}]},
            {},
            template_dir=tmp_path,
        )


def test_preview_of_an_all_false_template_is_empty():
    entries = Scaffolder().preview(
        {"structure": [{"path": "a.txt", "content": "x", "condition": "false"}]}, {}
    )

    assert entries == []


# ── the command ────────────────────────────────────────────────────────────────


def _run_dry(tmp_path: Path, template: dict):
    from typer.testing import CliRunner

    from navig.commands.scaffold import scaffold_app

    template_file = tmp_path / "t.yaml"
    template_file.write_text(yaml.dump(template), encoding="utf-8")

    return CliRunner().invoke(
        scaffold_app,
        ["apply", str(template_file), "--target-dir", str(tmp_path / "out"), "--dry-run"],
    )


def test_dry_run_lists_the_files_it_would_create(tmp_path: Path):
    """The bug, at the level the user sees it: the header was printed and the list was
    always empty."""
    result = _run_dry(tmp_path, _TEMPLATE)

    assert result.exit_code == 0, result.output
    assert "README.md" in result.output
    assert "src/app.py" in result.output or "src\\app.py" in result.output
    assert "optional.txt" not in result.output, "a false condition must not be listed"


def test_dry_run_writes_nothing(tmp_path: Path):
    _run_dry(tmp_path, _TEMPLATE)

    assert not (tmp_path / "out").exists(), "a dry run must not create the target"


def test_dry_run_reports_a_template_that_would_fail(tmp_path: Path):
    """Better to learn at dry-run time than after committing to a target."""
    result = _run_dry(tmp_path, {"structure": [{"path": "a.txt", "source": "gone.tpl"}]})

    assert result.exit_code != 0
    assert "would fail" in result.output.lower() or "not found" in result.output.lower()


def test_dry_run_says_so_when_a_template_would_create_nothing(tmp_path: Path):
    """Empty state: silence under a "Files to be created:" header is exactly the bug
    this replaced, so an empty result has to be stated."""
    result = _run_dry(
        tmp_path,
        {"structure": [{"path": "a.txt", "content": "x", "condition": "false"}]},
    )

    assert result.exit_code == 0, result.output
    assert "would create nothing" in result.output.lower()
