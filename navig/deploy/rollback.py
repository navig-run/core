"""
navig.deploy.rollback — Remote snapshot creation and restoration.

Strategy:
  - Pre-push: `cp -a <target> <snapshot_dir>/<timestamp>` on the remote host.
    This is instantaneous (same-disk copy), no bandwidth required. `-a` so the snapshot
    round-trips: mode, ownership and timestamps all survive.
  - On failure: stage the current deployment aside, copy the snapshot into place, then
    drop the staged copy — and put it back if the copy fails. The deployment is never
    removed before a replacement exists, and the snapshot is preserved so rollback can
    be run again.
  - Pruning: keep only the N most recent snapshots per app.
  - Local state: ~/.navig/cache/last_deploy_<app>.json tracks the last snapshot.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from navig.core.yaml_io import atomic_write_text
from navig.deploy.models import BackupConfig, SnapshotRecord

logger = logging.getLogger(__name__)


class RollbackManager:
    """Manages remote snapshots and rollback for a single deploy target."""

    # Exit codes the restore script uses to say WHICH step failed, so the caller can
    # tell "your deployment is untouched" from "your deployment was put back".
    _EXIT_SNAPSHOT_MISSING = 3
    _EXIT_SETASIDE_FAILED = 4
    _EXIT_COPY_FAILED = 5

    def __init__(
        self,
        backup_cfg: BackupConfig,
        deploy_target: str,  # e.g. /var/www/myapp
        app_name: str,
        server_config: dict[str, Any],
        remote_ops: Any,
        cache_dir: Path,
        dry_run: bool = False,
    ):
        self._bcfg = backup_cfg
        self._target = deploy_target.rstrip("/")
        self._app = app_name
        self._server = server_config
        self._remote = remote_ops
        self._dry_run = dry_run
        self._state_path = cache_dir / f"last_deploy_{app_name}.json"

        # Build remote snapshot base dir: e.g. /var/backups/myapp
        base = backup_cfg.remote_path.rstrip("/")
        self._snapshot_base = f"{base}/{app_name}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_snapshot(self) -> SnapshotRecord | None:
        """
        Create a timestamped remote snapshot of the current deploy target.

        Returns the SnapshotRecord if successful, None if backup is disabled.
        """
        if not self._bcfg.enabled:
            return None

        ts = time.strftime("%Y%m%d_%H%M%S")
        snap_path = f"{self._snapshot_base}/{ts}"

        if self._dry_run:
            logger.info("[DRY RUN] snapshot: cp -r %s %s", self._target, snap_path)
            return SnapshotRecord(path=snap_path, created_at=ts)

        import shlex

        target_safe = shlex.quote(self._target)
        snap_base_safe = shlex.quote(self._snapshot_base)
        snap_path_safe = shlex.quote(snap_path)

        # Ensure snapshot base dir exists
        mkdir_cmd = f"mkdir -p {snap_base_safe}"
        r = self._remote.execute_command(mkdir_cmd, self._server)
        if r.returncode != 0:
            raise RuntimeError(f"Could not create snapshot dir {self._snapshot_base}: {r.stderr}")

        # Create snapshot (cp, not rsync — same disk = fast).
        # `-a`, not `-r`: `cp -r` resets every timestamp to the moment of the snapshot
        # and does not carry ownership, so restoring one handed back a deployment that
        # differed from the original — enough to invalidate mtime-based caches and
        # incremental rsync, and to change file ownership when the deploy user differs
        # from the service user. A backup has to round-trip.
        cp_cmd = f"cp -a {target_safe} {snap_path_safe}"
        r = self._remote.execute_command(cp_cmd, self._server)
        if r.returncode != 0:
            raise RuntimeError(f"Snapshot failed: {r.stderr or r.stdout}")

        record = SnapshotRecord(path=snap_path, created_at=ts)
        logger.info("Snapshot created: %s", snap_path)
        return record

    def restore_snapshot(self, snapshot: SnapshotRecord | None = None) -> tuple[bool, str]:
        """
        Restore from the given snapshot (or load from last_deploy state file).

        Returns (success, message).
        """
        if snapshot is None:
            snapshot = self._load_last_snapshot()
        if snapshot is None:
            return False, "No snapshot available to restore from."

        snap_path = snapshot.path

        if self._dry_run:
            return True, f"[DRY RUN] would restore {snap_path} → {self._target}"

        import shlex

        target_safe = shlex.quote(self._target)
        snap_path_safe = shlex.quote(snap_path)
        staged = f"{self._target}.navig-rollback-tmp"
        staged_safe = shlex.quote(staged)

        # The restore used to be `rm -rf <target> && mv <snapshot> <target>`, which
        # destroys the live deployment BEFORE knowing whether it can be replaced:
        #   * a snapshot that no longer exists (pruned, wrong host, stale state file)
        #     left the target deleted and nothing put back;
        #   * any `mv` failure — permissions, a full disk — did the same;
        #   * `mv` CONSUMES the snapshot, so running `navig deploy rollback` twice
        #     deleted the deployment and then failed, with nothing left to restore from.
        #
        # This sequence never removes the deployment until a copy is in place, keeps the
        # snapshot so rollback is repeatable, and puts the original back if the copy
        # fails. `cp -a` (not `-r`) preserves mode, ownership and timestamps.
        script = (
            f"if [ ! -e {snap_path_safe} ]; then exit {self._EXIT_SNAPSHOT_MISSING}; fi; "
            f"rm -rf {staged_safe}; "
            f"if [ -e {target_safe} ]; then "
            f"mv {target_safe} {staged_safe} || exit {self._EXIT_SETASIDE_FAILED}; fi; "
            f"if cp -a {snap_path_safe} {target_safe}; then rm -rf {staged_safe}; else "
            f"rm -rf {target_safe}; "
            f"if [ -e {staged_safe} ]; then mv {staged_safe} {target_safe}; fi; "
            f"exit {self._EXIT_COPY_FAILED}; fi"
        )
        r = self._remote.execute_command(script, self._server)

        if r.returncode == self._EXIT_SNAPSHOT_MISSING:
            return False, (
                f"Snapshot {snap_path} no longer exists on the host — "
                f"{self._target} was left untouched."
            )
        if r.returncode == self._EXIT_SETASIDE_FAILED:
            return False, (
                f"Could not move {self._target} aside — it was left untouched. "
                f"{r.stderr or r.stdout}".rstrip()
            )
        if r.returncode == self._EXIT_COPY_FAILED:
            return False, (
                f"Restore failed while copying {snap_path}; {self._target} was put back. "
                f"{r.stderr or r.stdout}".rstrip()
            )
        if r.returncode != 0:
            return False, f"Restore failed: {r.stderr or r.stdout}"

        return True, f"Restored from {snap_path}"

    def save_state(self, record: SnapshotRecord) -> None:
        """Persist snapshot record locally for future rollback commands."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self._state_path,
            json.dumps({"path": record.path, "created_at": record.created_at}),
        )

    def load_state(self) -> SnapshotRecord | None:
        """Load the last known snapshot from local state file."""
        return self._load_last_snapshot()

    def prune_old_snapshots(self) -> None:
        """Remove all but the N most recent snapshots from the remote host."""
        keep = self._bcfg.keep_last
        if keep <= 0 or self._dry_run:
            return

        import shlex

        snap_base_safe = shlex.quote(self._snapshot_base)

        # List snapshots, sorted by name descending (timestamps sort lexicographically)
        list_cmd = f"ls -dt {snap_base_safe}/*/ 2>/dev/null | tail -n +{keep + 1}"
        r = self._remote.execute_command(list_cmd, self._server)
        old = [line.strip() for line in (r.stdout or "").splitlines() if line.strip()]
        if not old:
            return

        for path in old:
            path_safe = shlex.quote(path)
            rm_cmd = f"rm -rf {path_safe}"
            res = self._remote.execute_command(rm_cmd, self._server)
            if res.returncode == 0:
                logger.info("Pruned old snapshot: %s", path)
            else:
                logger.warning("Could not prune %s: %s", path, res.stderr)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_last_snapshot(self) -> SnapshotRecord | None:
        if not self._state_path.exists():
            return None
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return SnapshotRecord(path=data["path"], created_at=data["created_at"])
        except Exception as exc:
            logger.warning("Could not read deploy state file %s: %s", self._state_path, exc)
            return None
