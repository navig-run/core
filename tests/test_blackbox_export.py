"""Tests for navig.blackbox.export — export_bundle."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.blackbox.export import export_bundle
from navig.blackbox.types import Bundle


def _bundle() -> Bundle:
    return Bundle(
        id="test-bundle",
        created_at=datetime(2024, 1, 1),
        navig_version="1.0.0",
        events=[],
        crash_reports=[],
        log_tails={},
        manifest_hash="abc123",
    )


class TestExportBundle:
    def test_returns_zip_path_when_not_encrypted(self, tmp_path):
        expected = tmp_path / "bundle.navbox"
        with patch("navig.blackbox.export.write_bundle", return_value=expected) as mock_wb:
            result = export_bundle(_bundle(), tmp_path / "bundle", encrypted=False)
        assert result == expected
        mock_wb.assert_called_once()

    def test_encrypt_false_by_default(self, tmp_path):
        expected = tmp_path / "bundle.navbox"
        with patch("navig.blackbox.export.write_bundle", return_value=expected):
            result = export_bundle(_bundle(), tmp_path / "bundle")
        assert result == expected

    def test_encrypted_returns_enc_path(self, tmp_path):
        zip_path = tmp_path / "bundle.navbox"
        zip_path.write_bytes(b"fake zip content")
        enc_path = zip_path.with_suffix(".navbox.enc")

        mock_vault = MagicMock()
        mock_vault.engine.return_value.derive_key.return_value = b"key"
        mock_crypto = MagicMock()
        mock_crypto.seal.return_value = b"encrypted"

        with patch("navig.blackbox.export.write_bundle", return_value=zip_path):
            with patch.dict(
                "sys.modules",
                {
                    "navig.vault.core": MagicMock(get_vault=MagicMock(return_value=mock_vault)),
                    "navig.vault.crypto": MagicMock(CryptoEngine=mock_crypto),
                },
            ):
                result = export_bundle(_bundle(), tmp_path / "bundle", encrypted=True)

        assert result == enc_path
        assert enc_path.exists()
        assert not zip_path.exists()  # plaintext removed

    def test_encryption_failure_returns_zip(self, tmp_path):
        zip_path = tmp_path / "bundle.navbox"
        zip_path.write_bytes(b"content")

        import builtins
        real_import = builtins.__import__

        def bad_import(name, *args, **kwargs):
            if "vault" in name:
                raise ImportError("no vault")
            return real_import(name, *args, **kwargs)

        with patch("navig.blackbox.export.write_bundle", return_value=zip_path):
            with patch("builtins.__import__", side_effect=bad_import):
                result = export_bundle(_bundle(), tmp_path / "bundle", encrypted=True)

        # Should fall back to plaintext zip
        assert result == zip_path

    def test_write_bundle_called_with_output_path(self, tmp_path):
        output = tmp_path / "out.navbox"
        with patch("navig.blackbox.export.write_bundle", return_value=output) as mock_wb:
            export_bundle(_bundle(), output, encrypted=False)
        args = mock_wb.call_args[0]
        assert args[1] == output
