"""
navig.deploy.models — Data classes for the NAVIG deploy system.

Keep this import-free from heavy modules so it can be used anywhere.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ============================================================================
# LIFECYCLE PHASES
# ============================================================================


class DeployPhase(str, enum.Enum):
    """Ordered lifecycle phases for a NAVIG deploy run."""

    PRE_CHECK = "pre_check"
    BACKUP = "backup"
    PUSH = "push"
    APPLY = "apply"
    RESTART = "restart"
    HEALTH = "health"
    CLEANUP = "cleanup"


# ============================================================================
# PER-PHASE RESULT
# ============================================================================


@dataclass
class PhaseResult:
    phase: DeployPhase
    success: bool
    message: str = ""
    detail: str = ""
    elapsed: float = 0.0  # seconds
    skipped: bool = False  # phase was intentionally skipped


# ============================================================================
# DEPLOY CONFIG  (loaded from .navig/deploy.yaml)
# ============================================================================


@dataclass
class PushConfig:
    source: str  # Local path (relative to project root)
    target: str  # Remote absolute path
    excludes: list[str] = field(default_factory=list)


@dataclass
class ApplyConfig:
    commands: list[str] = field(default_factory=list)


@dataclass
class RestartConfig:
    adapter: str = "systemd"  # systemd | docker-compose | pm2 | command
    service: str | None = None
    compose_file: str = "docker-compose.yml"
    command: str | None = None  # for adapter=command


@dataclass
class HealthConfig:
    url: str | None = None
    method: str = "GET"
    expected_status: int = 200
    retries: int = 5
    interval_seconds: int = 5
    timeout_seconds: int = 30
    # Optional arbitrary command that runs on remote instead of curl
    command: str | None = None


@dataclass
class BackupConfig:
    enabled: bool = True
    remote_path: str = "/var/backups"  # base dir on server; app name appended
    keep_last: int = 5


@dataclass
class DeployConfig:
    """
    Fully merged deploy configuration for one deploy run.
    Source priority: CLI flags > .navig/deploy.yaml > global defaults.
    """

    version: str = "1"

    push: PushConfig = field(
        default_factory=lambda: PushConfig(source="./dist/", target="/var/www/app/")
    )
    apply: ApplyConfig = field(default_factory=ApplyConfig)
    restart: RestartConfig = field(default_factory=RestartConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)

    # Target resolution (can override active context)
    host: str | None = None
    app: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeployConfig:
        """Parse a deploy.yaml dict into a DeployConfig."""
        raw_push = data.get("push", {})
        raw_apply = data.get("apply", {})
        raw_restart = data.get("restart", {})
        raw_health = data.get("health_check", {})
        raw_backup = data.get("backup", {})

        push = PushConfig(
            source=raw_push.get("source", "./dist/"),
            target=raw_push.get("target", "/var/www/app/"),
            excludes=raw_push.get("excludes", []),
        )

        apply = ApplyConfig(commands=raw_apply.get("commands", []))

        restart = RestartConfig(
            adapter=raw_restart.get("adapter", "systemd"),
            service=raw_restart.get("service"),
            compose_file=raw_restart.get("compose_file", "docker-compose.yml"),
            command=raw_restart.get("command"),
        )

        health = HealthConfig(
            url=raw_health.get("url"),
            method=raw_health.get("method", "GET"),
            expected_status=raw_health.get("expected_status", 200),
            retries=raw_health.get("retries", 5),
            interval_seconds=raw_health.get("interval_seconds", 5),
            timeout_seconds=raw_health.get("timeout_seconds", 30),
            command=raw_health.get("command"),
        )

        backup = BackupConfig(
            enabled=raw_backup.get("enabled", True),
            remote_path=raw_backup.get("remote_path", "/var/backups"),
            keep_last=raw_backup.get("keep_last", 5),
        )

        return cls(
            version=str(data.get("version", "1")),
            push=push,
            apply=apply,
            restart=restart,
            health=health,
            backup=backup,
            host=data.get("host"),
            app=data.get("app"),
        )

    def merge_global_defaults(self, defaults: dict[str, Any]) -> None:
        """Apply global config defaults where project config left defaults."""
        d = defaults.get("deploy", {})
        excludes = d.get("default_push_excludes", [])
        # Merge without duplicates
        combined = list(self.push.excludes)
        for exc in excludes:
            if exc not in combined:
                combined.append(exc)
        self.push.excludes = combined

        if self.health.retries == 5:
            self.health.retries = int(d.get("default_health_retries", 5))
        if self.health.interval_seconds == 5:
            self.health.interval_seconds = int(d.get("default_health_interval_seconds", 5))
        if self.health.timeout_seconds == 30:
            self.health.timeout_seconds = int(d.get("default_health_timeout_seconds", 30))
        if self.backup.keep_last == 5:
            self.backup.keep_last = int(d.get("snapshot_keep_last", 5))


# ============================================================================
# SNAPSHOT RECORD
# ============================================================================


@dataclass
class SnapshotRecord:
    path: str  # Absolute path on remote host
    created_at: str  # ISO timestamp


# ============================================================================
# DEPLOY RESULT
# ============================================================================


@dataclass
class DeployResult:
    """Final result of a deploy run."""

    success: bool
    host: str
    app: str
    started_at: datetime
    finished_at: datetime | None = None
    phases: list[PhaseResult] = field(default_factory=list)
    snapshot: SnapshotRecord | None = None
    rolled_back: bool = False
    dry_run: bool = False
    git_ref: str | None = None
    error: str | None = None

    @property
    def elapsed(self) -> float:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return 0.0

    def phase(self, name: DeployPhase) -> PhaseResult | None:
        for p in self.phases:
            if p.phase == name:
                return p
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "host": self.host,
            "app": self.app,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "elapsed_seconds": round(self.elapsed, 2),
            "phases": [
                {
                    "phase": p.phase.value,
                    "success": p.success,
                    "message": p.message,
                    "elapsed": round(p.elapsed, 2),
                    "skipped": p.skipped,
                }
                for p in self.phases
            ],
            "snapshot": (
                {"path": self.snapshot.path, "created_at": self.snapshot.created_at}
                if self.snapshot
                else None
            ),
            "rolled_back": self.rolled_back,
            "dry_run": self.dry_run,
            "git_ref": self.git_ref,
            "error": self.error,
        }
