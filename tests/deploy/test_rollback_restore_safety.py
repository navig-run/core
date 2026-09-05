"""A restore must never destroy the deployment it is supposed to be saving.

`restore_snapshot` used to send one command::

    rm -rf <target> && mv <snapshot> <target>

which deletes the live deployment *before* knowing whether it can be replaced. Three
ways that loses a site, all reachable:

* **the snapshot is not there.** The state file is local and the snapshots are remote,
  so they drift — a pruned snapshot, a restored `~/.navig`, a different host. `rm -rf`
  runs first, `mv` then fails, and the deployment is gone with nothing to put back.
* **`mv` fails for any other reason** (permissions, full disk): same outcome.
* **`mv` CONSUMES the snapshot.** After one successful rollback the snapshot no longer
  exists, but `last_deploy_<app>.json` still names it. Running `navig deploy rollback`
  a second time — the obvious thing to do when the first did not fix it — deletes the
  deployment and then fails. Nothing is left.

The existing tests asserted only the returned `(success, message)` tuple, never the
command, so the shape was never examined by anything.

These tests pin the sequence itself, and then run the generated script against a real
temporary filesystem so the safety properties are demonstrated rather than asserted.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from navig.deploy.models import BackupConfig, SnapshotRecord
from navig.deploy.rollback import RollbackManager

SNAP = "/var/backups/myapp/20240101"


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _manager(tmp_path: Path, remote: MagicMock, *, target: str = "/var/www/myapp") -> RollbackManager:
    return RollbackManager(
        backup_cfg=BackupConfig(enabled=True, remote_path="/var/backups", keep_last=5),
        deploy_target=target,
        app_name="myapp",
        server_config={"host": "example.com"},
        remote_ops=remote,
        cache_dir=tmp_path,
        dry_run=False,
    )


def _script(tmp_path: Path, **kw) -> str:
    remote = MagicMock()
    remote.execute_command.return_value = _result(0)
    _manager(tmp_path, remote, **kw).restore_snapshot(
        SnapshotRecord(path=SNAP, created_at="20240101")
    )
    return remote.execute_command.call_args[0][0]


# ── the sequence itself ─────────────────────────────────────────────────────────


def test_the_snapshot_is_checked_before_anything_is_removed(tmp_path: Path) -> None:
    script = _script(tmp_path)

    guard = script.index(f"if [ ! -e {SNAP} ]")
    first_removal = script.index("rm -rf")
    assert guard < first_removal, "the existence check must come first"


def test_the_deployment_is_never_deleted_outright(tmp_path: Path) -> None:
    """`rm -rf <target>` as the first act is the whole bug."""
    script = _script(tmp_path)

    # nothing removes the target itself before the copy succeeds — only the staging dir
    before_copy = script.split("cp -a")[0]
    assert "rm -rf /var/www/myapp;" not in before_copy
    assert "rm -rf /var/www/myapp " not in before_copy
    assert "mv /var/www/myapp /var/www/myapp.navig-rollback-tmp" in script


def test_the_snapshot_is_copied_not_moved(tmp_path: Path) -> None:
    """`mv` consumes the snapshot; a second rollback then has nothing."""
    script = _script(tmp_path)

    assert f"cp -a {SNAP} /var/www/myapp" in script
    assert f"mv {SNAP}" not in script


def test_paths_are_shell_quoted(tmp_path: Path) -> None:
    script = _script(tmp_path, target="/var/www/my app")

    assert "'/var/www/my app'" in script
    assert "/var/www/my app " not in script.replace("'/var/www/my app'", "")


# ── failure modes are distinguishable ───────────────────────────────────────────


def test_a_missing_snapshot_reports_the_deployment_is_untouched(tmp_path: Path) -> None:
    remote = MagicMock()
    remote.execute_command.return_value = _result(RollbackManager._EXIT_SNAPSHOT_MISSING)

    ok, msg = _manager(tmp_path, remote).restore_snapshot(
        SnapshotRecord(path=SNAP, created_at="20240101")
    )

    assert ok is False
    assert "no longer exists" in msg
    assert "left untouched" in msg


def test_a_failed_copy_reports_the_deployment_was_put_back(tmp_path: Path) -> None:
    remote = MagicMock()
    remote.execute_command.return_value = _result(
        RollbackManager._EXIT_COPY_FAILED, stderr="No space left on device"
    )

    ok, msg = _manager(tmp_path, remote).restore_snapshot(
        SnapshotRecord(path=SNAP, created_at="20240101")
    )

    assert ok is False
    assert "put back" in msg
    assert "No space left on device" in msg


def test_a_failed_setaside_reports_the_deployment_is_untouched(tmp_path: Path) -> None:
    remote = MagicMock()
    remote.execute_command.return_value = _result(
        RollbackManager._EXIT_SETASIDE_FAILED, stderr="Permission denied"
    )

    ok, msg = _manager(tmp_path, remote).restore_snapshot(
        SnapshotRecord(path=SNAP, created_at="20240101")
    )

    assert ok is False
    assert "left untouched" in msg


def test_success_still_reports_the_snapshot_it_restored(tmp_path: Path) -> None:
    remote = MagicMock()
    remote.execute_command.return_value = _result(0)

    ok, msg = _manager(tmp_path, remote).restore_snapshot(
        SnapshotRecord(path=SNAP, created_at="20240101")
    )

    assert ok is True
    assert SNAP in msg


# ── executed against a real filesystem ──────────────────────────────────────────

_SH = shutil.which("bash") or shutil.which("sh")
needs_sh = pytest.mark.skipif(_SH is None, reason="no POSIX shell available")


def _run(tmp_path: Path, *, target: Path, snapshot: Path) -> subprocess.CompletedProcess:
    """Generate the real restore script for these paths and execute it."""
    remote = MagicMock()
    remote.execute_command.return_value = _result(0)
    RollbackManager(
        backup_cfg=BackupConfig(enabled=True, remote_path=str(tmp_path), keep_last=5),
        deploy_target=str(target),
        app_name="myapp",
        server_config={},
        remote_ops=remote,
        cache_dir=tmp_path,
        dry_run=False,
    ).restore_snapshot(SnapshotRecord(path=str(snapshot), created_at="20240101"))
    script = remote.execute_command.call_args[0][0]
    return subprocess.run([_SH, "-c", script], capture_output=True, text=True, timeout=30)


@needs_sh
def test_restore_really_replaces_the_deployment(tmp_path: Path) -> None:
    target = tmp_path / "site"
    target.mkdir()
    (target / "index.html").write_text("BROKEN", encoding="utf-8")
    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    (snapshot / "index.html").write_text("GOOD", encoding="utf-8")

    proc = _run(tmp_path, target=target, snapshot=snapshot)

    assert proc.returncode == 0, proc.stderr
    assert (target / "index.html").read_text(encoding="utf-8") == "GOOD"
    assert not (tmp_path / "site.navig-rollback-tmp").exists()


@needs_sh
def test_the_snapshot_survives_so_rollback_can_be_run_again(tmp_path: Path) -> None:
    """The old `mv` left nothing behind; the second rollback then deleted the site."""
    target = tmp_path / "site"
    target.mkdir()
    (target / "index.html").write_text("BROKEN", encoding="utf-8")
    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    (snapshot / "index.html").write_text("GOOD", encoding="utf-8")

    assert _run(tmp_path, target=target, snapshot=snapshot).returncode == 0
    assert snapshot.exists(), "snapshot must survive a restore"

    # second rollback: still works, still leaves the site in place
    assert _run(tmp_path, target=target, snapshot=snapshot).returncode == 0
    assert (target / "index.html").read_text(encoding="utf-8") == "GOOD"


@needs_sh
def test_a_missing_snapshot_leaves_the_deployment_intact(tmp_path: Path) -> None:
    """The catastrophic case: the old command deleted the site, then failed."""
    target = tmp_path / "site"
    target.mkdir()
    (target / "index.html").write_text("LIVE SITE", encoding="utf-8")

    proc = _run(tmp_path, target=target, snapshot=tmp_path / "gone")

    assert proc.returncode == RollbackManager._EXIT_SNAPSHOT_MISSING
    assert (target / "index.html").read_text(encoding="utf-8") == "LIVE SITE"


@needs_sh
def test_the_old_command_would_have_destroyed_the_site(tmp_path: Path) -> None:
    """Demonstrate the regression this replaces, so the risk is not theoretical."""
    target = tmp_path / "site"
    target.mkdir()
    (target / "index.html").write_text("LIVE SITE", encoding="utf-8")
    missing = tmp_path / "gone"

    old = f"rm -rf '{target}' && mv '{missing}' '{target}'"
    proc = subprocess.run([_SH, "-c", old], capture_output=True, text=True, timeout=30)

    assert proc.returncode != 0        # the restore "failed"…
    assert not target.exists()         # …and took the deployment with it


@needs_sh
def test_the_snapshot_round_trips_permissions_and_timestamps(tmp_path: Path) -> None:
    """`cp -r` resets every mtime; a backup that does not round-trip is not a backup."""
    target = tmp_path / "site"
    target.mkdir()
    script = target / "run.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    subprocess.run([_SH, "-c", f"chmod 755 '{script}'; touch -t 202001010000 '{script}'"], check=True)
    before = script.stat()

    remote = MagicMock()
    remote.execute_command.return_value = _result(0)
    mgr = RollbackManager(
        backup_cfg=BackupConfig(enabled=True, remote_path=str(tmp_path / "backups"), keep_last=5),
        deploy_target=str(target),
        app_name="myapp",
        server_config={},
        remote_ops=remote,
        cache_dir=tmp_path,
        dry_run=False,
    )
    record = mgr.create_snapshot()
    # run the generated cp for real (the second call is the copy, the first is mkdir)
    for call in remote.execute_command.call_args_list:
        subprocess.run([_SH, "-c", call[0][0]], capture_output=True, timeout=30)

    copied = Path(record.path) / "run.sh"
    assert copied.exists(), "snapshot did not contain the file"
    assert int(copied.stat().st_mtime) == int(before.st_mtime), "timestamps must survive"
    assert copied.stat().st_mode == before.st_mode


@needs_sh
def test_restore_onto_a_host_with_no_current_deployment(tmp_path: Path) -> None:
    """First deploy failed outright: there is no target to set aside."""
    target = tmp_path / "site"
    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    (snapshot / "index.html").write_text("GOOD", encoding="utf-8")

    proc = _run(tmp_path, target=target, snapshot=snapshot)

    assert proc.returncode == 0, proc.stderr
    assert (target / "index.html").read_text(encoding="utf-8") == "GOOD"
