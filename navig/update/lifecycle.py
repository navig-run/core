"""UpdateEngine — the core lifecycle for navig update.

Lifecycle per node:
  discover → compare → (skip if up-to-date) → backup_version →
  install → verify → commit  |  (on failure) → rollback → record
"""
from __future__ import annotations

import subprocess
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from navig.update.models import NodeResult, UpdatePlan, UpdateResult, VersionInfo
from navig.update.targets import UpdateTarget
from navig.update.checker import VersionChecker
from navig.update.sources import _BaseSource
from navig.update.history import UpdateHistory

# ProgressCallback(node_id, step, status, message)
ProgressCallback = Callable[[str, str, str, str], None]


class UpdateEngine:
    """Orchestrates update operations across one or more targets."""

    def __init__(
        self,
        targets: List[UpdateTarget],
        source: _BaseSource,
        remote_ops: Any = None,
        cache_dir: Optional[str] = None,
        config: Optional[Dict] = None,
    ):
        self._targets = targets
        self._source = source
        self._config = config or {}
        self._cache_dir = cache_dir
        self._history = UpdateHistory(cache_dir=cache_dir)

        if remote_ops is None:
            try:
                from navig.remote import RemoteOperations
                remote_ops = RemoteOperations()
            except Exception:
                remote_ops = None
        self._remote_ops = remote_ops

        # Shared latest-version cache so we only hit the source once
        self._version_cache: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Phase 1: plan
    # ------------------------------------------------------------------

    def plan(self, force: bool = False) -> UpdatePlan:
        """Check all targets and produce an UpdatePlan."""
        checker = VersionChecker(self._source, self._remote_ops, self._version_cache)
        version_infos: Dict[str, VersionInfo] = {}
        to_update: List[UpdateTarget] = []
        up_to_date: List[UpdateTarget] = []
        unreachable: List[UpdateTarget] = []

        for t in self._targets:
            if t.is_local:
                vi = checker.check_local()
            else:
                vi = checker.check_ssh(t.node_id, t.server_config or {})
            version_infos[t.node_id] = vi

            if not vi.reachable:
                unreachable.append(t)
            elif force or vi.needs_update:
                to_update.append(t)
            else:
                up_to_date.append(t)

        return UpdatePlan(
            targets=self._targets,
            version_infos=version_infos,
            to_update=to_update,
            up_to_date=up_to_date,
            unreachable=unreachable,
        )

    # ------------------------------------------------------------------
    # Phase 2: run
    # ------------------------------------------------------------------

    def run(
        self,
        dry_run: bool = False,
        force: bool = False,
        skip_backup: bool = False,
        auto_rollback: bool = True,
        channel: str = "stable",
        on_progress: Optional[ProgressCallback] = None,
    ) -> UpdateResult:
        """Execute updates. Returns an UpdateResult."""
        t_global = time.monotonic()
        p = self.plan(force=force)

        node_results: List[NodeResult] = []

        # Nodes that are unreachable
        for t in p.unreachable:
            vi = p.version_infos.get(t.node_id)
            nr = NodeResult(
                node_id=t.node_id,
                ok=False,
                old_version=vi.current if vi else "unknown",
                skipped=True,
                skip_reason="unreachable",
                error=vi.error if vi else "unreachable",
            )
            node_results.append(nr)

        # Nodes already up-to-date
        for t in p.up_to_date:
            vi = p.version_infos.get(t.node_id)
            nr = NodeResult(
                node_id=t.node_id,
                ok=True,
                old_version=vi.current if vi else "unknown",
                new_version=vi.current if vi else None,
                skipped=True,
                skip_reason="already up-to-date",
            )
            node_results.append(nr)
            self._emit(on_progress, t.node_id, "check", "skip", "already up-to-date")

        # Nodes that need updating
        for t in p.to_update:
            if dry_run:
                vi = p.version_infos.get(t.node_id)
                nr = NodeResult(
                    node_id=t.node_id,
                    ok=True,
                    old_version=vi.current if vi else "unknown",
                    new_version=vi.latest if vi else None,
                    skipped=True,
                    skip_reason="dry-run",
                )
                node_results.append(nr)
                continue

            nr = self._run_one(
                t, p.version_infos.get(t.node_id),
                skip_backup=skip_backup,
                auto_rollback=auto_rollback,
                channel=channel,
                on_progress=on_progress,
            )
            node_results.append(nr)
            self._record_history(nr, channel)

        return UpdateResult(
            plan=p,
            node_results=node_results,
            dry_run=dry_run,
            total_elapsed_seconds=time.monotonic() - t_global,
        )

    # ------------------------------------------------------------------
    # Per-node lifecycle
    # ------------------------------------------------------------------

    def _run_one(
        self,
        target: UpdateTarget,
        vi: Optional[VersionInfo],
        skip_backup: bool,
        auto_rollback: bool,
        channel: str,
        on_progress: Optional[ProgressCallback],
    ) -> NodeResult:
        t0 = time.monotonic()
        old_version = vi.current if vi else "unknown"
        nr = NodeResult(node_id=target.node_id, old_version=old_version)

        def _step(step: str, fn: Callable) -> bool:
            self._emit(on_progress, target.node_id, step, "running", "")
            try:
                fn()
                nr.steps.append(f"{step}:ok")
                self._emit(on_progress, target.node_id, step, "ok", "")
                return True
            except Exception as exc:
                nr.steps.append(f"{step}:fail:{str(exc)[:80]}")
                nr.ok = False
                nr.error = str(exc)[:200]
                self._emit(on_progress, target.node_id, step, "fail", str(exc)[:80])
                return False

        # Install
        if target.is_local:
            ok = _step("install", lambda: self._install_local(channel))
        else:
            ok = _step("install", lambda: self._install_ssh(target, channel))

        if not ok:
            if auto_rollback and old_version not in ("unknown", ""):
                _step("rollback", lambda: self._rollback_node(target, old_version))
                nr.rolled_back = True
            nr.elapsed_seconds = time.monotonic() - t0
            return nr

        # Verify
        ok = _step("verify", lambda: self._verify_version(target, nr))

        if not ok and auto_rollback and old_version not in ("unknown", ""):
            _step("rollback", lambda: self._rollback_node(target, old_version))
            nr.rolled_back = True

        nr.elapsed_seconds = time.monotonic() - t0
        return nr

    # ------------------------------------------------------------------
    # Install helpers
    # ------------------------------------------------------------------

    def _install_local(self, channel: str) -> None:
        from pathlib import Path
        from navig.commands.update import _step_git, _step_pypi

        src_dir = Path(__file__).resolve().parent.parent.parent
        is_git = (src_dir / ".git").exists()

        if is_git:
            result = _step_git(src_dir, force=True)
        else:
            result = _step_pypi(force=False)

        if not result.ok:
            raise RuntimeError(result.note or "Install step failed")

    def _install_ssh(self, target: UpdateTarget, channel: str) -> None:
        if self._remote_ops is None:
            raise RuntimeError("No remote_ops available for SSH install")

        # Prefer delegating to navig on the remote node (self-contained update)
        cmd = "navig update run --force --no-rollback 2>&1 || pip install --upgrade navig 2>&1"
        r = self._remote_ops.execute_command(cmd, server_config=target.server_config or {})

        rc = getattr(r, "returncode", 0) if hasattr(r, "returncode") else 0
        stderr = getattr(r, "stderr", "") or ""
        if rc != 0 and "error" in stderr.lower():
            raise RuntimeError(f"Remote install failed (rc={rc}): {stderr[:120]}")

    # ------------------------------------------------------------------
    # Verify
    # ------------------------------------------------------------------

    def _verify_version(self, target: UpdateTarget, nr: NodeResult) -> None:
        if target.is_local:
            from navig.commands.update import _reload_version
            new_v = _reload_version()
        else:
            if self._remote_ops is None:
                raise RuntimeError("No remote_ops for verify")
            import json as _json, re as _re
            r = self._remote_ops.execute_command(
                "navig version --json 2>/dev/null || navig --version 2>/dev/null",
                server_config=target.server_config or {},
            )
            output = (getattr(r, "stdout", "") or "").strip()
            try:
                data = _json.loads(output)
                new_v = data.get("version", "?")
            except Exception:
                m = _re.search(r"\d+\.\d+\.\d+", output)
                new_v = m.group(0) if m else "?"
        nr.new_version = new_v

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def _rollback_node(self, target: UpdateTarget, old_version: str) -> None:
        uv = shutil.which("uv")
        if target.is_local:
            if uv:
                cmd = [uv, "pip", "install", "--python", sys.executable,
                       f"navig=={old_version}"]
            else:
                cmd = [sys.executable, "-m", "pip", "install",
                       f"navig=={old_version}", "-q"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"Rollback failed: {r.stderr[:80]}")
        else:
            if self._remote_ops is None:
                raise RuntimeError("No remote_ops for rollback")
            pkg = f"navig=={old_version}"
            cmd = f"pip install '{pkg}' -q 2>&1 || uv pip install '{pkg}' 2>&1"
            self._remote_ops.execute_command(cmd, server_config=target.server_config or {})

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _record_history(self, nr: NodeResult, channel: str) -> None:
        import datetime
        record = {
            "node_id": nr.node_id,
            "old_version": nr.old_version,
            "new_version": nr.new_version,
            "channel": channel,
            "ok": nr.ok,
            "elapsed_seconds": round(nr.elapsed_seconds, 2),
            "rolled_back": nr.rolled_back,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "error": nr.error,
        }
        try:
            self._history.append(record)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _emit(
        cb: Optional[ProgressCallback],
        node_id: str,
        step: str,
        status: str,
        message: str,
    ) -> None:
        if cb:
            try:
                cb(node_id, step, status, message)
            except Exception:
                pass
