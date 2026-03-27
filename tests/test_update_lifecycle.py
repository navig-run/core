"""Tests for navig.update.lifecycle — UpdateEngine."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.update.lifecycle import UpdateEngine
from navig.update.models import VersionInfo
from navig.update.targets import UpdateTarget


def _local_target() -> UpdateTarget:
    return UpdateTarget(node_id="local", type="local")


def _ssh_target(name: str = "myhost") -> UpdateTarget:
    return UpdateTarget(
        node_id=name,
        type="ssh",
        server_config={"host": "1.2.3.4", "port": 22, "username": "root"},
    )


def _make_source(latest: str = "2.5.0"):
    src = MagicMock()
    src.label = "mock"
    src.latest_version.return_value = latest
    return src


# ---------------------------------------------------------------------------
# plan()
# ---------------------------------------------------------------------------


class TestPlan:
    def test_local_up_to_date(self):
        with patch(
            "navig.update.checker.VersionChecker.check_local",
            return_value=VersionInfo("local", current="2.5.0", latest="2.5.0"),
        ):
            engine = UpdateEngine([_local_target()], source=_make_source("2.5.0"))
            plan = engine.plan()
        assert len(plan.up_to_date) == 1
        assert len(plan.to_update) == 0

    def test_local_needs_update(self):
        with patch(
            "navig.update.checker.VersionChecker.check_local",
            return_value=VersionInfo("local", current="2.4.0", latest="2.5.0"),
        ):
            engine = UpdateEngine([_local_target()], source=_make_source("2.5.0"))
            plan = engine.plan()
        assert len(plan.to_update) == 1

    def test_unreachable_ssh(self):
        vi = VersionInfo("myhost", error="SSH refused")
        with patch("navig.update.checker.VersionChecker.check_ssh", return_value=vi):
            engine = UpdateEngine([_ssh_target()], source=_make_source())
            plan = engine.plan()
        assert len(plan.unreachable) == 1

    def test_force_includes_up_to_date(self):
        with patch(
            "navig.update.checker.VersionChecker.check_local",
            return_value=VersionInfo("local", current="2.5.0", latest="2.5.0"),
        ):
            engine = UpdateEngine([_local_target()], source=_make_source("2.5.0"))
            plan = engine.plan(force=True)
        assert len(plan.to_update) == 1


# ---------------------------------------------------------------------------
# run() — dry-run
# ---------------------------------------------------------------------------


class TestRunDryRun:
    def test_dry_run_skips_install(self):
        with patch(
            "navig.update.checker.VersionChecker.check_local",
            return_value=VersionInfo("local", current="2.4.0", latest="2.5.0"),
        ):
            engine = UpdateEngine([_local_target()], source=_make_source())
            result = engine.run(dry_run=True)
        assert result.dry_run
        assert result.success
        nr = result.node("local")
        assert nr is not None
        assert nr.skipped
        assert nr.skip_reason == "dry-run"


# ---------------------------------------------------------------------------
# run() — already up-to-date
# ---------------------------------------------------------------------------


class TestRunUpToDate:
    def test_already_up_to_date_skipped(self):
        with patch(
            "navig.update.checker.VersionChecker.check_local",
            return_value=VersionInfo("local", current="2.5.0", latest="2.5.0"),
        ):
            engine = UpdateEngine([_local_target()], source=_make_source("2.5.0"))
            result = engine.run()
        nr = result.node("local")
        assert nr is not None
        assert nr.skipped
        assert nr.skip_reason == "already up-to-date"


# ---------------------------------------------------------------------------
# run() — successful install
# ---------------------------------------------------------------------------


class TestRunSuccess:
    def test_local_install_success(self):
        vi = VersionInfo("local", current="2.4.0", latest="2.5.0")
        with (
            patch("navig.update.checker.VersionChecker.check_local", return_value=vi),
            patch.object(UpdateEngine, "_install_local", return_value=None),
            patch.object(
                UpdateEngine,
                "_verify_version",
                side_effect=lambda t, nr: setattr(nr, "new_version", "2.5.0"),
            ),
        ):
            engine = UpdateEngine([_local_target()], source=_make_source())
            result = engine.run(force=False)
        assert result.success
        nr = result.node("local")
        assert nr.ok
        assert nr.new_version == "2.5.0"


# ---------------------------------------------------------------------------
# run() — failed install + rollback
# ---------------------------------------------------------------------------


class TestRunRollback:
    def test_rollback_on_install_failure(self):
        vi = VersionInfo("local", current="2.4.0", latest="2.5.0")
        rollback_called = []

        def _fail_install(_channel):
            raise RuntimeError("pip exploded")

        def _track_rollback(target, old_v):
            rollback_called.append(old_v)

        with (
            patch("navig.update.checker.VersionChecker.check_local", return_value=vi),
            patch.object(UpdateEngine, "_install_local", side_effect=_fail_install),
            patch.object(UpdateEngine, "_rollback_node", side_effect=_track_rollback),
        ):
            engine = UpdateEngine([_local_target()], source=_make_source())
            result = engine.run(auto_rollback=True)

        assert not result.success
        nr = result.node("local")
        assert nr.rolled_back
        assert rollback_called == ["2.4.0"]


# ---------------------------------------------------------------------------
# run() — SSH node
# ---------------------------------------------------------------------------


class TestRunSSH:
    def test_ssh_install_called(self):
        vi = VersionInfo("myhost", current="2.4.0", latest="2.5.0")
        with (
            patch("navig.update.checker.VersionChecker.check_ssh", return_value=vi),
            patch.object(UpdateEngine, "_install_ssh", return_value=None),
            patch.object(
                UpdateEngine,
                "_verify_version",
                side_effect=lambda t, nr: setattr(nr, "new_version", "2.5.0"),
            ),
        ):
            engine = UpdateEngine([_ssh_target()], source=_make_source())
            result = engine.run()
        assert result.success


# ---------------------------------------------------------------------------
# history recording
# ---------------------------------------------------------------------------


class TestHistoryRecording:
    def test_record_written(self, tmp_path):
        vi = VersionInfo("local", current="2.4.0", latest="2.5.0")
        with (
            patch("navig.update.checker.VersionChecker.check_local", return_value=vi),
            patch.object(UpdateEngine, "_install_local", return_value=None),
            patch.object(
                UpdateEngine,
                "_verify_version",
                side_effect=lambda t, nr: setattr(nr, "new_version", "2.5.0"),
            ),
        ):
            engine = UpdateEngine(
                [_local_target()], source=_make_source(), cache_dir=str(tmp_path)
            )
            engine.run()

        hist_file = tmp_path / "update_history.jsonl"
        assert hist_file.exists()
        import json

        entries = [
            json.loads(l) for l in hist_file.read_text().splitlines() if l.strip()
        ]
        assert len(entries) == 1
        assert entries[0]["old_version"] == "2.4.0"
        assert entries[0]["new_version"] == "2.5.0"
