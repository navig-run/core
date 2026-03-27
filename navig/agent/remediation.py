"""
Remediation System - Auto-Healing for Agent Components

Handles automatic remediation of common failures:
- Component restart with exponential backoff
- Connection failure recovery
- Configuration rollback
- Permission issue detection
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from navig.debug_logger import DebugLogger


class RemediationType(Enum):
    """Types of remediation actions."""

    COMPONENT_RESTART = "component_restart"
    CONNECTION_RETRY = "connection_retry"
    CONFIG_ROLLBACK = "config_rollback"
    PERMISSION_FIX = "permission_fix"
    SERVICE_RESTART = "service_restart"


class RemediationStatus(Enum):
    """Status of remediation attempt."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class RemediationAction:
    """A remediation action to be performed."""

    id: str
    type: RemediationType
    component: str
    reason: str
    timestamp: datetime = field(default_factory=datetime.now)
    status: RemediationStatus = RemediationStatus.PENDING
    attempts: int = 0
    max_attempts: int = 5
    backoff_seconds: list[int] = field(default_factory=lambda: [1, 2, 4, 8, 16, 60])
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "component": self.component,
            "reason": self.reason,
            "timestamp": self.timestamp.isoformat(),
            "status": self.status.value,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RemediationAction:
        """Create a RemediationAction from serialized data."""
        return cls(
            id=data["id"],
            type=RemediationType(data["type"]),
            component=data["component"],
            reason=data["reason"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            status=RemediationStatus(
                data.get("status", RemediationStatus.PENDING.value)
            ),
            attempts=int(data.get("attempts", 0)),
            max_attempts=int(data.get("max_attempts", 5)),
            error=data.get("error"),
            metadata=data.get("metadata", {}),
        )


class RemediationEngine:
    """
    Automatic remediation engine for agent component failures.

    Monitors component health and automatically attempts recovery
    when failures are detected.
    """

    def __init__(
        self,
        config_dir: Path | None = None,
        log_dir: Path | None = None,
    ):
        self.config_dir = config_dir or Path.home() / ".navig" / "workspace"
        self.log_dir = log_dir or Path.home() / ".navig" / "logs"
        self.backup_dir = self.config_dir / "config-backup"
        self.actions_file = self.config_dir / "remediation_actions.json"
        self.remediation_log = self.log_dir / "remediation.log"

        # Ensure directories exist
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        self.logger = DebugLogger()
        self._actions: dict[str, RemediationAction] = {}
        self._load_actions()
        self._running = False
        self._task: asyncio.Task | None = None
        self._heart = None  # Will be set by Heart after initialization

    async def start(self) -> None:
        """Start the remediation engine."""
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        self._log("Remediation engine started")

    async def stop(self) -> None:
        """Stop the remediation engine."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown
        self._save_actions()
        self._log("Remediation engine stopped")

    def schedule_restart_sync(
        self, component: str, reason: str, metadata: dict[str, Any] | None = None
    ) -> str:
        """Schedule a component restart (synchronous API)."""
        action_id = f"{component}_{datetime.now().timestamp()}"
        action = RemediationAction(
            id=action_id,
            type=RemediationType.COMPONENT_RESTART,
            component=component,
            reason=reason,
            metadata=metadata or {},
        )
        self._actions[action_id] = action
        self._save_actions()
        self._log(f"Scheduled restart for {component}: {reason}")
        return action_id

    async def schedule_restart(
        self, component: str, reason: str, metadata: dict[str, Any] | None = None
    ) -> str:
        """Schedule a component restart (async API)."""
        return self.schedule_restart_sync(
            component=component, reason=reason, metadata=metadata
        )

    def schedule_connection_retry_sync(
        self,
        component: str,
        service: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Schedule connection retry (synchronous API)."""
        action_id = f"{component}_conn_{datetime.now().timestamp()}"
        action = RemediationAction(
            id=action_id,
            type=RemediationType.CONNECTION_RETRY,
            component=component,
            reason=reason,
            metadata={"service": service, **(metadata or {})},
        )
        self._actions[action_id] = action
        self._save_actions()
        self._log(f"Scheduled connection retry for {component}/{service}: {reason}")
        return action_id

    async def schedule_connection_retry(
        self,
        component: str,
        service: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Schedule connection retry (async API)."""
        return self.schedule_connection_retry_sync(
            component=component,
            service=service,
            reason=reason,
            metadata=metadata,
        )

    def _load_actions(self) -> None:
        """Load persisted remediation actions from disk."""
        if not self.actions_file.exists():
            return

        try:
            raw = json.loads(self.actions_file.read_text(encoding="utf-8"))
            actions = raw.get("actions", [])
            for item in actions:
                action = RemediationAction.from_dict(item)
                self._actions[action.id] = action
        except Exception as e:
            self._log(f"Failed to load remediation actions: {e}", level="warning")

    def _save_actions(self) -> None:
        """Persist remediation actions to disk for later inspection."""
        try:
            payload = {
                "updated_at": datetime.now().isoformat(),
                "actions": [a.to_dict() for a in self._actions.values()],
            }
            self.actions_file.write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            self._log(f"Failed to save remediation actions: {e}", level="warning")

    async def rollback_config(self, component: str, reason: str) -> bool:
        """
        Rollback configuration to last known good state.

        Returns True if rollback succeeded, False otherwise.
        """
        try:
            # Find most recent backup
            backups = sorted(
                self.backup_dir.glob(f"{component}-config-*.yaml"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

            if not backups:
                self._log(
                    f"No backup found for {component}, cannot rollback", level="warning"
                )
                return False

            latest_backup = backups[0]
            current_config = self.config_dir / "config.yaml"

            # Create backup of current (failed) config
            failed_backup = (
                self.backup_dir
                / f"{component}-config-failed-{datetime.now().strftime('%Y%m%d-%H%M%S')}.yaml"
            )
            if current_config.exists():
                shutil.copy2(current_config, failed_backup)

            # Restore from backup
            shutil.copy2(latest_backup, current_config)

            self._log(f"Rolled back {component} config to {latest_backup.name}")
            return True

        except Exception as e:
            self._log(f"Config rollback failed for {component}: {e}", level="error")
            return False

    async def _process_loop(self) -> None:
        """Main processing loop for remediation actions."""
        while self._running:
            try:
                # Process pending actions
                removed_any = False
                for action_id, action in list(self._actions.items()):
                    if action.status == RemediationStatus.PENDING:
                        await self._execute_action(action)

                    # Clean up completed/failed actions after 1 hour
                    if action.status in (
                        RemediationStatus.SUCCESS,
                        RemediationStatus.FAILED,
                        RemediationStatus.SKIPPED,
                    ):
                        if (datetime.now() - action.timestamp) > timedelta(hours=1):
                            del self._actions[action_id]
                            removed_any = True

                if removed_any:
                    self._save_actions()

                await asyncio.sleep(5)  # Check every 5 seconds

            except Exception as e:
                self._log(f"Error in remediation loop: {e}", level="error")
                await asyncio.sleep(10)

    async def _execute_action(self, action: RemediationAction) -> None:
        """Execute a remediation action."""
        if action.attempts >= action.max_attempts:
            action.status = RemediationStatus.FAILED
            action.error = f"Max attempts ({action.max_attempts}) exceeded"
            self._save_actions()
            self._log(
                f"Remediation failed for {action.component}: {action.error}",
                level="error",
            )
            return

        action.status = RemediationStatus.IN_PROGRESS
        action.attempts += 1
        self._save_actions()

        # Calculate backoff
        backoff = action.backoff_seconds[
            min(action.attempts - 1, len(action.backoff_seconds) - 1)
        ]

        self._log(
            f"Executing {action.type.value} for {action.component} (attempt {action.attempts}/{action.max_attempts})"
        )

        try:
            if action.type == RemediationType.COMPONENT_RESTART:
                success = await self._restart_component(action)
            elif action.type == RemediationType.CONNECTION_RETRY:
                success = await self._retry_connection(action)
            elif action.type == RemediationType.CONFIG_ROLLBACK:
                success = await self.rollback_config(action.component, action.reason)
            else:
                self._log(f"Unknown remediation type: {action.type}", level="warning")
                action.status = RemediationStatus.SKIPPED
                self._save_actions()
                return

            if success:
                action.status = RemediationStatus.SUCCESS
                self._save_actions()
                self._log(f"Remediation successful for {action.component}")
            else:
                action.status = RemediationStatus.PENDING  # Retry
                self._save_actions()
                self._log(
                    f"Remediation attempt {action.attempts} failed, will retry after {backoff}s"
                )
                await asyncio.sleep(backoff)

        except Exception as e:
            action.error = str(e)
            action.status = RemediationStatus.PENDING  # Retry
            self._save_actions()
            self._log(f"Remediation error for {action.component}: {e}", level="error")
            await asyncio.sleep(backoff)

    async def _restart_component(self, action: RemediationAction) -> bool:
        """
        Restart a component.

        Returns True if restart succeeded, False otherwise.
        """
        try:
            # Get the component from Heart registry
            if not hasattr(self, "_heart") or not self._heart:
                self._log(
                    f"Cannot restart {action.component}: Heart not set", level="warning"
                )
                return False

            component = self._heart._components.get(action.component)
            if not component:
                self._log(f"Component {action.component} not found", level="error")
                return False

            # Attempt restart
            self._log(f"Restarting component {action.component}...")
            await component.restart()

            # Verify component is running
            if component.is_running:
                self._log(f"Component {action.component} restart successful")
                return True
            else:
                self._log(
                    f"Component {action.component} is not running after restart",
                    level="warning",
                )
                return False

        except Exception as e:
            self._log(f"Failed to restart {action.component}: {e}", level="error")
            return False

    async def _retry_connection(self, action: RemediationAction) -> bool:
        """
        Retry a connection.

        Note: This is a placeholder - actual connection retry logic
        should be implemented by the specific component.
        """
        service = action.metadata.get("service", "unknown")
        self._log(f"Connection retry requested for {action.component}/{service}")
        return True

    def get_action_status(self, action_id: str) -> dict[str, Any] | None:
        """Get status of a remediation action."""
        action = self._actions.get(action_id)
        return action.to_dict() if action else None

    def get_all_actions(self) -> list[dict[str, Any]]:
        """Get all remediation actions."""
        return [action.to_dict() for action in self._actions.values()]

    def retry_action(self, action_id: str, reset_attempts: bool = True) -> bool:
        """
        Requeue a remediation action.

        Args:
            action_id: Action ID to retry
            reset_attempts: Reset attempt counter before retrying

        Returns:
            True when action exists and was re-queued.
        """
        action = self._actions.get(action_id)
        if not action:
            return False

        if reset_attempts:
            action.attempts = 0

        action.status = RemediationStatus.PENDING
        action.error = None
        action.timestamp = datetime.now()
        self._save_actions()
        self._log(
            f"Requeued remediation action {action_id} "
            f"(reset_attempts={reset_attempts})"
        )
        return True

    def _log(self, message: str, level: str = "info") -> None:
        """Log remediation activity."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] [{level.upper()}] {message}\n"

        # Append to remediation log
        try:
            with open(self.remediation_log, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception:
            pass  # Fail silently if logging fails

        # Also log to debug logger
        self.logger.log_operation("remediation", {"message": message, "level": level})
