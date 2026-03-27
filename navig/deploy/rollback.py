"""
navig.deploy.rollback — Remote snapshot creation and restoration.

Strategy:
  - Pre-push: `cp -r <target> <snapshot_dir>/<timestamp>` on the remote host.
    This is instantaneous (same-disk copy), no bandwidth required.
  - On failure: `rm -rf <target> && mv <snapshot_path> <target>`.
  - Pruning: keep only the N most recent snapshots per app.
  - Local state: ~/.navig/cache/last_deploy_<app>.json tracks the last snapshot.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from navig.deploy.models import BackupConfig, SnapshotRecord

logger = logging.getLogger(__name__)


class RollbackManager:
    """Manages remote snapshots and rollback for a single deploy target."""

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

        # Ensure snapshot base dir exists
        mkdir_cmd = f"mkdir -p {self._snapshot_base}"
        r = self._remote.execute_command(mkdir_cmd, self._server)
        if r.returncode != 0:
            raise RuntimeError(
                f"Could not create snapshot dir {self._snapshot_base}: {r.stderr}"
            )

        # Create snapshot (cp, not rsync — same disk = fast)
        cp_cmd = f"cp -r {self._target} {snap_path}"
        r = self._remote.execute_command(cp_cmd, self._server)
        if r.returncode != 0:
            raise RuntimeError(f"Snapshot failed: {r.stderr or r.stdout}")

        record = SnapshotRecord(path=snap_path, created_at=ts)
        logger.info("Snapshot created: %s", snap_path)
        return record

    def restore_snapshot(
        self, snapshot: SnapshotRecord | None = None
    ) -> tuple[bool, str]:
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

        # Swap: remove current deployment, move snapshot into place
        cmd = f"rm -rf {self._target} && mv {snap_path} {self._target}"
        r = self._remote.execute_command(cmd, self._server)
        if r.returncode != 0:
            return False, f"Restore failed: {r.stderr or r.stdout}"

        return True, f"Restored from {snap_path}"

    def save_state(self, record: SnapshotRecord) -> None:
        """Persist snapshot record locally for future rollback commands."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps({"path": record.path, "created_at": record.created_at}),
            encoding="utf-8",
        )

    def load_state(self) -> SnapshotRecord | None:
        """Load the last known snapshot from local state file."""
        return self._load_last_snapshot()

    def prune_old_snapshots(self) -> None:
        """Remove all but the N most recent snapshots from the remote host."""
        keep = self._bcfg.keep_last
        if keep <= 0 or self._dry_run:
            return

        # List snapshots, sorted by name descending (timestamps sort lexicographically)
        list_cmd = f"ls -dt {self._snapshot_base}/*/ 2>/dev/null | tail -n +{keep + 1}"
        r = self._remote.execute_command(list_cmd, self._server)
        old = [line.strip() for line in (r.stdout or "").splitlines() if line.strip()]
        if not old:
            return

        for path in old:
            rm_cmd = f"rm -rf {path}"
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
            logger.warning(
                "Could not read deploy state file %s: %s", self._state_path, exc
            )
            return None
