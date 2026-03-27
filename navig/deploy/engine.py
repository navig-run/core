"""
navig.deploy.engine — Deploy lifecycle orchestrator.

Executes phases in order: pre_check → backup → push → apply → restart → health → cleanup
Triggers automatic rollback on health check failure (unless --no-auto-rollback).
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navig.deploy.adapters import build_adapter
from navig.deploy.health import HealthChecker
from navig.deploy.history import DeployHistory
from navig.deploy.models import (
    DeployConfig,
    DeployPhase,
    DeployResult,
    PhaseResult,
    SnapshotRecord,
)
from navig.deploy.rollback import RollbackManager

logger = logging.getLogger(__name__)

# Progress callback type: (phase, status, message) → None
# status: "start" | "ok" | "fail" | "skip" | "warn"
ProgressCallback = Callable[[DeployPhase, str, str], None]


class DeployEngine:
    """
    Runs a full deploy lifecycle against a remote host.

    Usage:
        engine = DeployEngine(config, server_config, remote_ops, cache_dir)
        result = engine.run(auto_rollback=True, on_progress=my_callback)
    """

    def __init__(
        self,
        config: DeployConfig,
        server_config: dict[str, Any],
        remote_ops: Any,
        cache_dir: Path,
        project_root: Path = Path("."),
        verbose: bool = False,
    ):
        self._cfg = config
        self._server = server_config
        self._remote = remote_ops
        self._cache_dir = cache_dir
        self._project_root = project_root
        self._verbose = verbose
        self._dry_run = False  # set at run() time

        self._rollback_mgr: RollbackManager | None = None
        self._snapshot: SnapshotRecord | None = None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        dry_run: bool = False,
        skip_backup: bool = False,
        auto_rollback: bool = True,
        on_progress: ProgressCallback | None = None,
    ) -> DeployResult:
        """
        Execute the full deploy lifecycle.

        Returns a DeployResult. Caller is responsible for output rendering.
        """
        self._dry_run = dry_run
        phases: list[PhaseResult] = []
        started_at = datetime.now(tz=timezone.utc)
        host_name = self._server.get("name", self._server.get("host", "unknown"))
        app_name = self._cfg.app or "app"

        def emit(phase: DeployPhase, status: str, msg: str) -> None:
            if on_progress:
                try:
                    on_progress(phase, status, msg)
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

        result = DeployResult(
            success=False,
            host=host_name,
            app=app_name,
            started_at=started_at,
            dry_run=dry_run,
            git_ref=self._get_git_ref(),
        )

        # Build rollback manager early (needed across phases)
        self._rollback_mgr = RollbackManager(
            backup_cfg=self._cfg.backup,
            deploy_target=self._cfg.push.target,
            app_name=app_name,
            server_config=self._server,
            remote_ops=self._remote,
            cache_dir=self._cache_dir,
            dry_run=dry_run,
        )

        # ── Phase 1: Pre-check ──────────────────────────────────────
        pr = self._phase_pre_check(emit, dry_run)
        phases.append(pr)
        if not pr.success:
            result.phases = phases
            result.error = pr.message
            result.finished_at = datetime.now(tz=timezone.utc)
            self._record_history(result)
            return result

        # ── Phase 2: Backup / snapshot ──────────────────────────────
        backup_skipped = skip_backup or not self._cfg.backup.enabled
        pr = self._phase_backup(emit, dry_run, skip=backup_skipped)
        phases.append(pr)
        if not pr.success and not backup_skipped:
            result.phases = phases
            result.error = pr.message
            result.finished_at = datetime.now(tz=timezone.utc)
            self._record_history(result)
            return result

        # ── Phase 3: Push ───────────────────────────────────────────
        pr = self._phase_push(emit, dry_run)
        phases.append(pr)
        if not pr.success:
            result.phases = phases
            result.error = pr.message
            result.finished_at = datetime.now(tz=timezone.utc)
            result = self._maybe_rollback(result, emit) if auto_rollback else result
            self._record_history(result)
            return result

        # ── Phase 4: Apply ──────────────────────────────────────────
        pr = self._phase_apply(emit, dry_run)
        phases.append(pr)
        if not pr.success:
            result.phases = phases
            result.error = pr.message
            result.finished_at = datetime.now(tz=timezone.utc)
            result = self._maybe_rollback(result, emit) if auto_rollback else result
            self._record_history(result)
            return result

        # ── Phase 5: Restart ────────────────────────────────────────
        pr = self._phase_restart(emit, dry_run)
        phases.append(pr)
        if not pr.success:
            result.phases = phases
            result.error = pr.message
            result.finished_at = datetime.now(tz=timezone.utc)
            result = self._maybe_rollback(result, emit) if auto_rollback else result
            self._record_history(result)
            return result

        # ── Phase 6: Health check ───────────────────────────────────
        pr = self._phase_health(emit, dry_run)
        phases.append(pr)
        if not pr.success:
            result.phases = phases
            result.error = pr.message
            result.finished_at = datetime.now(tz=timezone.utc)
            result = self._maybe_rollback(result, emit) if auto_rollback else result
            self._record_history(result)
            return result

        # ── Phase 7: Cleanup ────────────────────────────────────────
        pr = self._phase_cleanup(emit, dry_run)
        phases.append(pr)

        # ── Success ─────────────────────────────────────────────────
        result.success = True
        result.phases = phases
        result.snapshot = self._snapshot
        result.finished_at = datetime.now(tz=timezone.utc)

        # Persist snapshot state for future rollbacks
        if self._snapshot and not dry_run:
            self._rollback_mgr.save_state(self._snapshot)

        self._record_history(result)
        return result

    # ------------------------------------------------------------------
    # Individual phase implementations
    # ------------------------------------------------------------------

    def _phase_pre_check(self, emit: ProgressCallback, dry_run: bool) -> PhaseResult:
        t0 = time.perf_counter()
        phase = DeployPhase.PRE_CHECK
        emit(phase, "start", "")

        checks: list[str] = []
        errors: list[str] = []

        # 1. SSH reachable
        if not dry_run:
            try:
                r = self._remote.execute_command("echo navig-ping", self._server)
                if r.returncode == 0:
                    checks.append("SSH reachable")
                else:
                    errors.append(f"SSH failed: {r.stderr.strip()}")
            except Exception as exc:
                errors.append(f"SSH error: {exc}")
        else:
            checks.append(f"[DRY RUN] SSH connect to {self._server.get('host')}")

        # 2. Remote disk check (warn below 20% free)
        if not dry_run and not errors:
            try:
                import shlex

                target_dir_safe = shlex.quote(self._cfg.push.target)
                r = self._remote.execute_command(
                    f"df -P {target_dir_safe} 2>/dev/null | tail -1 | awk '{{print $5}}'",
                    self._server,
                )
                if r.returncode == 0:
                    pct_str = (r.stdout or "").strip().replace("%", "")
                    used_pct = int(pct_str) if pct_str.isdigit() else 0
                    free_pct = 100 - used_pct
                    if free_pct < 20:
                        errors.append(f"Low disk: {free_pct}% free on target partition")
                    else:
                        checks.append(f"disk {free_pct}% free")
            except Exception:
                checks.append("disk (check skipped)")

        # 3. Local source exists
        source = self._project_root / self._cfg.push.source.lstrip("./")
        if not source.exists() and not dry_run:
            errors.append(f"Local source not found: {self._cfg.push.source}")

        elapsed = time.perf_counter() - t0

        if errors:
            msg = " · ".join(errors)
            emit(phase, "fail", msg)
            return PhaseResult(phase=phase, success=False, message=msg, elapsed=elapsed)

        msg = " · ".join(checks)
        emit(phase, "ok", msg)
        return PhaseResult(phase=phase, success=True, message=msg, elapsed=elapsed)

    def _phase_backup(
        self, emit: ProgressCallback, dry_run: bool, skip: bool = False
    ) -> PhaseResult:
        t0 = time.perf_counter()
        phase = DeployPhase.BACKUP
        if skip:
            emit(phase, "skip", "backup disabled")
            return PhaseResult(
                phase=phase, success=True, message="skipped", skipped=True, elapsed=0.0
            )

        emit(phase, "start", "")
        try:
            record = self._rollback_mgr.create_snapshot()
            if record:
                self._snapshot = record
                msg = f"Snapshot → {record.path}"
            else:
                msg = "Snapshot skipped (backup.enabled=false)"
            elapsed = time.perf_counter() - t0
            emit(phase, "ok", msg)
            return PhaseResult(phase=phase, success=True, message=msg, elapsed=elapsed)
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            msg = str(exc)
            emit(phase, "fail", msg)
            return PhaseResult(phase=phase, success=False, message=msg, elapsed=elapsed)

    def _phase_push(self, emit: ProgressCallback, dry_run: bool) -> PhaseResult:
        t0 = time.perf_counter()
        phase = DeployPhase.PUSH
        emit(phase, "start", "")

        source = str(self._project_root / self._cfg.push.source.lstrip("./"))
        target = self._cfg.push.target
        excludes = self._cfg.push.excludes

        if dry_run:
            msg = f"[DRY RUN] rsync {source} → {self._server.get('host')}:{target}"
            elapsed = time.perf_counter() - t0
            emit(phase, "ok", msg)
            return PhaseResult(phase=phase, success=True, message=msg, elapsed=elapsed)

        try:
            cmd = self._build_rsync_cmd(source, target, excludes)
            logger.debug("rsync: %s", " ".join(cmd))
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            elapsed = time.perf_counter() - t0
            if r.returncode == 0:
                # Parse rsync summary line: "sent X bytes  received Y bytes"
                summary = self._parse_rsync_summary(r.stdout)
                emit(phase, "ok", summary)
                return PhaseResult(phase=phase, success=True, message=summary, elapsed=elapsed)
            else:
                msg = (r.stderr or r.stdout).strip()[:300]
                emit(phase, "fail", msg)
                return PhaseResult(phase=phase, success=False, message=msg, elapsed=elapsed)
        except subprocess.TimeoutExpired:
            elapsed = time.perf_counter() - t0
            msg = "rsync timed out after 300s"
            emit(phase, "fail", msg)
            return PhaseResult(phase=phase, success=False, message=msg, elapsed=elapsed)
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            msg = str(exc)
            emit(phase, "fail", msg)
            return PhaseResult(phase=phase, success=False, message=msg, elapsed=elapsed)

    def _phase_apply(self, emit: ProgressCallback, dry_run: bool) -> PhaseResult:
        t0 = time.perf_counter()
        phase = DeployPhase.APPLY
        cmds = self._cfg.apply.commands

        if not cmds:
            emit(phase, "skip", "no apply commands")
            return PhaseResult(
                phase=phase, success=True, message="skipped", skipped=True, elapsed=0.0
            )

        emit(phase, "start", "")

        for cmd in cmds:
            if dry_run:
                logger.info("[DRY RUN] apply: %s", cmd)
                continue

            # Run each apply command in the deploy target directory
            import shlex

            target_dir_safe = shlex.quote(self._cfg.push.target)
            full_cmd = f"cd {target_dir_safe} && {cmd}"
            r = self._remote.execute_command(full_cmd, self._server)
            if r.returncode != 0:
                elapsed = time.perf_counter() - t0
                msg = f"`{cmd}` failed: {(r.stderr or r.stdout).strip()[:200]}"
                emit(phase, "fail", msg)
                return PhaseResult(phase=phase, success=False, message=msg, elapsed=elapsed)

        elapsed = time.perf_counter() - t0
        msg = (
            f"{len(cmds)} command(s) applied"
            if not dry_run
            else f"[DRY RUN] {len(cmds)} command(s)"
        )
        emit(phase, "ok", msg)
        return PhaseResult(phase=phase, success=True, message=msg, elapsed=elapsed)

    def _phase_restart(self, emit: ProgressCallback, dry_run: bool) -> PhaseResult:
        t0 = time.perf_counter()
        phase = DeployPhase.RESTART
        emit(phase, "start", "")

        # Annotate server config with deploy target root (for docker-compose adapter)
        enriched_server = {**self._server, "_deploy_target_root": self._cfg.push.target}

        try:
            adapter = build_adapter(self._cfg.restart, enriched_server, self._remote, dry_run)
        except ValueError as exc:
            elapsed = time.perf_counter() - t0
            msg = str(exc)
            emit(phase, "fail", msg)
            return PhaseResult(phase=phase, success=False, message=msg, elapsed=elapsed)

        ok, detail = adapter.restart()
        elapsed = time.perf_counter() - t0

        if ok:
            # Wait for service to settle before health check
            settle = 2  # default
            if not dry_run:
                time.sleep(settle)
            emit(phase, "ok", detail)
            return PhaseResult(phase=phase, success=True, message=detail, elapsed=elapsed)
        else:
            emit(phase, "fail", detail)
            return PhaseResult(phase=phase, success=False, message=detail, elapsed=elapsed)

    def _phase_health(self, emit: ProgressCallback, dry_run: bool) -> PhaseResult:
        t0 = time.perf_counter()
        phase = DeployPhase.HEALTH
        emit(phase, "start", "")

        checker = HealthChecker(
            config=self._cfg.health,
            server_config=self._server,
            remote_ops=self._remote,
            dry_run=dry_run,
        )

        ok, msg = checker.check()
        elapsed = time.perf_counter() - t0

        if ok:
            emit(phase, "ok", msg)
            return PhaseResult(phase=phase, success=True, message=msg, elapsed=elapsed)
        else:
            emit(phase, "fail", msg)
            return PhaseResult(phase=phase, success=False, message=msg, elapsed=elapsed)

    def _phase_cleanup(self, emit: ProgressCallback, dry_run: bool) -> PhaseResult:
        t0 = time.perf_counter()
        phase = DeployPhase.CLEANUP
        emit(phase, "start", "")

        if not dry_run and self._rollback_mgr:
            try:
                self._rollback_mgr.prune_old_snapshots()
            except Exception as exc:
                logger.warning("Snapshot pruning failed (non-fatal): %s", exc)

        elapsed = time.perf_counter() - t0
        emit(phase, "ok", "done")
        return PhaseResult(phase=phase, success=True, message="done", elapsed=elapsed)

    # ------------------------------------------------------------------
    # Rollback helper
    # ------------------------------------------------------------------

    def _maybe_rollback(self, result: DeployResult, emit: ProgressCallback) -> DeployResult:
        """Execute automatic rollback and update result accordingly."""
        if not self._rollback_mgr:
            return result

        snap = self._snapshot or self._rollback_mgr.load_state()
        if not snap:
            logger.warning("No snapshot available for rollback.")
            return result

        emit(DeployPhase.BACKUP, "start", "Rolling back…")
        ok, msg = self._rollback_mgr.restore_snapshot(snap)
        if not ok:
            logger.error("Rollback restore failed: %s", msg)
            return result

        # Restart after restore
        enriched = {**self._server, "_deploy_target_root": self._cfg.push.target}
        try:
            adapter = build_adapter(self._cfg.restart, enriched, self._remote, self._dry_run)
            adapter.restart()
        except Exception as exc:
            logger.warning("Restart after rollback failed: %s", exc)

        # Post-rollback health check
        checker = HealthChecker(self._cfg.health, self._server, self._remote, self._dry_run)
        checker.check()

        result.rolled_back = True
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_rsync_cmd(self, source: str, target: str, excludes: list[str]) -> list[str]:
        server = self._server
        host = f"{server['user']}@{server['host']}"
        port = server.get("port", 22)
        key = server.get("ssh_key", "")

        ssh_opts = ["-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10"]
        if port != 22:
            ssh_opts += ["-p", str(port)]
        if key:
            ssh_opts += ["-i", key]
        ssh_cmd = "ssh " + " ".join(ssh_opts)

        cmd = [
            "rsync",
            "-az",  # archive + compress
            "--delete",  # remove files deleted locally
            "-e",
            ssh_cmd,
        ]
        for exc in excludes:
            cmd += ["--exclude", exc]

        # Ensure trailing slash on source so rsync syncs contents, not dir name
        if not source.endswith("/"):
            source += "/"

        cmd += [source, f"{host}:{target}"]
        return cmd

    @staticmethod
    def _parse_rsync_summary(stdout: str) -> str:
        """Extract the concise transfer summary from rsync output."""
        for line in (stdout or "").splitlines():
            line = line.strip()
            if "sent" in line and "received" in line:
                return line
        return "synced"

    @staticmethod
    def _get_git_ref() -> str | None:
        """Try to get current HEAD git short hash."""
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    def _record_history(self, result: DeployResult) -> None:
        try:
            keep = 50
            try:
                from navig.config import get_config_manager

                cm = get_config_manager()
                keep = int(cm._load_global_config().get("deploy", {}).get("history_keep", 50))
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
            history = DeployHistory(cache_dir=self._cache_dir, keep=keep)
            history.append(result.to_dict())
        except Exception as exc:
            logger.debug("Could not write deploy history: %s", exc)
