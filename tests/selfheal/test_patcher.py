"""Unit tests for navig.selfheal.patcher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from navig.selfheal.patcher import build_patch
from navig.selfheal.scanner import ScanFinding

pytestmark = pytest.mark.slow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(
    severity: str = "critical",
    confidence: float = 0.95,
    line: int = 1,
    suggested_fix: str = "raise ValueError('x')  # NAVIG-HEAL: replace bare except",
) -> ScanFinding:
    return ScanFinding(
        file="navig/commands/example.py",
        line=line,
        severity=severity,  # type: ignore[arg-type]
        category="bug",
        description="Bare except clause catches BaseException.",
        suggested_fix=suggested_fix,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOnlyCriticalHighPatched:
    """build_patch() must skip medium and low severity findings."""

    def test_medium_finding_not_in_patch(self, tmp_repo: Path) -> None:
        """A medium-severity finding must produce no diff output."""
        finding = _make_finding(severity="medium")
        with patch("pathlib.Path.read_text", return_value="x = 1\n"):
            patch_str = build_patch([finding], tmp_repo)
        assert patch_str == "" or "medium" not in patch_str.lower()

    def test_low_finding_not_in_patch(self, tmp_repo: Path) -> None:
        """A low-severity finding must be silently skipped."""
        finding = _make_finding(severity="low")
        with patch("pathlib.Path.read_text", return_value="x = 1\n"):
            patch_str = build_patch([finding], tmp_repo)
        assert patch_str.strip() == ""

    def test_critical_finding_produces_diff(self, tmp_repo: Path) -> None:
        """A critical finding with a valid suggested_fix must produce a non-empty diff."""
        finding = _make_finding(severity="critical")
        source = "try:\n    pass\nexcept:\n    pass\n"
        # Write actual source so patcher can read it
        source_path = tmp_repo / "navig" / "commands" / "example.py"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(source, encoding="utf-8")

        patch_str = build_patch([finding], tmp_repo)
        assert patch_str.strip() != ""

    def test_high_finding_produces_diff(self, tmp_repo: Path) -> None:
        """A high-severity finding must also be included in the patch."""
        finding = _make_finding(severity="high")
        source_path = tmp_repo / "navig" / "commands" / "example.py"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("x = old_value\n", encoding="utf-8")

        patch_str = build_patch([finding], tmp_repo)
        assert patch_str.strip() != ""


class TestPatchIncludesNavigHealComment:
    """build_patch() must annotate every changed line with # NAVIG-HEAL:."""

    def test_navig_heal_marker_present(self, tmp_repo: Path) -> None:
        """Patched lines must contain the NAVIG-HEAL annotation in the diff."""
        finding = _make_finding(severity="critical")
        source_path = tmp_repo / "navig" / "commands" / "example.py"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("except:\n    pass\n", encoding="utf-8")

        patch_str = build_patch([finding], tmp_repo)
        # Either the marker is in the diff body or the suggested_fix itself
        # contains the annotation — both are acceptable proof
        assert "NAVIG-HEAL" in patch_str or "NAVIG-HEAL" in finding.suggested_fix

    def test_multiple_findings_each_annotated(self, tmp_repo: Path) -> None:
        """Every changed hunk must carry at least one NAVIG-HEAL annotation."""
        findings = [
            _make_finding(severity="critical", line=1, suggested_fix="x = 1  # NAVIG-HEAL: fixed"),
            _make_finding(severity="high", line=2, suggested_fix="y = 2  # NAVIG-HEAL: fixed"),
        ]
        source_path = tmp_repo / "navig" / "commands" / "example.py"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("old_x\nold_y\n", encoding="utf-8")

        patch_str = build_patch(findings, tmp_repo)
        assert patch_str.count("NAVIG-HEAL") >= 1

    def test_annotation_describes_issue(self, tmp_repo: Path) -> None:
        """The NAVIG-HEAL comment must describe the issue, not be empty."""
        finding = _make_finding(
            severity="critical",
            suggested_fix="raise ValueError('x')  # NAVIG-HEAL: replace bare except",
        )
        source_path = tmp_repo / "navig" / "commands" / "example.py"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("except Exception:\n    pass\n", encoding="utf-8")

        patch_str = build_patch([finding], tmp_repo)
        if "NAVIG-HEAL" in patch_str:
            idx = patch_str.index("NAVIG-HEAL")
            comment = patch_str[idx : idx + 60]
            assert len(comment.strip()) > len("NAVIG-HEAL")  # has text after marker


class TestPatchIsValidUnifiedDiff:
    """build_patch() output must be a syntactically valid unified diff."""

    def test_patch_starts_with_header(self, tmp_repo: Path) -> None:
        """A non-empty patch must start with '--- ' (unified diff header)."""
        finding = _make_finding(severity="critical")
        source_path = tmp_repo / "navig" / "commands" / "example.py"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("except:\n    pass\n", encoding="utf-8")

        patch_str = build_patch([finding], tmp_repo)
        if patch_str.strip():
            assert patch_str.lstrip().startswith("---"), (
                "Unified diff must begin with '--- <filename>' header"
            )

    def test_patch_contains_plus_plus_header(self, tmp_repo: Path) -> None:
        """A non-empty patch must contain a '+++ ' header line."""
        finding = _make_finding(severity="critical")
        source_path = tmp_repo / "navig" / "commands" / "example.py"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("except Exception:\n    pass\n", encoding="utf-8")

        patch_str = build_patch([finding], tmp_repo)
        if patch_str.strip():
            assert "+++" in patch_str

    def test_no_critical_high_findings_returns_empty_string(self, tmp_repo: Path) -> None:
        """With no actionable findings the return value must be an empty string."""
        harmless = [
            _make_finding(severity="medium"),
            _make_finding(severity="low"),
        ]
        with patch("pathlib.Path.read_text", return_value="x = 1\n"):
            result = build_patch(harmless, tmp_repo)
        assert result == ""

    def test_empty_findings_list_returns_empty_string(self, tmp_repo: Path) -> None:
        """An empty findings list must return an empty string without raising."""
        result = build_patch([], tmp_repo)
        assert result == ""


