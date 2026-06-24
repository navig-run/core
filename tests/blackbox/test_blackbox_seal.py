"""Tests for navig.blackbox.seal — seal_bundle, is_sealed, unseal."""
from __future__ import annotations

import plistlib
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.blackbox.seal import is_sealed, seal_bundle, unseal
from navig.blackbox.types import Bundle


def _bundle() -> Bundle:
    return Bundle(
        id="test",
        created_at=datetime(2024, 1, 1),
        navig_version="1.0.0",
        events=[],
        crash_reports=[],
        log_tails={},
        manifest_hash="abc",
    )


class TestSealBundle:
    def test_sets_sealed_true(self, tmp_path):
        b = _bundle()
        result = seal_bundle(b, blackbox_dir=tmp_path)
        assert result.sealed is True

    def test_returns_same_bundle(self, tmp_path):
        b = _bundle()
        result = seal_bundle(b, blackbox_dir=tmp_path)
        assert result is b

    def test_writes_seal_marker(self, tmp_path):
        seal_bundle(_bundle(), blackbox_dir=tmp_path)
        assert (tmp_path / "SEALED").exists()

    def test_seal_marker_contains_timestamp(self, tmp_path):
        b = _bundle()
        seal_bundle(b, blackbox_dir=tmp_path)
        content = (tmp_path / "SEALED").read_text()
        assert "2024-01-01" in content

    def test_creates_dir_if_missing(self, tmp_path):
        bb_dir = tmp_path / "new" / "blackbox"
        seal_bundle(_bundle(), blackbox_dir=bb_dir)
        assert bb_dir.exists()


class TestIsSealed:
    def test_false_when_no_marker(self, tmp_path):
        assert is_sealed(blackbox_dir=tmp_path) is False

    def test_true_after_seal(self, tmp_path):
        seal_bundle(_bundle(), blackbox_dir=tmp_path)
        assert is_sealed(blackbox_dir=tmp_path) is True


class TestUnseal:
    def test_returns_true_when_marker_present(self, tmp_path):
        seal_bundle(_bundle(), blackbox_dir=tmp_path)
        assert unseal(blackbox_dir=tmp_path) is True

    def test_removes_marker(self, tmp_path):
        seal_bundle(_bundle(), blackbox_dir=tmp_path)
        unseal(blackbox_dir=tmp_path)
        assert not (tmp_path / "SEALED").exists()

    def test_returns_false_when_not_sealed(self, tmp_path):
        assert unseal(blackbox_dir=tmp_path) is False

    def test_roundtrip_seal_unseal(self, tmp_path):
        b = _bundle()
        seal_bundle(b, blackbox_dir=tmp_path)
        assert is_sealed(blackbox_dir=tmp_path)
        unseal(blackbox_dir=tmp_path)
        assert not is_sealed(blackbox_dir=tmp_path)
