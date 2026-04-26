"""
Tests for:
  - navig.commands._async_utils      (run_sync)
  - navig.commands._db_utils         (calculate_file_checksum, create_mysql_config_file)
  - navig.commands._interactive_wrappers (run_menu_wrapper)

All tests are hermetic.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import pytest


# ===========================================================================
# navig.commands._async_utils
# ===========================================================================

class TestRunSync:
    def test_runs_simple_coroutine(self):
        from navig.commands._async_utils import run_sync

        async def coro():
            return 42

        assert run_sync(coro()) == 42

    def test_returns_coroutine_result(self):
        from navig.commands._async_utils import run_sync

        async def coro():
            await asyncio.sleep(0)
            return "done"

        assert run_sync(coro()) == "done"

    def test_propagates_exception(self):
        from navig.commands._async_utils import run_sync

        async def coro():
            raise ValueError("async boom")

        with pytest.raises(ValueError, match="async boom"):
            run_sync(coro())

    def test_returns_none_for_void_coro(self):
        from navig.commands._async_utils import run_sync

        async def coro():
            pass

        assert run_sync(coro()) is None

    def test_handles_args_in_coro(self):
        from navig.commands._async_utils import run_sync

        async def add(a, b):
            return a + b

        assert run_sync(add(3, 4)) == 7


# ===========================================================================
# navig.commands._db_utils — calculate_file_checksum
# ===========================================================================

class TestCalculateFileChecksum:
    def test_sha256_matches_manual(self, tmp_path):
        from navig.commands._db_utils import calculate_file_checksum

        test_file = tmp_path / "data.bin"
        content = b"hello world"
        test_file.write_bytes(content)

        expected = hashlib.sha256(content).hexdigest()
        result = calculate_file_checksum(test_file)
        assert result == expected

    def test_md5_algorithm(self, tmp_path):
        from navig.commands._db_utils import calculate_file_checksum

        test_file = tmp_path / "data.bin"
        content = b"test content"
        test_file.write_bytes(content)

        expected = hashlib.md5(content).hexdigest()
        result = calculate_file_checksum(test_file, algorithm="md5")
        assert result == expected

    def test_sha1_algorithm(self, tmp_path):
        from navig.commands._db_utils import calculate_file_checksum

        test_file = tmp_path / "data.bin"
        content = b"another test"
        test_file.write_bytes(content)

        expected = hashlib.sha1(content).hexdigest()
        result = calculate_file_checksum(test_file, algorithm="sha1")
        assert result == expected

    def test_empty_file(self, tmp_path):
        from navig.commands._db_utils import calculate_file_checksum

        test_file = tmp_path / "empty.bin"
        test_file.write_bytes(b"")

        expected = hashlib.sha256(b"").hexdigest()
        result = calculate_file_checksum(test_file)
        assert result == expected

    def test_returns_hex_string(self, tmp_path):
        from navig.commands._db_utils import calculate_file_checksum

        test_file = tmp_path / "data.bin"
        test_file.write_bytes(b"x")

        result = calculate_file_checksum(test_file)
        assert isinstance(result, str)
        # SHA256 hex digest is 64 characters
        assert len(result) == 64

    def test_different_content_different_checksum(self, tmp_path):
        from navig.commands._db_utils import calculate_file_checksum

        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"content A")
        f2.write_bytes(b"content B")

        assert calculate_file_checksum(f1) != calculate_file_checksum(f2)

    def test_same_content_same_checksum(self, tmp_path):
        from navig.commands._db_utils import calculate_file_checksum

        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"identical")
        f2.write_bytes(b"identical")

        assert calculate_file_checksum(f1) == calculate_file_checksum(f2)


class TestCreateMysqlConfigFile:
    def test_creates_file(self):
        from navig.commands._db_utils import create_mysql_config_file

        path = None
        try:
            path = create_mysql_config_file("root", "secret")
            assert os.path.exists(path)
        finally:
            if path and os.path.exists(path):
                os.unlink(path)

    def test_content_has_client_section(self):
        from navig.commands._db_utils import create_mysql_config_file

        path = None
        try:
            path = create_mysql_config_file("admin", "pass123")
            content = open(path, encoding="utf-8").read()
            assert "[client]" in content
            assert "admin" in content
            assert "pass123" in content
        finally:
            if path and os.path.exists(path):
                os.unlink(path)

    def test_file_ends_with_cnf(self):
        from navig.commands._db_utils import create_mysql_config_file

        path = None
        try:
            path = create_mysql_config_file("user", "pw")
            assert path.endswith(".cnf")
        finally:
            if path and os.path.exists(path):
                os.unlink(path)

    def test_credentials_in_content(self):
        from navig.commands._db_utils import create_mysql_config_file

        path = None
        try:
            path = create_mysql_config_file("myuser", "mypass")
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "user=myuser" in content
            assert "password=mypass" in content
        finally:
            if path and os.path.exists(path):
                os.unlink(path)


# ===========================================================================
# navig.commands._interactive_wrappers
# ===========================================================================

class TestRunMenuWrapper:
    def test_calls_command(self):
        from navig.commands._interactive_wrappers import run_menu_wrapper

        calls = []
        def fake_cmd(*args, **kwargs):
            calls.append((args, kwargs))

        run_menu_wrapper(fake_cmd)
        assert len(calls) == 1

    def test_passes_args_to_command(self):
        from navig.commands._interactive_wrappers import run_menu_wrapper

        received = {}
        def fake_cmd(a, b, key="default"):
            received.update({"a": a, "b": b, "key": key})

        run_menu_wrapper(fake_cmd, 10, 20, key="custom")
        assert received == {"a": 10, "b": 20, "key": "custom"}

    def test_returns_none(self):
        from navig.commands._interactive_wrappers import run_menu_wrapper

        def fake_cmd():
            return 42  # return value is ignored

        result = run_menu_wrapper(fake_cmd)
        assert result is None

    def test_propagates_exception(self):
        from navig.commands._interactive_wrappers import run_menu_wrapper

        def fail_cmd():
            raise RuntimeError("oops")

        with pytest.raises(RuntimeError, match="oops"):
            run_menu_wrapper(fail_cmd)
