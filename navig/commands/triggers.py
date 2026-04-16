"""
Event-Driven Automation (Triggers) for NAVIG

Triggers allow NAVIG to react automatically to system events:
- Health check failures -> Auto-remediation workflows
- Disk space warnings -> Cleanup scripts
- Service restarts -> Notifications
- Scheduled events -> Automated workflows
- Custom events -> User-defined actions

Trigger Types:
- health: Triggered when heartbeat detects issues
- schedule: Time-based triggers (cron-like)
- threshold: Resource thresholds (CPU, memory, disk)
- webhook: Incoming HTTP webhooks
- file: File system changes
- command: After specific commands complete
"""

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import yaml

from navig import console_helper as ch

# ============================================================================
# ENUMS AND DATA CLASSES
# ============================================================================


class TriggerType(str, Enum):
    """Types of events that can trigger actions."""

    HEALTH = "health"  # Heartbeat failure/recovery
    SCHEDULE = "schedule"  # Time-based (cron-like)
    THRESHOLD = "threshold"  # Resource thresholds
    WEBHOOK = "webhook"  # Incoming webhooks
    FILE = "file"  # File changes
    COMMAND = "command"  # After command execution
    MANUAL = "manual"  # Manual trigger only
    CALENDAR = "calendar"  # Calendar events
    EMAIL = "email"  # Incoming email


