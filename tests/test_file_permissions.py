"""Tests for navig.core.file_permissions — set_owner_only_file_permissions."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.core.file_permissions import set_owner_only_file_permissions


class TestSetOwnerOnlyFilePermissions:
    def test_does_not_raise_on_valid_file(self, tmp_path):
        f = tmp_path / "secret.txt"
        f.write_text("content", encoding="utf-8")
        set_owner_only_file_permissions(f)  # must not raise

    def test_accepts_string_path(self, tmp_path):
        f = tmp_path / "secret.txt"
        f.write_text("x", encoding="utf-8")
        set_owner_only_file_permissions(str(f))  # must not raise

    def test_accepts_path_object(self, tmp_path):
        f = tmp_path / "secret.txt"
        f.write_text("x", encoding="utf-8")
        set_owner_only_file_permissions(Path(f))  # must not raise

    @pytest.mark.skipif(os.name == "nt", reason="chmod only on POSIX")
    def test_posix_sets_600(self, tmp_path):
        f = tmp_path / "key.pem"
        f.write_text("BEGIN RSA PRIVATE KEY", encoding="utf-8")
        set_owner_only_file_permissions(f)
        mode = f.stat().st_mode & 0o777
        assert mode == 0o600

    def test_does_not_raise_on_missing_file(self, tmp_path):
        # The function is best-effort; should silently absorb failures
        missing = tmp_path / "nonexistent.txt"
        set_owner_only_file_permissions(missing)  # must not raise

    @pytest.mark.skipif(os.name != "nt", reason="Windows-only path")
    def test_windows_calls_icacls(self, tmp_path):
        import subprocess

        f = tmp_path / "win_secret.txt"
        f.write_text("data", encoding="utf-8")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            set_owner_only_file_permissions(f)
        assert mock_run.called
