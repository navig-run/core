"""
Tests for navig.personas.soul_loader — 6-level priority chain.

Covers the Phase-1 IDENTITY.md addition from RFC #37, plus regression tests
for the existing chain steps.
"""

from pathlib import Path
from unittest.mock import patch
import tempfile
import os

import pytest

from navig.personas.soul_loader import load_soul


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> Path:
    """Create a file with the given content, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Tests: IDENTITY.md (RFC #37 Phase 1 — step 3)
# ---------------------------------------------------------------------------

class TestIdentityMdPriority:
    """IDENTITY.md at ~/.navig/workspace/IDENTITY.md must take priority over SOUL.md."""

    def test_identity_md_used_when_present(self, tmp_path):
        """When IDENTITY.md exists in the workspace dir, load_soul returns it."""
        identity_content = "# I am the identity file"
        _write(tmp_path / ".navig" / "workspace" / "IDENTITY.md", identity_content)

        with patch("navig.personas.soul_loader.Path.home", return_value=tmp_path):
            result = load_soul()

        assert result == identity_content

    def test_identity_md_beats_soul_md(self, tmp_path):
        """IDENTITY.md (step 3) must win over SOUL.md (step 4) when both exist."""
        _write(tmp_path / ".navig" / "workspace" / "IDENTITY.md", "IDENTITY wins")
        _write(tmp_path / ".navig" / "workspace" / "SOUL.md", "SOUL fallback")

        with patch("navig.personas.soul_loader.Path.home", return_value=tmp_path):
            result = load_soul()

        assert result == "IDENTITY wins"

    def test_soul_md_used_when_no_identity_md(self, tmp_path):
        """When only SOUL.md exists (no IDENTITY.md), SOUL.md is still loaded."""
        _write(tmp_path / ".navig" / "workspace" / "SOUL.md", "legacy soul content")

        with patch("navig.personas.soul_loader.Path.home", return_value=tmp_path):
            result = load_soul()

        assert result == "legacy soul content"

    def test_identity_md_skipped_when_empty(self, tmp_path):
        """An empty (whitespace-only) IDENTITY.md must NOT be returned; chain continues."""
        _write(tmp_path / ".navig" / "workspace" / "IDENTITY.md", "   \n   ")
        _write(tmp_path / ".navig" / "workspace" / "SOUL.md", "fallback soul")

        with patch("navig.personas.soul_loader.Path.home", return_value=tmp_path):
            result = load_soul()

        # Empty IDENTITY.md → falls through to SOUL.md
        assert result == "fallback soul"

    def test_identity_md_trimmed(self, tmp_path):
        """load_soul strips leading/trailing whitespace from IDENTITY.md."""
        _write(tmp_path / ".navig" / "workspace" / "IDENTITY.md", "\n  hello identity  \n")

        with patch("navig.personas.soul_loader.Path.home", return_value=tmp_path):
            result = load_soul()

        assert result == "hello identity"


# ---------------------------------------------------------------------------
# Tests: active space SOUL.md beats workspace IDENTITY.md (step 2 > step 3)
# ---------------------------------------------------------------------------

class TestSpaceSoulBeatsIdentityMd:
    """Active space SOUL.md (step 2) has higher priority than workspace IDENTITY.md (step 3)."""

    def test_space_soul_beats_identity_md(self, tmp_path):
        """An active-space SOUL.md wins over a workspace IDENTITY.md."""
        _write(tmp_path / ".navig" / "spaces" / "myspace" / "SOUL.md", "space soul")
        _write(tmp_path / ".navig" / "workspace" / "IDENTITY.md", "workspace identity")

        with patch("navig.personas.soul_loader.Path.home", return_value=tmp_path):
            result = load_soul(active_space="myspace")

        assert result == "space soul"

    def test_identity_md_used_when_space_soul_absent(self, tmp_path):
        """When no space SOUL.md, IDENTITY.md is used."""
        _write(tmp_path / ".navig" / "workspace" / "IDENTITY.md", "workspace identity")

        with patch("navig.personas.soul_loader.Path.home", return_value=tmp_path):
            result = load_soul(active_space="myspace")

        assert result == "workspace identity"


# ---------------------------------------------------------------------------
# Tests: fallback chain when no workspace files are present
# ---------------------------------------------------------------------------

class TestFallbackChain:
    """Package default and context fallback are used when no user files exist."""

    def test_always_returns_str(self, tmp_path):
        """load_soul always returns a str (never None or raises)."""
        with patch("navig.personas.soul_loader.Path.home", return_value=tmp_path):
            result = load_soul()
        assert isinstance(result, str)

    def test_load_soul_no_persona_no_space_returns_package_default(self):
        """With no persona/space arguments and no user overrides, the package default is returned."""
        result = load_soul()
        # The package ships SOUL.default.md, so a non-empty string is expected.
        assert isinstance(result, str)
        assert len(result) > 0
