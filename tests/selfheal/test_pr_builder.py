"""
Tests for navig.selfheal.pr_builder — PR body formatting and token resolution.
"""
import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finding(
    file="navig/foo.py",
    line=42,
    severity="medium",
    category="bug",
    description="A bug",
    suggested_fix="Fix the bug by doing X",
    confidence=0.85,
):
    from navig.selfheal.scanner import ScanFinding

    return ScanFinding(
        file=file,
        line=line,
        severity=severity,
        category=category,
        description=description,
        suggested_fix=suggested_fix,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# _finding_summary
# ---------------------------------------------------------------------------

class TestFindingSummary:
    def test_empty_returns_no_issues_found(self):
        from navig.selfheal.pr_builder import _finding_summary

        assert "No issues found" in _finding_summary([])

    def test_single_critical_finding(self):
        from navig.selfheal.pr_builder import _finding_summary

        f = _finding(severity="critical")
        result = _finding_summary([f])
        assert "critical" in result.lower()
        assert "navig/foo.py:42" in result

    def test_groups_by_severity(self):
        from navig.selfheal.pr_builder import _finding_summary

        findings = [
            _finding(severity="high"),
            _finding(severity="low", line=10),
            _finding(severity="high", line=5),
        ]
        result = _finding_summary(findings)
        assert "High" in result
        assert "Low" in result
        assert "(2 issue(s))" in result

    def test_description_included(self):
        from navig.selfheal.pr_builder import _finding_summary

        f = _finding(description="Memory leak found")
        result = _finding_summary([f])
        assert "Memory leak found" in result

    def test_severity_order_critical_first(self):
        from navig.selfheal.pr_builder import _finding_summary

        findings = [_finding(severity="low"), _finding(severity="critical")]
        result = _finding_summary(findings).lower()
        assert result.index("critical") < result.index("low")


# ---------------------------------------------------------------------------
# _fix_bullets
# ---------------------------------------------------------------------------

class TestFixBullets:
    def test_empty_returns_no_fixes(self):
        from navig.selfheal.pr_builder import _fix_bullets

        assert "No fixes" in _fix_bullets([])

    def test_single_finding_bullet(self):
        from navig.selfheal.pr_builder import _fix_bullets

        f = _finding(file="navig/bar.py", line=7, suggested_fix="Replace with safer version")
        result = _fix_bullets([f])
        assert "navig/bar.py:7" in result
        assert "Replace with safer version" in result

    def test_long_fix_truncated_to_100_chars(self):
        from navig.selfheal.pr_builder import _fix_bullets

        f = _finding(suggested_fix="A" * 200)
        result = _fix_bullets([f])
        # Each bullet shows [:100]
        assert "A" * 100 in result
        assert "A" * 101 not in result


# ---------------------------------------------------------------------------
# _confidence_table
# ---------------------------------------------------------------------------

class TestConfidenceTable:
    def test_empty_returns_dash_row(self):
        from navig.selfheal.pr_builder import _confidence_table

        result = _confidence_table([])
        assert "—" in result

    def test_contains_file_and_confidence(self):
        from navig.selfheal.pr_builder import _confidence_table

        f = _finding(confidence=0.92)
        result = _confidence_table([f])
        assert "navig/foo.py:42" in result
        assert "0.92" in result

    def test_description_truncated_to_60_chars(self):
        from navig.selfheal.pr_builder import _confidence_table

        f = _finding(description="D" * 100)
        result = _confidence_table([f])
        assert "D" * 60 in result
        assert "D" * 61 not in result


# ---------------------------------------------------------------------------
# _build_pr_body
# ---------------------------------------------------------------------------

class TestBuildPRBody:
    def _build(self, findings=None, alias="alice", version="2.0.0", branch="fix/branch"):
        from navig.selfheal.pr_builder import _build_pr_body

        return _build_pr_body(findings or [], alias=alias, version=version, branch=branch)

    def test_contains_contributor(self):
        body = self._build(alias="devuser")
        assert "devuser" in body

    def test_anonymous_when_empty_alias(self):
        body = self._build(alias="")
        assert "Anonymous" in body

    def test_contains_version(self):
        body = self._build(version="3.1.4")
        assert "3.1.4" in body

    def test_contains_branch(self):
        body = self._build(branch="navig-selfheal/abc")
        assert "navig-selfheal/abc" in body

    def test_contains_what_was_found_section(self):
        body = self._build()
        assert "What Was Found" in body

    def test_contains_what_was_fixed_section(self):
        body = self._build()
        assert "What Was Fixed" in body

    def test_contains_confidence_scores_section(self):
        body = self._build()
        assert "Confidence Scores" in body

    def test_contains_finding_description(self):
        body = self._build(findings=[_finding(description="Null pointer")])
        assert "Null pointer" in body


# ---------------------------------------------------------------------------
# _derive_pr_title
# ---------------------------------------------------------------------------

class TestDerivePRTitle:
    def test_empty_findings_code_quality(self):
        from navig.selfheal.pr_builder import _derive_pr_title

        assert "code quality" in _derive_pr_title([])

    def test_single_critical(self):
        from navig.selfheal.pr_builder import _derive_pr_title

        result = _derive_pr_title([_finding(severity="critical")])
        assert "critical" in result
        assert "self-heal" in result

    def test_mixed_severities_in_title(self):
        from navig.selfheal.pr_builder import _derive_pr_title

        findings = [_finding(severity="high"), _finding(severity="low")]
        result = _derive_pr_title(findings)
        assert "high" in result
        assert "low" in result


# ---------------------------------------------------------------------------
# _resolve_token
# ---------------------------------------------------------------------------

class TestResolveToken:
    def test_raises_when_no_token(self):
        from navig.selfheal.pr_builder import _resolve_token

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("NAVIG_GITHUB_TOKEN", None)
            # Mock vault to fail
            with patch("navig.vault.get_vault", side_effect=Exception("no vault")):
                with pytest.raises(ValueError, match="No GitHub token"):
                    _resolve_token({})

    def test_resolves_from_env_var(self):
        from navig.selfheal.pr_builder import _resolve_token

        with patch.dict(os.environ, {"NAVIG_GITHUB_TOKEN": "ghp_abc123"}):
            with patch("navig.vault.get_vault", side_effect=Exception("no vault")):
                token = _resolve_token({})
        assert token == "ghp_abc123"

    def test_resolves_from_custom_env_var(self):
        from navig.selfheal.pr_builder import _resolve_token

        with patch.dict(os.environ, {"MY_CUSTOM_TOKEN": "ghp_custom"}):
            with patch("navig.vault.get_vault", side_effect=Exception("no vault")):
                token = _resolve_token({"github_token_env": "MY_CUSTOM_TOKEN"})
        assert token == "ghp_custom"

    def test_resolves_from_vault(self):
        from navig.selfheal.pr_builder import _resolve_token

        mock_vault = MagicMock()
        mock_vault.get_api_key.return_value = "vault_token_123"
        with patch("navig.vault.get_vault", return_value=mock_vault):
            token = _resolve_token({})
        assert token == "vault_token_123"
