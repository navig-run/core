from __future__ import annotations

from typer.testing import CliRunner

from navig.commands.import_cmd import import_app


runner = CliRunner()


def test_import_unknown_source_fails() -> None:
    result = runner.invoke(import_app, ["--source", "unknown"])
    assert result.exit_code == 1
    assert "Unknown source" in result.output


def test_import_source_all_with_path_fails(tmp_path) -> None:
    p = tmp_path / "dummy.json"
    p.write_text("{}", encoding="utf-8")

    result = runner.invoke(import_app, ["--source", "all", "--path", str(p)])
    assert result.exit_code == 1
    assert "cannot be used with --source all" in result.output


def test_import_missing_path_fails(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    result = runner.invoke(import_app, ["--source", "chrome", "--path", str(missing)])
    assert result.exit_code == 1
    assert "does not exist" in result.output
