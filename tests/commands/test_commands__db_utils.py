"""Tests for navig.commands._db_utils — create_mysql_config_file, calculate_file_checksum."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.commands._db_utils import (
    calculate_file_checksum,
    create_mysql_config_file,
)


# ---------------------------------------------------------------------------
# create_mysql_config_file
# ---------------------------------------------------------------------------

class TestCreateMysqlConfigFile:
    def _cleanup(self, path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass

    def test_returns_path_string(self):
        path = create_mysql_config_file("root", "secret")
        try:
            assert isinstance(path, str)
        finally:
            self._cleanup(path)

    def test_file_exists(self):
        path = create_mysql_config_file("root", "secret")
        try:
            assert os.path.exists(path)
        finally:
            self._cleanup(path)

    def test_file_extension_cnf(self):
        path = create_mysql_config_file("root", "pass")
        try:
            assert path.endswith(".cnf")
        finally:
            self._cleanup(path)

    def test_filename_has_navig_prefix(self):
        path = create_mysql_config_file("root", "pass")
        try:
            assert "navig_mysql_" in os.path.basename(path)
        finally:
            self._cleanup(path)

    def test_file_contains_client_section(self):
        path = create_mysql_config_file("root", "secret")
        try:
            content = Path(path).read_text(encoding="utf-8")
            assert "[client]" in content
        finally:
            self._cleanup(path)

    def test_file_contains_user(self):
        path = create_mysql_config_file("admin", "pass")
        try:
            content = Path(path).read_text(encoding="utf-8")
            assert "user=admin" in content
        finally:
            self._cleanup(path)

    def test_file_contains_password(self):
        path = create_mysql_config_file("root", "my$up3rP@ss")
        try:
            content = Path(path).read_text(encoding="utf-8")
            assert "password=my$up3rP@ss" in content
        finally:
            self._cleanup(path)

    def test_different_calls_return_different_files(self):
        path1 = create_mysql_config_file("root", "pass1")
        path2 = create_mysql_config_file("root", "pass2")
        try:
            assert path1 != path2
        finally:
            self._cleanup(path1)
            self._cleanup(path2)

    def test_empty_password_allowed(self):
        path = create_mysql_config_file("root", "")
        try:
            content = Path(path).read_text(encoding="utf-8")
            assert "password=" in content
        finally:
            self._cleanup(path)

    def test_raises_on_mkstemp_failure(self):
        with patch("navig.commands._db_utils.tempfile.mkstemp", side_effect=OSError("no space")):
            with pytest.raises(OSError):
                create_mysql_config_file("root", "pass")


# ---------------------------------------------------------------------------
# calculate_file_checksum
# ---------------------------------------------------------------------------

class TestCalculateFileChecksum:
    def test_sha256_default(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        checksum = calculate_file_checksum(f)
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert checksum == expected

    def test_md5_algorithm(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"test data")
        checksum = calculate_file_checksum(f, algorithm="md5")
        expected = hashlib.md5(b"test data").hexdigest()  # noqa: S324
        assert checksum == expected

    def test_sha1_algorithm(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"data")
        checksum = calculate_file_checksum(f, algorithm="sha1")
        expected = hashlib.sha1(b"data").hexdigest()  # noqa: S324
        assert checksum == expected

    def test_returns_hex_string(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"data")
        checksum = calculate_file_checksum(f)
        assert isinstance(checksum, str)
        assert all(c in "0123456789abcdef" for c in checksum)

    def test_sha256_length_is_64(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"data")
        assert len(calculate_file_checksum(f, "sha256")) == 64

    def test_md5_length_is_32(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"data")
        assert len(calculate_file_checksum(f, "md5")) == 32

    def test_empty_file_has_known_sha256(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        checksum = calculate_file_checksum(f)
        assert checksum == hashlib.sha256(b"").hexdigest()

    def test_deterministic_for_same_content(self, tmp_path):
        f = tmp_path / "test.bin"
        data = b"consistent content"
        f.write_bytes(data)
        assert calculate_file_checksum(f) == calculate_file_checksum(f)

    def test_different_content_different_checksum(self, tmp_path):
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"content A")
        f2.write_bytes(b"content B")
        assert calculate_file_checksum(f1) != calculate_file_checksum(f2)

    def test_large_file_works(self, tmp_path):
        f = tmp_path / "large.bin"
        data = b"x" * (1024 * 1024)  # 1MB
        f.write_bytes(data)
        checksum = calculate_file_checksum(f)
        assert checksum == hashlib.sha256(data).hexdigest()
