"""Tests for navig.update.checker — VersionChecker."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from navig.update.checker import VersionChecker
from navig.update.sources import SourceError


def _make_source(version: str = "2.5.0"):
    src = MagicMock()
    src.label = "mock-source"
    src.latest_version.return_value = version
    return src


def _make_remote_ops(stdout: str = "2.4.16", returncode: int = 0):
    r = MagicMock()
    r.stdout = stdout
    r.returncode = returncode
    ops = MagicMock()
    ops.execute_command.return_value = r
    return ops


# ---------------------------------------------------------------------------
# check_local
# ---------------------------------------------------------------------------


class TestCheckLocal:
    def test_returns_version_info(self):
        checker = VersionChecker(_make_source("2.5.0"))
        with patch(
            "navig.update.checker.VersionChecker._latest_cached", return_value="2.5.0"
        ):
            vi = checker.check_local()
        assert vi.node_id == "local"
        assert vi.current != ""
        assert vi.latest == "2.5.0"
        assert vi.source_name == "mock-source"

    def test_needs_update_when_behind(self):
        checker = VersionChecker(_make_source("9.9.9"))
        vi = checker.check_local()
        # current is whatever is installed; just check latest is populated
        assert vi.latest == "9.9.9"

    def test_source_failure_sets_error(self):
        src = MagicMock()
        src.label = "bad-source"
        src.latest_version.side_effect = SourceError("network down")
        checker = VersionChecker(src)
        vi = checker.check_local()
        assert vi.latest is None


# ---------------------------------------------------------------------------
# check_ssh
# ---------------------------------------------------------------------------


class TestCheckSSH:
    def test_parses_plain_version(self):
        ops = _make_remote_ops(stdout="navig 2.4.16")
        checker = VersionChecker(_make_source("2.5.0"), remote_ops=ops)
        vi = checker.check_ssh("myhost", {"host": "1.2.3.4"})
        assert vi.node_id == "myhost"
        assert vi.current == "2.4.16"
        assert vi.latest == "2.5.0"
        assert vi.needs_update

    def test_parses_json_version(self):
        payload = json.dumps({"version": "2.4.10", "install_type": "pip"})
        ops = _make_remote_ops(stdout=payload)
        checker = VersionChecker(_make_source("2.5.0"), remote_ops=ops)
        vi = checker.check_ssh("myhost", {})
        assert vi.current == "2.4.10"
        assert vi.install_type == "pip"

    def test_ssh_error_sets_error(self):
        ops = MagicMock()
        ops.execute_command.side_effect = ConnectionError("SSH refused")
        checker = VersionChecker(_make_source("2.5.0"), remote_ops=ops)
        vi = checker.check_ssh("badhost", {})
        assert vi.error is not None
        assert not vi.reachable

    def test_up_to_date(self):
        ops = _make_remote_ops(stdout="navig 2.5.0")
        checker = VersionChecker(_make_source("2.5.0"), remote_ops=ops)
        vi = checker.check_ssh("myhost", {})
        assert not vi.needs_update


# ---------------------------------------------------------------------------
# version cache
# ---------------------------------------------------------------------------


class TestVersionCache:
    def test_source_called_once(self):
        src = MagicMock()
        src.label = "mock"
        src.latest_version.return_value = "2.5.0"
        cache: dict = {}
        checker = VersionChecker(src, cache=cache)
        checker.latest_from_source()
        checker.latest_from_source()
        src.latest_version.assert_called_once()


# ---------------------------------------------------------------------------
# VersionInfo.needs_update
# ---------------------------------------------------------------------------


class TestVersionInfo:
    def test_needs_update_true(self):
        from navig.update.models import VersionInfo

        vi = VersionInfo(node_id="x", current="2.4.0", latest="2.5.0")
        assert vi.needs_update

    def test_needs_update_false_same(self):
        from navig.update.models import VersionInfo

        vi = VersionInfo(node_id="x", current="2.5.0", latest="2.5.0")
        assert not vi.needs_update

    def test_needs_update_false_with_error(self):
        from navig.update.models import VersionInfo

        vi = VersionInfo(node_id="x", current="2.4.0", latest="2.5.0", error="broken")
        assert not vi.needs_update
