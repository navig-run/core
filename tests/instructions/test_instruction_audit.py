"""Tests for the instruction-set audit (`navig instructions audit`)."""

from __future__ import annotations

from pathlib import Path

from navig.core.instruction_audit import audit, discover, find_repo


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def paragraph(tag: str) -> str:
    return (f"{tag}: " + "The rule is stated once, in one place, and read from there. " * 6).strip()


def test_clean_set_passes(tmp_path: Path) -> None:
    write(tmp_path / "CLAUDE.md", "# Rules\n\nKeep it short.\n")
    write(tmp_path / "docs" / "extra.md", "Detail lives here.\n")
    result = audit(tmp_path)
    assert result.violations == []
    assert result.repo_bytes == len((tmp_path / "CLAUDE.md").read_bytes())


def test_imported_file_counts_towards_the_set(tmp_path: Path) -> None:
    write(tmp_path / "CLAUDE.md", "# Rules\n\n@docs/extra.md\n")
    write(tmp_path / "docs" / "extra.md", "# Extra\n\n" + "x" * 500 + "\n")
    result = discover(tmp_path)
    assert [f.name for f in result.files] == ["CLAUDE.md", "docs/extra.md"]
    assert result.repo_bytes > 500


def test_hidden_claude_file_is_counted(tmp_path: Path) -> None:
    write(tmp_path / "CLAUDE.md", "# Rules\n")
    write(tmp_path / ".claude" / "CLAUDE.md", "# Engineering\n\nMore rules.\n")
    result = discover(tmp_path)
    assert [f.name for f in result.files] == ["CLAUDE.md", ".claude/CLAUDE.md"]


def test_duplicate_paragraph_across_files_is_a_violation(tmp_path: Path) -> None:
    body = paragraph("Same")
    write(tmp_path / "CLAUDE.md", f"# Rules\n\n{body}\n")
    write(tmp_path / ".claude" / "CLAUDE.md", f"# More\n\n{body}\n")
    result = audit(tmp_path)
    assert result.duplicate_bytes > 0
    assert any("duplicated" in v for v in result.violations)


def test_over_budget_is_a_violation(tmp_path: Path) -> None:
    write(tmp_path / "CLAUDE.md", "# Rules\n\n" + "y" * 600 + "\n")
    result = audit(tmp_path, budget_bytes=100, file_budget_bytes=10_000)
    assert any("over the" in v for v in result.violations)


def test_missing_link_is_flagged_but_inline_code_is_ignored(tmp_path: Path) -> None:
    write(
        tmp_path / "CLAUDE.md",
        "# Rules\n\nSee [missing](docs/gone.md).\n\nExample: `[x](docs/also-gone.md)`\n",
    )
    result = audit(tmp_path)
    targets = [m["target"] for m in result.missing_links]
    assert targets == ["docs/gone.md"]


def test_existing_link_resolves(tmp_path: Path) -> None:
    write(tmp_path / "CLAUDE.md", "# Rules\n\nSee [doc](docs/here.md).\n")
    write(tmp_path / "docs" / "here.md", "# Here\n")
    result = audit(tmp_path)
    assert result.missing_links == []


def test_find_repo_walks_up(tmp_path: Path) -> None:
    write(tmp_path / "CLAUDE.md", "# Rules\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_repo(nested) == tmp_path