class TriggerStatus(str, Enum):
    """Status of a trigger."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    FIRING = "firing"  # Currently executing
    COOLDOWN = "cooldown"  # In cooldown period


class ActionType(str, Enum):
    """Types of actions a trigger can execute."""

    COMMAND = "command"  # Run navig command
    WORKFLOW = "workflow"  # Run workflow
    NOTIFY = "notify"  # Send notification
    WEBHOOK = "webhook"  # Call external webhook
    SCRIPT = "script"  # Run script file


@dataclass
class TriggerCondition:
    """Condition that must be met for trigger to fire."""

    type: str  # check_type: health_status, resource, time, etc.
    operator: str  # eq, ne, gt, lt, gte, lte, contains, matches
    value: Any  # Expected value
    target: str = ""  # Target to check (host, service, metric name)

    def evaluate(self, actual_value: Any) -> bool:
        """Evaluate if condition is met."""
        try:
            if self.operator == "eq":
                return actual_value == self.value
            elif self.operator == "ne":
                return actual_value != self.value
            elif self.operator == "gt":
                return float(actual_value) > float(self.value)
            elif self.operator == "lt":
                return float(actual_value) < float(self.value)
            elif self.operator == "gte":
                return float(actual_value) >= float(self.value)
            elif self.operator == "lte":
                return float(actual_value) <= float(self.value)
            elif self.operator == "contains":
                return str(self.value) in str(actual_value)
            elif self.operator == "matches":
                return bool(re.match(str(self.value), str(actual_value)))
            else:
                return False
        except (ValueError, TypeError):
            return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "operator": self.operator,
            "value": self.value,
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TriggerCondition":
        return cls(
            type=data.get("type", ""),
            operator=data.get("operator", "eq"),
            value=data.get("value"),
            target=data.get("target", ""),
        )


@dataclass
class TriggerAction:
    """Action to execute when trigger fires."""

    type: ActionType
    target: str  # Command, workflow name, webhook URL, etc.
    params: dict[str, Any] = field(default_factory=dict)
    on_failure: str = "continue"  # continue, stop, retry
    retries: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "target": self.target,
            "params": self.params,
            "on_failure": self.on_failure,
            "retries": self.retries,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TriggerAction":
        return cls(
            type=ActionType(data.get("type", "command")),
            target=data.get("target", ""),
            params=data.get("params", {}),
            on_failure=data.get("on_failure", "continue"),
            retries=data.get("retries", 0),
        )


@dataclass
class Trigger:
    """Complete trigger definition."""

    id: str
    name: str
    type: TriggerType
    description: str = ""
    conditions: list[TriggerCondition] = field(default_factory=list)
    actions: list[TriggerAction] = field(default_factory=list)
    status: TriggerStatus = TriggerStatus.ENABLED
    cooldown_seconds: int = 60  # Min time between firings
    max_fires_per_hour: int = 10
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    last_fired: str = ""
    fire_count: int = 0

    # Schedule-specific (for SCHEDULE type)
    schedule: str = ""  # Cron expression or interval

    # Threshold-specific (for THRESHOLD type)
    host: str = ""  # Target host
    metric: str = ""  # Metric to monitor

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.id:
            self.id = self._generate_id()

    def _generate_id(self) -> str:
        """Generate unique trigger ID from name."""
        base = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")
        hash_suffix = hashlib.md5(self.name.encode()).hexdigest()[:6]
        return f"{base}-{hash_suffix}"

    def can_fire(self) -> bool:
        """Check if trigger can fire (not in cooldown, within rate limit)."""
        if self.status == TriggerStatus.DISABLED:
            return False
        if self.status == TriggerStatus.COOLDOWN:
            return False
        if self.last_fired:
            try:
                last = datetime.fromisoformat(self.last_fired)
                if datetime.now() - last < timedelta(seconds=self.cooldown_seconds):
                    return False
            except ValueError:
                pass  # malformed value; skip
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "description": self.description,
            "conditions": [c.to_dict() for c in self.conditions],
            "actions": [a.to_dict() for a in self.actions],
            "status": self.status.value,
            "cooldown_seconds": self.cooldown_seconds,
            "max_fires_per_hour": self.max_fires_per_hour,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_fired": self.last_fired,
            "fire_count": self.fire_count,
            "schedule": self.schedule,
            "host": self.host,
            "metric": self.metric,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trigger":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", "Unnamed"),
            type=TriggerType(data.get("type", "manual")),
            description=data.get("description", ""),
            conditions=[TriggerCondition.from_dict(c) for c in data.get("conditions", [])],
            actions=[TriggerAction.from_dict(a) for a in data.get("actions", [])],
            status=TriggerStatus(data.get("status", "enabled")),
            cooldown_seconds=data.get("cooldown_seconds", 60),
            max_fires_per_hour=data.get("max_fires_per_hour", 10),
            tags=data.get("tags", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            last_fired=data.get("last_fired", ""),
            fire_count=data.get("fire_count", 0),
            schedule=data.get("schedule", ""),
            host=data.get("host", ""),
            metric=data.get("metric", ""),
        )


@dataclass
class TriggerEvent:
    """Event that may trigger actions."""

    type: TriggerType
    source: str  # Where event came from
    data: dict[str, Any]  # Event payload
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class TriggerResult:
    """Result of trigger execution."""

    trigger_id: str
    success: bool
    actions_run: int
    actions_succeeded: int
    actions_failed: int
    message: str = ""
    duration_ms: int = 0
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


# ============================================================================
# TRIGGER MANAGER
# ============================================================================


class TriggerManager:
    """
    Manages trigger definitions, evaluation, and execution.

    Storage: ~/.navig/triggers/
    - triggers.yaml - Trigger definitions
    - history.jsonl - Execution history
    """

    def __init__(self, config_manager=None):
        from navig.config import get_config_manager

        self.config_manager = config_manager or get_config_manager()

        # Storage paths
        self.triggers_dir = Path(self.config_manager.global_config_dir) / "triggers"
        self.triggers_file = self.triggers_dir / "triggers.yaml"
        self.history_file = self.triggers_dir / "history.jsonl"

        # Ensure directory exists
        self.triggers_dir.mkdir(parents=True, exist_ok=True)

        # In-memory cache
        self._triggers: dict[str, Trigger] = {}
        self._loaded = False

    def _ensure_loaded(self):
        """Lazy-load triggers from disk."""
        if not self._loaded:
            self._load_triggers()
            self._loaded = True

    def _load_triggers(self):
        """Load triggers from YAML file."""
        self._triggers = {}

        if self.triggers_file.exists():
            try:
                with open(self.triggers_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}

                for trigger_data in data.get("triggers", []):
                    trigger = Trigger.from_dict(trigger_data)
                    self._triggers[trigger.id] = trigger
            except Exception as e:
                ch.warning(f"Failed to load triggers: {e}")

    def _save_triggers(self):
        """Save triggers to YAML file."""
        data = {
            "version": 1,
            "triggers": [t.to_dict() for t in self._triggers.values()],
        }
        try:
            # Atomic write: use a sibling temp file + rename so a crash
            # during the write never corrupts the active triggers config.
            tmp_path: Path | None = None
            try:
                fd, tmp = tempfile.mkstemp(
                    dir=self.triggers_dir, suffix=".tmp"
                )
                tmp_path = Path(tmp)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
                os.replace(tmp_path, self.triggers_file)
                tmp_path = None
            finally:
                if tmp_path is not None and tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
        except Exception as e:
            ch.error(f"Failed to save triggers: {e}")

    def _log_history(self, result: TriggerResult):
        """Append result to history file."""
        try:
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "trigger_id": result.trigger_id,
                            "success": result.success,
                            "actions_run": result.actions_run,
                            "actions_succeeded": result.actions_succeeded,
                            "actions_failed": result.actions_failed,
                            "message": result.message,
                            "duration_ms": result.duration_ms,
                            "timestamp": result.timestamp,
                        }
                    )
                    + "\n"
                )
        except Exception as _exc:
            logger.debug("history log skipped: %s", _exc)

    # ========================================================================
    # CRUD OPERATIONS
    # ========================================================================

    def add_trigger(self, trigger: Trigger) -> bool:
        """Add a new trigger."""
        self._ensure_loaded()

        if trigger.id in self._triggers:
            ch.error(f"Trigger '{trigger.id}' already exists")
            return False

        trigger.created_at = datetime.now().isoformat()
        trigger.updated_at = trigger.created_at
        self._triggers[trigger.id] = trigger
        self._save_triggers()
        return True

    def update_trigger(self, trigger: Trigger) -> bool:
        """Update an existing trigger."""
        self._ensure_loaded()

        if trigger.id not in self._triggers:
            ch.error(f"Trigger '{trigger.id}' not found")
            return False

        trigger.updated_at = datetime.now().isoformat()
        self._triggers[trigger.id] = trigger
        self._save_triggers()
        return True

    def remove_trigger(self, trigger_id: str) -> bool:
        """Remove a trigger by ID."""
        self._ensure_loaded()

        if trigger_id not in self._triggers:
            ch.error(f"Trigger '{trigger_id}' not found")
            return False

        del self._triggers[trigger_id]
        self._save_triggers()
        return True

    def get_trigger(self, trigger_id: str) -> Trigger | None:
        """Get a trigger by ID."""
        self._ensure_loaded()
        return self._triggers.get(trigger_id)

    def list_triggers(
        self,
        type_filter: TriggerType | None = None,
        status_filter: TriggerStatus | None = None,
        tag_filter: str | None = None,
    ) -> list[Trigger]:
        """List triggers with optional filtering."""
        self._ensure_loaded()

        triggers = list(self._triggers.values())

        if type_filter:
            triggers = [t for t in triggers if t.type == type_filter]

        if status_filter:
            triggers = [t for t in triggers if t.status == status_filter]

        if tag_filter:
            triggers = [t for t in triggers if tag_filter in t.tags]

        return sorted(triggers, key=lambda t: t.name)

    def enable_trigger(self, trigger_id: str) -> bool:
        """Enable a trigger."""
        trigger = self.get_trigger(trigger_id)
        if not trigger:
            return False
        trigger.status = TriggerStatus.ENABLED
        return self.update_trigger(trigger)

    def disable_trigger(self, trigger_id: str) -> bool:
        """Disable a trigger."""
        trigger = self.get_trigger(trigger_id)
        if not trigger:
            return False
        trigger.status = TriggerStatus.DISABLED
        return self.update_trigger(trigger)

    # ========================================================================
    # EVENT PROCESSING
    # ========================================================================

    def process_event(self, event: TriggerEvent) -> list[TriggerResult]:
        """
        Process an event against all triggers.

        Returns list of results for triggers that fired.
        """
        self._ensure_loaded()
        results = []

        for trigger in self._triggers.values():
            if trigger.type != event.type:
                continue

            if not trigger.can_fire():
                continue

            # Evaluate conditions
            if self._evaluate_conditions(trigger, event):
                result = self._execute_trigger(trigger, event)
                results.append(result)

        return results

    def _evaluate_conditions(self, trigger: Trigger, event: TriggerEvent) -> bool:
        """Evaluate if all conditions are met for trigger."""
        if not trigger.conditions:
            return True  # No conditions = always fire

        for condition in trigger.conditions:
            # Get actual value from event data
            actual_value = event.data.get(condition.target, event.data.get(condition.type))

            if not condition.evaluate(actual_value):
                return False

        return True

    def _execute_trigger(self, trigger: Trigger, event: TriggerEvent) -> TriggerResult:
        """Execute all actions for a trigger."""
        import time

        start_time = time.time()

        actions_run = 0
        actions_succeeded = 0
        actions_failed = 0
        messages = []

        # Mark as firing
        trigger.status = TriggerStatus.FIRING

        for action in trigger.actions:
            actions_run += 1
            success, msg = self._execute_action(action, trigger, event)

            if success:
                actions_succeeded += 1
            else:
                actions_failed += 1
                messages.append(msg)

                if action.on_failure == "stop":
                    break

        # Update trigger state
        trigger.status = TriggerStatus.ENABLED
        trigger.last_fired = datetime.now().isoformat()
        trigger.fire_count += 1
        self.update_trigger(trigger)

        duration_ms = int((time.time() - start_time) * 1000)

        result = TriggerResult(
            trigger_id=trigger.id,
            success=actions_failed == 0,
            actions_run=actions_run,
            actions_succeeded=actions_succeeded,
            actions_failed=actions_failed,
            message="; ".join(messages) if messages else "OK",
            duration_ms=duration_ms,
        )

        self._log_history(result)
        return result

    def _execute_action(
        self, action: TriggerAction, trigger: Trigger, event: TriggerEvent
    ) -> tuple[bool, str]:
        """Execute a single action."""
        try:
            if action.type == ActionType.COMMAND:
                return self._run_command(action.target, action.params)
            elif action.type == ActionType.WORKFLOW:
                return self._run_workflow(action.target, action.params)
            elif action.type == ActionType.NOTIFY:
                return self._send_notification(action.target, trigger, event, action.params)
            elif action.type == ActionType.WEBHOOK:
                return self._call_webhook(action.target, trigger, event, action.params)
            elif action.type == ActionType.SCRIPT:
                return self._run_script(action.target, action.params)
            else:
                return False, f"Unknown action type: {action.type}"
        except Exception as e:
            return False, str(e)

    def _run_command(self, command: str, params: dict[str, Any]) -> tuple[bool, str]:
        """Run a navig command."""
        import os
        import subprocess
        import sys

        # Variable substitution
        for key, value in params.items():
            command = command.replace(f"${{{key}}}", str(value))

        # Build full command
        if not command.startswith("navig "):
            command = f"navig {command}"

        try:
            # Set UTF-8 encoding for subprocess
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"

            result = subprocess.run(
                [sys.executable, "-m", "navig"] + command.replace("navig ", "").split(),
                capture_output=True,
                text=True,
                timeout=300,
                encoding="utf-8",
                errors="replace",
                env=env,
            )

            if result.returncode == 0:
                return True, ""
            else:
                return False, result.stderr or "Command failed"
        except subprocess.TimeoutExpired:
            return False, "Command timed out"
        except Exception as e:
            return False, str(e)

    def _run_workflow(self, workflow_name: str, params: dict[str, Any]) -> tuple[bool, str]:
        """Run a workflow."""
        from navig.commands.workflow import WorkflowManager

        manager = WorkflowManager()
        workflow = manager.load_workflow(workflow_name)

        if not workflow:
            return False, f"Workflow '{workflow_name}' not found"

        success = manager.execute_workflow(
            workflow,
            variables=params,
            skip_prompts=True,
            verbose=False,
        )

        return success, "" if success else "Workflow execution failed"

    def _send_notification(
        self,
        channel: str,
        trigger: Trigger,
        event: TriggerEvent,
        params: dict[str, Any],
    ) -> tuple[bool, str]:
        """Send notification via specified channel."""
        message = params.get("message", f"Trigger '{trigger.name}' fired")

        # Substitute variables
        message = message.replace("${trigger_name}", trigger.name)
        message = message.replace("${event_type}", event.type.value)
        message = message.replace("${event_source}", event.source)
        message = message.replace("${timestamp}", event.timestamp)

        if channel == "telegram":
            # Try to send via telegram bot
            try:
                from navig.commands.gateway import send_telegram_message

                send_telegram_message(message)
                return True, ""
            except Exception as e:
                return False, f"Telegram notification failed: {e}"
        elif channel == "console":
            ch.info(f"[TRIGGER] {message}")
            return True, ""
        elif channel == "log":
            from navig.debug_logger import get_logger

            logger = get_logger()
            logger.info("[TRIGGER] %s", message)
            return True, ""
        else:
            return False, f"Unknown notification channel: {channel}"

    def _call_webhook(
        self, url: str, trigger: Trigger, event: TriggerEvent, params: dict[str, Any]
    ) -> tuple[bool, str]:
        """Call external webhook."""
        try:
            import urllib.error
            import urllib.request

            payload = {
                "trigger_id": trigger.id,
                "trigger_name": trigger.name,
                "event_type": event.type.value,
                "event_source": event.source,
                "event_data": event.data,
                "timestamp": event.timestamp,
                **params,
            }

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status < 400:
                    return True, ""
                else:
                    return False, f"Webhook returned status {response.status}"
        except urllib.error.URLError as e:
            return False, f"Webhook call failed: {e}"
        except Exception as e:
            return False, str(e)

    def _run_script(self, script_path: str, params: dict[str, Any]) -> tuple[bool, str]:
        """Run a script file."""
        import subprocess
        import sys

        path = Path(script_path)
        if not path.exists():
            return False, f"Script not found: {script_path}"

        try:
            # Determine how to run based on extension
            if path.suffix == ".py":
                cmd = [sys.executable, str(path)]
            elif path.suffix == ".sh":
                cmd = ["bash", str(path)]
            elif path.suffix in (".ps1", ".psm1"):
                cmd = ["powershell", "-File", str(path)]
            else:
                cmd = [str(path)]

            # Add params as environment
            env = {
                **dict(__import__("os").environ),
                **{k: str(v) for k, v in params.items()},
            }

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )

            if result.returncode == 0:
                return True, ""
            else:
                return False, result.stderr or "Script failed"
        except subprocess.TimeoutExpired:
            return False, "Script timed out"
        except Exception as e:
            return False, str(e)

    # ========================================================================
    # MANUAL TRIGGER
    # ========================================================================

    def fire_trigger(self, trigger_id: str, dry_run: bool = False) -> TriggerResult | None:
        """Manually fire a trigger."""
        trigger = self.get_trigger(trigger_id)
        if not trigger:
            return None

        event = TriggerEvent(
            type=trigger.type,
            source="manual",
            data={"manual": True},
        )

        if dry_run:
            ch.info(f"[DRY RUN] Would fire trigger: {trigger.name}")
            for i, action in enumerate(trigger.actions, 1):
                ch.info(f"  Action {i}: {action.type.value} -> {action.target}")
            return TriggerResult(
                trigger_id=trigger_id,
                success=True,
                actions_run=0,
                actions_succeeded=0,
                actions_failed=0,
                message="Dry run completed",
            )

        return self._execute_trigger(trigger, event)

    # ========================================================================
    # HISTORY
    # ========================================================================

    def get_history(
        self,
        trigger_id: str | None = None,
        limit: int = 50,
        success_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Get trigger execution history."""
        history = []

        if not self.history_file.exists():
            return history

        try:
            with open(self.history_file, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        entry = json.loads(line)

                        if trigger_id and entry.get("trigger_id") != trigger_id:
                            continue

                        if success_only and not entry.get("success"):
                            continue

                        history.append(entry)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        # Return most recent first
        return list(reversed(history[-limit:]))

    def clear_history(self, trigger_id: str | None = None) -> int:
        """Clear trigger history."""
        if not self.history_file.exists():
            return 0

        if trigger_id is None:
            # Clear all
            with open(self.history_file, encoding="utf-8") as f:
                count = sum(1 for _ in f)
            self.history_file.unlink()
            return count

        # Clear for specific trigger
        kept = []
        removed = 0

        with open(self.history_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    if entry.get("trigger_id") == trigger_id:
                        removed += 1
                    else:
                        kept.append(line)

        _tmp_path: Path | None = None
        try:
            _fd, _tmp = tempfile.mkstemp(dir=self.history_file.parent, suffix=".tmp")
            _tmp_path = Path(_tmp)
            with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                _fh.writelines(kept)
            os.replace(_tmp_path, self.history_file)
            _tmp_path = None
        finally:
            if _tmp_path is not None:
                _tmp_path.unlink(missing_ok=True)

        return removed


# ============================================================================
# CLI DISPLAY FUNCTIONS
# ============================================================================


def list_triggers(
    type_filter: str | None = None,
    status_filter: str | None = None,
    tag: str | None = None,
    plain: bool = False,
    json_out: bool = False,
):
    """List all triggers."""
    from rich.table import Table

    manager = TriggerManager()

    # Convert filters
    tt = TriggerType(type_filter) if type_filter else None
    ts = TriggerStatus(status_filter) if status_filter else None

    triggers = manager.list_triggers(type_filter=tt, status_filter=ts, tag_filter=tag)

    if not triggers:
        if plain:
            print("No triggers configured.")
        else:
            ch.warning("No triggers configured.")
            ch.info("Add one with: navig trigger add")
        return

    if json_out:
        import json

        print(json.dumps([t.to_dict() for t in triggers], indent=2))
        return

    if plain:
        for t in triggers:
            status_icon = "+" if t.status == TriggerStatus.ENABLED else "-"
            print(f"{status_icon} {t.id}\t{t.type.value}\t{t.name}\t{t.fire_count}")
        return

    table = Table(title="Triggers")
    table.add_column("Status", style="dim", width=3)
    table.add_column("ID", style="cyan")
    table.add_column("Type", style="blue")
    table.add_column("Name")
    table.add_column("Fires", justify="right")
    table.add_column("Last Fired", style="dim")

    for t in triggers:
        if t.status == TriggerStatus.ENABLED:
            status = "[green]ON[/green]"
        elif t.status == TriggerStatus.DISABLED:
            status = "[red]OFF[/red]"
        else:
            status = f"[yellow]{t.status.value[:3].upper()}[/yellow]"

        last_fired = ""
        if t.last_fired:
            try:
                dt = datetime.fromisoformat(t.last_fired)
                last_fired = dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                last_fired = t.last_fired[:16]

        table.add_row(
            status,
            t.id,
            t.type.value,
            t.name,
            str(t.fire_count),
            last_fired or "-",
        )

    ch.console.print(table)


def show_trigger(trigger_id: str, plain: bool = False, json_out: bool = False):
    """Show detailed trigger information."""
    manager = TriggerManager()
    trigger = manager.get_trigger(trigger_id)

    if not trigger:
        ch.error(f"Trigger '{trigger_id}' not found")
        return

    if json_out:
        import json

        print(json.dumps(trigger.to_dict(), indent=2))
        return

    if plain:
        print(f"ID: {trigger.id}")
        print(f"Name: {trigger.name}")
        print(f"Type: {trigger.type.value}")
        print(f"Status: {trigger.status.value}")
        print(f"Description: {trigger.description}")
        print(f"Fire Count: {trigger.fire_count}")
        return

    # Header
    status = (
        "[green]ENABLED[/green]"
        if trigger.status == TriggerStatus.ENABLED
        else "[red]DISABLED[/red]"
    )
    ch.header(f"{trigger.name} ({status})")

    if trigger.description:
        ch.console.print(f"[dim]{trigger.description}[/dim]\n")

    # Metadata
    ch.console.print(f"[bold]ID:[/bold] {trigger.id}")
    ch.console.print(f"[bold]Type:[/bold] {trigger.type.value}")
    ch.console.print(f"[bold]Cooldown:[/bold] {trigger.cooldown_seconds}s")
    ch.console.print(f"[bold]Rate Limit:[/bold] {trigger.max_fires_per_hour}/hour")

    if trigger.tags:
        ch.console.print(f"[bold]Tags:[/bold] {', '.join(trigger.tags)}")

    if trigger.schedule:
        ch.console.print(f"[bold]Schedule:[/bold] {trigger.schedule}")

    if trigger.host:
        ch.console.print(f"[bold]Host:[/bold] {trigger.host}")

    # Stats
    ch.console.print(f"\n[bold]Fire Count:[/bold] {trigger.fire_count}")
    if trigger.last_fired:
        ch.console.print(f"[bold]Last Fired:[/bold] {trigger.last_fired}")

    # Conditions
    if trigger.conditions:
        ch.console.print("\n[bold]Conditions:[/bold]")
        for c in trigger.conditions:
            ch.console.print(f"  - {c.target or c.type} {c.operator} {c.value}")

    # Actions
    ch.console.print("\n[bold]Actions:[/bold]")
    for i, a in enumerate(trigger.actions, 1):
        ch.console.print(f"  {i}. [{a.type.value}] {a.target}")
        if a.params:
            ch.console.print(f"     Params: {a.params}")


def add_trigger_interactive():
    """Interactive trigger creation wizard."""
    import typer

    ch.header("Create New Trigger")

    # Name
    name = typer.prompt("Trigger name")

    # Type
    ch.info("\nAvailable trigger types:")
    for tt in TriggerType:
        ch.console.print(f"  - {tt.value}")

    type_str = typer.prompt("Trigger type", default="manual")
    try:
        trigger_type = TriggerType(type_str)
    except ValueError:
        ch.error(f"Invalid trigger type: {type_str}")
        return

    # Description
    description = typer.prompt("Description (optional)", default="")

    # Action
    ch.info("\nAction to execute when trigger fires:")
    ch.info("  - Enter a navig command (e.g., 'host list')")
    ch.info("  - Or workflow name prefixed with 'workflow:' (e.g., 'workflow:deploy')")

    action_str = typer.prompt("Action")

    if action_str.startswith("workflow:"):
        action = TriggerAction(
            type=ActionType.WORKFLOW,
            target=action_str.replace("workflow:", ""),
        )
    else:
        action = TriggerAction(
            type=ActionType.COMMAND,
            target=action_str,
        )

    # Create trigger
    trigger = Trigger(
        id="",
        name=name,
        type=trigger_type,
        description=description,
        actions=[action],
    )

    # Schedule for schedule triggers
    if trigger_type == TriggerType.SCHEDULE:
        schedule = typer.prompt("Schedule (e.g., '0 9 * * *' or '1h')")
        trigger.schedule = schedule

    # Host for threshold triggers
    if trigger_type == TriggerType.THRESHOLD:
        host = typer.prompt("Host to monitor", default="")
        metric = typer.prompt("Metric (cpu, memory, disk)")
        threshold = typer.prompt("Threshold value (e.g., 80 for 80%)")

        trigger.host = host
        trigger.metric = metric
        trigger.conditions = [
            TriggerCondition(type="metric", operator="gte", value=int(threshold), target=metric)
        ]

    # Save
    manager = TriggerManager()
    if manager.add_trigger(trigger):
        ch.success(f"Created trigger: {trigger.id}")
        ch.info(f"\nTest with: navig trigger test {trigger.id}")
        ch.info(f"Fire with: navig trigger fire {trigger.id}")


def add_trigger_quick(
    name: str,
    action: str,
    trigger_type: str = "manual",
    description: str = "",
    schedule: str = "",
    host: str = "",
    condition: str = "",
):
    """Quick trigger creation from CLI."""
    try:
        tt = TriggerType(trigger_type)
    except ValueError:
        ch.error(f"Invalid trigger type: {trigger_type}")
        ch.info(f"Valid types: {', '.join(t.value for t in TriggerType)}")
        return

    # Parse action
    if action.startswith("workflow:"):
        trigger_action = TriggerAction(
            type=ActionType.WORKFLOW,
            target=action.replace("workflow:", ""),
        )
    elif action.startswith("notify:"):
        trigger_action = TriggerAction(
            type=ActionType.NOTIFY,
            target=action.replace("notify:", ""),
        )
    elif action.startswith("webhook:"):
        trigger_action = TriggerAction(
            type=ActionType.WEBHOOK,
            target=action.replace("webhook:", ""),
        )
    else:
        trigger_action = TriggerAction(
            type=ActionType.COMMAND,
            target=action,
        )

    trigger = Trigger(
        id="",
        name=name,
        type=tt,
        description=description,
        actions=[trigger_action],
        schedule=schedule,
        host=host,
    )

    # Parse condition if provided (format: "target op value")
    if condition:
        parts = condition.split()
        if len(parts) >= 3:
            trigger.conditions = [
                TriggerCondition(
                    type=parts[0],
                    operator=parts[1],
                    value=parts[2],
                    target=parts[0],
                )
            ]

    manager = TriggerManager()
    if manager.add_trigger(trigger):
        ch.success(f"Created trigger: {trigger.id}")


def remove_trigger(trigger_id: str, force: bool = False):
    """Remove a trigger."""
    import typer

    manager = TriggerManager()
    trigger = manager.get_trigger(trigger_id)

    if not trigger:
        ch.error(f"Trigger '{trigger_id}' not found")
        return

    if not force:
        if not typer.confirm(f"Remove trigger '{trigger.name}'?", default=False):
            ch.info("Cancelled")
            return

    if manager.remove_trigger(trigger_id):
        ch.success(f"Removed trigger: {trigger.name}")


def enable_trigger(trigger_id: str):
    """Enable a trigger."""
    manager = TriggerManager()
    if manager.enable_trigger(trigger_id):
        ch.success(f"Enabled trigger: {trigger_id}")


def disable_trigger(trigger_id: str):
    """Disable a trigger."""
    manager = TriggerManager()
    if manager.disable_trigger(trigger_id):
        ch.success(f"Disabled trigger: {trigger_id}")


def test_trigger(trigger_id: str):
    """Test a trigger (dry run)."""
    manager = TriggerManager()
    result = manager.fire_trigger(trigger_id, dry_run=True)

    if result:
        ch.success("Trigger test completed")


def fire_trigger(trigger_id: str):
    """Manually fire a trigger."""
    manager = TriggerManager()
    result = manager.fire_trigger(trigger_id, dry_run=False)

    if result:
        if result.success:
            ch.success(
                f"Trigger fired successfully: {result.actions_succeeded}/{result.actions_run} actions succeeded"
            )
        else:
            ch.error(f"Trigger failed: {result.message}")
            ch.info(
                f"Actions: {result.actions_succeeded} succeeded, {result.actions_failed} failed"
            )


def show_trigger_history(
    trigger_id: str | None = None,
    limit: int = 20,
    plain: bool = False,
    json_out: bool = False,
):
    """Show trigger execution history."""
    from rich.table import Table

    manager = TriggerManager()
    history = manager.get_history(trigger_id=trigger_id, limit=limit)

    if not history:
        ch.warning("No trigger history found.")
        return

    if json_out:
        import json

        print(json.dumps(history, indent=2))
        return

    if plain:
        for entry in history:
            status = "OK" if entry["success"] else "FAIL"
            print(f"{entry['timestamp']}\t{entry['trigger_id']}\t{status}\t{entry['message']}")
        return

    table = Table(title="Trigger History")
    table.add_column("Time", style="dim")
    table.add_column("Trigger")
    table.add_column("Status")
    table.add_column("Actions", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Message")

    for entry in history:
        # Format timestamp
        try:
            dt = datetime.fromisoformat(entry["timestamp"])
            time_str = dt.strftime("%m-%d %H:%M:%S")
        except ValueError:
            time_str = entry["timestamp"][:19]

        status = "[green]OK[/green]" if entry["success"] else "[red]FAIL[/red]"
        actions = f"{entry['actions_succeeded']}/{entry['actions_run']}"
        duration = f"{entry['duration_ms']}ms"
        message = entry.get("message", "")[:30]

        table.add_row(
            time_str,
            entry["trigger_id"],
            status,
            actions,
            duration,
            message,
        )

    ch.console.print(table)


def clear_trigger_history(trigger_id: str | None = None, force: bool = False):
    """Clear trigger history."""
    import typer

    if not force:
        target = f"for trigger '{trigger_id}'" if trigger_id else "all"
        if not typer.confirm(f"Clear trigger history {target}?", default=False):
            ch.info("Cancelled")
            return

    manager = TriggerManager()
    count = manager.clear_history(trigger_id)
    ch.success(f"Cleared {count} history entries")


def show_trigger_stats():
    """Show trigger statistics."""

    manager = TriggerManager()
    triggers = manager.list_triggers()
    history = manager.get_history(limit=1000)

    if not triggers:
        ch.warning("No triggers configured.")
        return

    ch.header("Trigger Statistics")

    # Overall stats
    total_triggers = len(triggers)
    enabled = sum(1 for t in triggers if t.status == TriggerStatus.ENABLED)
    disabled = total_triggers - enabled
    total_fires = sum(t.fire_count for t in triggers)

    ch.console.print(f"\n[bold]Total Triggers:[/bold] {total_triggers}")
    ch.console.print(f"[bold]Enabled:[/bold] {enabled}")
    ch.console.print(f"[bold]Disabled:[/bold] {disabled}")
    ch.console.print(f"[bold]Total Fires:[/bold] {total_fires}")

    # By type
    ch.console.print("\n[bold]By Type:[/bold]")
    type_counts = {}
    for t in triggers:
        type_counts[t.type.value] = type_counts.get(t.type.value, 0) + 1
    for tt, count in sorted(type_counts.items()):
        ch.console.print(f"  {tt}: {count}")

    # Recent activity
    if history:
        recent_success = sum(1 for h in history if h["success"])
        recent_fail = len(history) - recent_success
        ch.console.print(f"\n[bold]Recent Activity (last {len(history)}):[/bold]")
        ch.console.print(f"  Success: {recent_success}")
        ch.console.print(f"  Failed: {recent_fail}")


# ============================================================================
# TYPER SUB-APP — extracted from navig/cli/__init__.py
# ============================================================================

import typer  # noqa: E402


trigger_app = typer.Typer(
    help="Event-driven automation triggers",
    invoke_without_command=True,
    no_args_is_help=False,
)


@trigger_app.callback()
def trigger_callback(ctx: typer.Context):
    """Event-driven automation triggers - run without subcommand for list."""
    if ctx.invoked_subcommand is None:
        list_triggers()
        raise typer.Exit()


@trigger_app.command("list")
def trigger_list_cmd(
    ctx: typer.Context,
    type_filter: str | None = typer.Option(None, "--type", "-t", help="Filter by trigger type"),
    status: str | None = typer.Option(
        None, "--status", "-s", help="Filter by status (enabled/disabled)"
    ),
    tag: str | None = typer.Option(None, "--tag", help="Filter by tag"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List all configured triggers."""
    list_triggers(
        type_filter=type_filter,
        status_filter=status,
        tag=tag,
        plain=plain,
        json_out=json_out,
    )


@trigger_app.command("show")
def trigger_show_cmd(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to show"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show detailed trigger information."""
    show_trigger(trigger_id, plain=plain, json_out=json_out)


@trigger_app.command("add")
def trigger_add_cmd(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Trigger name"),
    action: str | None = typer.Option(None, "--action", "-a", help="Action to execute"),
    trigger_type: str = typer.Option(
        "manual",
        "--type",
        "-t",
        help="Trigger type (health, schedule, threshold, manual)",
    ),
    description: str = typer.Option("", "--desc", "-d", help="Description"),
    schedule: str = typer.Option(
        "", "--schedule", help="Schedule expression (for schedule triggers)"
    ),
    host: str = typer.Option("", "--host", help="Target host (for threshold triggers)"),
    condition: str = typer.Option(
        "", "--condition", "-c", help="Condition (format: 'target op value')"
    ),
):
    """
    Create a new trigger.

    Interactive mode (no args):
        navig trigger add

    Quick mode:
        navig trigger add "Disk Alert" --action "notify:telegram" --type threshold --host prod --condition "disk gte 80"
        navig trigger add "Daily Backup" --action "workflow:backup" --type schedule --schedule "0 2 * * *"
        navig trigger add "Health Check" --action "host test" --type health

    Action formats:
        - navig command: "host list", "db dump", etc.
        - workflow: "workflow:deploy", "workflow:backup"
        - notify: "notify:telegram", "notify:console"
        - webhook: "webhook:https://example.com/hook"
    """
    if name is None:
        add_trigger_interactive()
    else:
        if not action:
            from navig import console_helper as _ch

            _ch.error(
                "Action is required for quick mode. Use --action or run without args for interactive mode."
            )
            return
        add_trigger_quick(
            name=name,
            action=action,
            trigger_type=trigger_type,
            description=description,
            schedule=schedule,
            host=host,
            condition=condition,
        )


@trigger_app.command("remove")
def trigger_remove_cmd(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove a trigger."""
    remove_trigger(trigger_id, force=force)


@trigger_app.command("enable")
def trigger_enable_cmd(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to enable"),
):
    """Enable a disabled trigger."""
    enable_trigger(trigger_id)


@trigger_app.command("disable")
def trigger_disable_cmd(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to disable"),
):
    """Disable a trigger (stops it from firing)."""
    disable_trigger(trigger_id)


@trigger_app.command("test")
def trigger_test_cmd(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to test"),
):
    """
    Test a trigger (dry run).

    Shows what actions would be executed without actually running them.
    """
    test_trigger(trigger_id)


@trigger_app.command("fire")
def trigger_fire_cmd(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to fire"),
):
    """
    Manually fire a trigger.

    Executes all actions associated with the trigger immediately,
    regardless of conditions or cooldown.
    """
    fire_trigger(trigger_id)


@trigger_app.command("history")
def trigger_history_cmd(
    ctx: typer.Context,
    trigger_id: str | None = typer.Argument(None, help="Filter by trigger ID"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max entries to show"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show trigger execution history."""
    show_trigger_history(
        trigger_id=trigger_id,
        limit=limit,
        plain=plain,
        json_out=json_out,
    )


@trigger_app.command("clear-history")
def trigger_clear_history_cmd(
    ctx: typer.Context,
    trigger_id: str | None = typer.Argument(None, help="Clear history for specific trigger only"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Clear trigger execution history."""
    clear_trigger_history(trigger_id=trigger_id, force=force)


@trigger_app.command("stats")
def trigger_stats_cmd(ctx: typer.Context):
    """Show trigger statistics."""
    show_trigger_stats()
