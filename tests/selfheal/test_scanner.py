"""Unit tests for navig.selfheal.scanner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.selfheal.scanner import (
    ScanFinding,
    _collect_py_files,
    _is_sensitive_path,
    scan_files,
)


class TestScanFiltersbelowConfidence:
    """scan_files() must drop findings below the configured min_confidence."""

    def test_findings_below_threshold_are_dropped(
        self, tmp_repo: Path, mock_llm_response: str
    ) -> None:
        """Findings with confidence < min_confidence must not appear in results."""
        # LLM returns one finding with confidence 0.93; threshold is 0.95
        with patch("navig.llm_generate.llm_generate", return_value=mock_llm_response):
            results = scan_files(
                tmp_repo / "navig",
                config={"min_confidence": 0.95},
            )
        assert all(
            f.confidence >= 0.95 for f in results
        ), "All returned findings must meet the confidence threshold"

    def test_findings_at_threshold_are_kept(self, tmp_repo: Path) -> None:
        """Findings exactly at min_confidence must be included."""
        findings_at_threshold = json.dumps(
            [
                {
                    "file": "navig/commands/example.py",
                    "line": 1,
                    "severity": "high",
                    "category": "bug",
                    "description": "Missing error handling.",
                    "suggested_fix": "Wrap in try/except.",
                    "confidence": 0.80,
                }
            ]
        )
        with patch(
            "navig.llm_generate.llm_generate", return_value=findings_at_threshold
        ):
            results = scan_files(
                tmp_repo / "navig",
                config={"min_confidence": 0.80},
            )
        assert any(f.confidence == 0.80 for f in results)

    def test_empty_llm_response_returns_empty_list(self, tmp_repo: Path) -> None:
        """An empty LLM response must yield an empty findings list (no crash)."""
        with patch("navig.llm_generate.llm_generate", return_value="[]"):
            results = scan_files(tmp_repo / "navig", config={"min_confidence": 0.80})
        assert results == []


class TestScanSkipsVaultFiles:
    """scan_files() must never send vault/ or other sensitive files to the LLM."""

    def test_vault_directory_files_are_excluded(self, tmp_path: Path) -> None:
        """Files under vault/ must not be collected for scanning."""
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir(parents=True)
        (vault_dir / "core.py").write_text("SECRET=1", encoding="utf-8")
        (tmp_path / "safe.py").write_text("def foo(): pass\n", encoding="utf-8")

        collected = _collect_py_files(tmp_path)
        vault_prefix = str(vault_dir)
        assert not any(
            str(p).startswith(vault_prefix) for p in collected
        ), "vault/ files must never be included in the scan batch"
        assert any(p.name == "safe.py" for p in collected)

    def test_is_sensitive_path_detects_vault(self) -> None:
        """_is_sensitive_path must return True for vault/ paths."""
        assert _is_sensitive_path(
            Path("/home/user/.navig/core-repo/navig/vault/core.py")
        )

    def test_is_sensitive_path_detects_secret_in_name(self) -> None:
        """Files with 'secret' in their path are flagged as sensitive."""
        assert _is_sensitive_path(Path("navig/config/my_secret_config.py"))

    def test_is_sensitive_path_allows_safe_files(self) -> None:
        """Regular source files must not be flagged as sensitive."""
        assert not _is_sensitive_path(Path("navig/commands/contribute.py"))
        assert not _is_sensitive_path(Path("navig/selfheal/scanner.py"))


class TestScanReturnsPydanticModels:
    """scan_files() must return properly typed ScanFinding instances."""

    def test_results_are_scan_finding_instances(
        self, tmp_repo: Path, mock_llm_response: str
    ) -> None:
        """Every item in the returned list must be a ScanFinding."""
        with patch("navig.llm_generate.llm_generate", return_value=mock_llm_response):
            results = scan_files(tmp_repo / "navig", config={"min_confidence": 0.80})
        assert all(isinstance(f, ScanFinding) for f in results)

    def test_findings_are_sorted_critical_first(self, tmp_repo: Path) -> None:
        """Results must be ordered critical → high → medium → low."""
        multi_severity_response = json.dumps(
            [
                {
                    "file": "navig/commands/example.py",
                    "line": 1,
                    "severity": "low",
                    "category": "readability",
                    "description": "d",
                    "suggested_fix": "f",
                    "confidence": 0.90,
                },
                {
                    "file": "navig/commands/example.py",
                    "line": 2,
                    "severity": "critical",
                    "category": "bug",
                    "description": "d",
                    "suggested_fix": "f",
                    "confidence": 0.95,
                },
                {
                    "file": "navig/commands/example.py",
                    "line": 3,
                    "severity": "high",
                    "category": "security",
                    "description": "d",
                    "suggested_fix": "f",
                    "confidence": 0.88,
                },
            ]
        )
        with patch(
            "navig.llm_generate.llm_generate", return_value=multi_severity_response
        ):
            results = scan_files(tmp_repo / "navig", config={"min_confidence": 0.80})

        if len(results) >= 2:
            order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            for i in range(len(results) - 1):
                assert order[results[i].severity] <= order[results[i + 1].severity]

    def test_confidence_clamped_to_valid_range(self) -> None:
        """ScanFinding must clamp out-of-range confidence values gracefully."""
        f = ScanFinding(
            file="x.py",
            line=1,
            severity="low",
            category="readability",
            description="desc",
            suggested_fix="fix",
            confidence=1.5,  # invalid — should be clamped to 1.0
        )
        assert f.confidence <= 1.0

    def test_invalid_severity_raises_validation_error(self) -> None:
        """ScanFinding must reject an unrecognised severity value."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScanFinding(
                file="x.py",
                line=1,
                severity="extreme",  # not in Literal
                category="bug",
                description="d",
                suggested_fix="f",
                confidence=0.9,
            )
