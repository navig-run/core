"""Tests for navig.update.checker — VersionChecker."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.update.checker import VersionChecker
from navig.update.models import VersionInfo
from navig.update.sources import SourceError


def _mock_source(label: str = "pypi", latest: str | None = "9.9.9") -> MagicMock:
    src = MagicMock()
    src.label = label
    if latest is None:
        src.latest_version.side_effect = SourceError("unavailable")
    else:
        src.latest_version.return_value = latest
    return src


class TestLatestCached:
    def test_returns_version_from_source(self):
        checker = VersionChecker(source=_mock_source(latest="1.2.3"))
        result = checker.latest_from_source()
        assert result == "1.2.3"

    def test_caches_result(self):
        src = _mock_source(latest="1.0.0")
        checker = VersionChecker(source=src)
        checker.latest_from_source()
        checker.latest_from_source()
        # Source called only once due to cache
        src.latest_version.assert_called_once()

    def test_returns_none_on_source_error(self):
        src = _mock_source(latest=None)
        checker = VersionChecker(source=src)
        assert checker.latest_from_source() is None

    def test_uses_pre_populated_cache(self):
        src = _mock_source(latest="5.0.0")
        cache = {"pypi": "3.0.0"}
        checker = VersionChecker(source=src, cache=cache)
        assert checker.latest_from_source() == "3.0.0"
        src.latest_version.assert_not_called()


class TestCheckLocal:
    def test_returns_version_info(self):
        checker = VersionChecker(source=_mock_source())
        info = checker.check_local()
        assert isinstance(info, VersionInfo)
        assert info.node_id == "local"

    def test_latest_populated(self):
        checker = VersionChecker(source=_mock_source(latest="9.9.9"))
        info = checker.check_local()
        assert info.latest == "9.9.9"

    def test_install_type_is_git_or_pip(self):
        checker = VersionChecker(source=_mock_source())
        info = checker.check_local()
        assert info.install_type in ("git", "pip")

    def test_no_error_when_source_works(self):
        checker = VersionChecker(source=_mock_source())
        info = checker.check_local()
        assert info.error is None


class TestCheckSSH:
    def _mock_remote(self, stdout: str = "navig 2.4.0") -> MagicMock:
        remote = MagicMock()
        result = MagicMock()
        result.stdout = stdout
        remote.execute_command.return_value = result
        return remote

    def test_returns_version_info(self):
        remote = self._mock_remote("navig 2.4.0")
        checker = VersionChecker(source=_mock_source(), remote_ops=remote)
        info = checker.check_ssh("node1", {"host": "10.0.0.1"})
        assert isinstance(info, VersionInfo)
        assert info.node_id == "node1"

    def test_parses_semver_from_plain_output(self):
        remote = self._mock_remote("navig 2.4.16")
        checker = VersionChecker(source=_mock_source(), remote_ops=remote)
        info = checker.check_ssh("node1", {})
        assert info.current == "2.4.16"

    def test_parses_json_output(self):
        import json
        payload = json.dumps({"version": "3.1.0", "install_type": "pip"})
        remote = self._mock_remote(payload)
        checker = VersionChecker(source=_mock_source(), remote_ops=remote)
        info = checker.check_ssh("node1", {})
        assert info.current == "3.1.0"
        assert info.install_type == "pip"

    def test_error_captured_on_exception(self):
        remote = MagicMock()
        remote.execute_command.side_effect = Exception("connection refused")
        checker = VersionChecker(source=_mock_source(), remote_ops=remote)
        info = checker.check_ssh("node1", {})
        assert info.error is not None
        assert "connection refused" in info.error
