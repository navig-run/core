from unittest.mock import patch

import pytest

from navig.blackbox.export import export_bundle
from navig.blackbox.types import Bundle


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def dummy_bundle():
    from datetime import datetime

    return Bundle(
        id="test123",
        created_at=datetime.now(),
        navig_version="1.0.0",
        events=[],
        crash_reports=[],
        log_tails={},
        manifest_hash="hash",
        sealed=True,
    )


def test_export_bundle_unencrypted(tmp_dir, dummy_bundle):
    out = tmp_dir / "out"
    with patch(
        "navig.blackbox.export.write_bundle", return_value=(tmp_dir / "out.navbox")
    ):
        res = export_bundle(dummy_bundle, out, encrypted=False)
        assert res == (tmp_dir / "out.navbox")


def test_export_bundle_encrypted_success(tmp_dir, dummy_bundle):
    out = tmp_dir / "out"
    zip_path = tmp_dir / "out.navbox"
    zip_path.write_bytes(b"zipdata")

    with (
        patch("navig.blackbox.export.write_bundle", return_value=zip_path),
        patch("navig.vault.core_v2.get_vault_v2") as mock_v2,
        patch("navig.vault.crypto.CryptoEngine.seal", return_value=b"sealed_data"),
    ):

        mock_v2.return_value.engine.return_value.derive_key.return_value = b"masterkey"

        res = export_bundle(dummy_bundle, out, encrypted=True)

        assert res.suffix == ".enc"
        assert res.read_bytes() == b"sealed_data"
        assert not zip_path.exists()


def test_export_bundle_encrypted_failure_fallback(tmp_dir, dummy_bundle):
    out = tmp_dir / "out"
    zip_path = tmp_dir / "out.navbox"
    zip_path.write_bytes(b"zipdata")

    # Force an exception in encryption
    with (
        patch("navig.blackbox.export.write_bundle", return_value=zip_path),
        patch(
            "navig.vault.core_v2.get_vault_v2",
            side_effect=Exception("Failed to load vault"),
        ),
    ):

        res = export_bundle(dummy_bundle, out, encrypted=True)

        # Falls back to unencrypted and prints a warning
        assert res == zip_path
        assert res.exists()
