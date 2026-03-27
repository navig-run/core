from datetime import datetime
from unittest.mock import patch

import pytest

from navig.blackbox.seal import is_sealed, seal_bundle, unseal
from navig.blackbox.types import Bundle


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def dummy_bundle():
    return Bundle(
        id="test1234",
        created_at=datetime.now(),
        navig_version="1.0.0",
        events=[],
        crash_reports=[],
        log_tails={},
        manifest_hash="hash",
        sealed=False,
    )


def test_seal_bundle_explicit_dir(tmp_dir, dummy_bundle):
    res = seal_bundle(dummy_bundle, blackbox_dir=tmp_dir)
    assert res.sealed is True
    assert (tmp_dir / "SEALED").exists()
    assert len((tmp_dir / "SEALED").read_text()) > 0


@patch("navig.platform.paths.blackbox_dir")
def test_seal_bundle_default_dir(mock_bbdir, tmp_dir, dummy_bundle):
    mock_bbdir.return_value = tmp_dir
    res = seal_bundle(dummy_bundle)
    assert res.sealed is True
    assert (tmp_dir / "SEALED").exists()


def test_is_sealed_explicit_dir(tmp_dir):
    assert is_sealed(blackbox_dir=tmp_dir) is False
    (tmp_dir / "SEALED").touch()
    assert is_sealed(blackbox_dir=tmp_dir) is True


@patch("navig.platform.paths.blackbox_dir")
def test_is_sealed_default_dir(mock_bbdir, tmp_dir):
    mock_bbdir.return_value = tmp_dir
    assert is_sealed() is False


def test_unseal_explicit_dir(tmp_dir):
    assert unseal(blackbox_dir=tmp_dir) is False
    (tmp_dir / "SEALED").touch()
    assert unseal(blackbox_dir=tmp_dir) is True
    assert not (tmp_dir / "SEALED").exists()


@patch("navig.platform.paths.blackbox_dir")
def test_unseal_default_dir(mock_bbdir, tmp_dir):
    mock_bbdir.return_value = tmp_dir
    (tmp_dir / "SEALED").touch()
    assert unseal() is True
    assert not (tmp_dir / "SEALED").exists()