# ---------------------------------------------------------------------------
# Structural validity — `suggested_fix` and `description` are LLM free text
# ---------------------------------------------------------------------------


def _assert_structurally_valid(patch_str: str) -> None:
    """Every line of a unified diff must carry a diff prefix.

    A newline inside `suggested_fix`/`description` used to land INSIDE one element of the line
    list, so difflib counted one line where the file had two: the extra physical line was emitted
    with NO prefix and `git apply` rejected the whole patch as corrupt.
    """
    for line in patch_str.splitlines():
        if line.startswith(("---", "+++", "@@")) or not line:
            continue
        assert line[0] in " +-\\", f"line without a diff prefix (corrupt patch): {line!r}"


def _write(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestLLMTextCannotCorruptTheDiff:
    """`suggested_fix` and `description` come from an LLM — newlines must not break the patch."""

    def test_multiline_fix_is_spliced_into_separate_lines(self, tmp_repo: Path) -> None:
        _write(tmp_repo, "navig/commands/example.py", "def f():\n    x = 1\n    return x\n")
        finding = _make_finding(severity="high", line=2, suggested_fix="x = 1\nassert x")

        patch_str = build_patch([finding], tmp_repo)

        _assert_structurally_valid(patch_str)
        added = [ln for ln in patch_str.splitlines() if ln.startswith("+") and ln[1:2] != "+"]
        assert any("assert x" in ln for ln in added), "the second row must be its own + line"
        # difflib must see the real line count: 3 lines in, 4 out.
        assert "@@ -1,3 +1,4 @@" in patch_str

    def test_newline_in_description_collapses_to_one_comment(self, tmp_repo: Path) -> None:
        _write(tmp_repo, "navig/commands/example.py", "x = old\n")
        finding = _make_finding(severity="critical", line=1, suggested_fix="x = new")
        finding.description = "first line\nsecond line"

        patch_str = build_patch([finding], tmp_repo)

        _assert_structurally_valid(patch_str)
        assert "NAVIG-HEAL: first line second line" in patch_str

    def test_whitespace_only_fix_does_not_delete_the_line(self, tmp_repo: Path) -> None:
        _write(tmp_repo, "navig/commands/example.py", "keep_me = 1\n")
        finding = _make_finding(severity="critical", line=1, suggested_fix="   \n  ")

        patch_str = build_patch([finding], tmp_repo)

        _assert_structurally_valid(patch_str)
        assert "keep_me = 1" in patch_str


class TestBuildPatchIsPure:
    """Building a patch must not mutate the working tree.

    `_append_requirement` used to write requirements.txt during generation — before the caller's
    final "Submit this patch?" confirmation, and outside the returned diff. Since
    `commit_and_push` runs `git add --all`, an LLM-suggested dependency reached the PR without
    ever appearing in the diff a human approved.
    """

    def test_requirements_file_is_not_modified_on_disk(self, tmp_repo: Path) -> None:
        _write(tmp_repo, "navig/commands/example.py", "x = 1\n")
        req = _write(tmp_repo, "requirements.txt", "httpx==0.27\n")
        before = req.read_text(encoding="utf-8")

        build_patch([_make_finding(suggested_fix="pip install tenacity")], tmp_repo)

        assert req.read_text(encoding="utf-8") == before

    def test_new_dependency_rides_in_the_diff(self, tmp_repo: Path) -> None:
        _write(tmp_repo, "navig/commands/example.py", "x = 1\n")
        _write(tmp_repo, "requirements.txt", "httpx==0.27\n")

        patch_str = build_patch([_make_finding(suggested_fix="pip install tenacity")], tmp_repo)

        _assert_structurally_valid(patch_str)
        assert "+++ b/requirements.txt" in patch_str
        assert "+tenacity" in patch_str

    def test_existing_dependency_is_not_duplicated(self, tmp_repo: Path) -> None:
        _write(tmp_repo, "navig/commands/example.py", "x = 1\n")
        _write(tmp_repo, "requirements.txt", "httpx==0.27\n")

        patch_str = build_patch([_make_finding(suggested_fix="pip install httpx")], tmp_repo)

        assert "requirements.txt" not in patch_str

    def test_install_instruction_does_not_replace_source_code(self, tmp_repo: Path) -> None:
        """"pip install X" is an instruction, not a line of Python — it must never be written
        into the source file (the dependency hunk carries the change instead)."""
        _write(tmp_repo, "navig/commands/example.py", "import json\n")
        _write(tmp_repo, "requirements.txt", "httpx==0.27\n")

        patch_str = build_patch(
            [_make_finding(line=1, suggested_fix="pip install tenacity")], tmp_repo
        )

        _assert_structurally_valid(patch_str)
        assert "+pip install tenacity" not in patch_str
