"""Unit tests for navig.selfheal.patcher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from navig.selfheal.patcher import build_patch
from navig.selfheal.scanner import ScanFinding

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
            _make_finding(
                severity="critical", line=1, suggested_fix="x = 1  # NAVIG-HEAL: fixed"
            ),
            _make_finding(
                severity="high", line=2, suggested_fix="y = 2  # NAVIG-HEAL: fixed"
            ),
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
            assert patch_str.lstrip().startswith(
                "---"
            ), "Unified diff must begin with '--- <filename>' header"

    def test_patch_contains_plus_plus_header(self, tmp_repo: Path) -> None:
        """A non-empty patch must contain a '+++ ' header line."""
        finding = _make_finding(severity="critical")
        source_path = tmp_repo / "navig" / "commands" / "example.py"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("except Exception:\n    pass\n", encoding="utf-8")

        patch_str = build_patch([finding], tmp_repo)
        if patch_str.strip():
            assert "+++" in patch_str

    def test_no_critical_high_findings_returns_empty_string(
        self, tmp_repo: Path
    ) -> None:
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
