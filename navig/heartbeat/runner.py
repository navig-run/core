"""
Heartbeat Runner - Periodic health check system

Based on periodic heartbeat pattern:
- Runs every N minutes (default 30)
- AI agent checks system health
- HEARTBEAT_OK suppresses notifications
- Only alerts on actual issues
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from navig.debug_logger import get_debug_logger

if TYPE_CHECKING:
    from navig.gateway.server import NavigGateway

logger = get_debug_logger()


@dataclass
class HeartbeatConfig:
    """Heartbeat configuration."""

    enabled: bool = True
    interval_minutes: int = 30
    timeout_seconds: int = 300  # 5 minutes max per heartbeat
    retry_on_error: bool = True
    retry_delay_seconds: int = 60
    max_retries: int = 3
    notify_on_start: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "HeartbeatConfig":
        return cls(
            enabled=data.get("enabled", True),
            interval_minutes=data.get("interval", 30),
            timeout_seconds=data.get("timeout", 300),
            retry_on_error=data.get("retry_on_error", True),
            retry_delay_seconds=data.get("retry_delay", 60),
            max_retries=data.get("max_retries", 3),
            notify_on_start=data.get("notify_on_start", False),
        )


@dataclass
class HeartbeatResult:
    """Result of a heartbeat check."""

    success: bool
    response: str
    duration_seconds: float
    timestamp: datetime
    suppressed: bool = False  # True if HEARTBEAT_OK
    error: Optional[str] = None
    issues_found: List[str] = None

    def __post_init__(self):
        if self.issues_found is None:
            self.issues_found = []

        # Check for HEARTBEAT_OK pattern
        if self.success and "HEARTBEAT_OK" in self.response:
            self.suppressed = True


class HeartbeatRunner:
    """
    Runs periodic health checks using the AI agent.

    The AI agent receives the HEARTBEAT.md instructions and:
    1. Checks all configured hosts
    2. Verifies service health
    3. Checks disk/memory usage
    4. Returns HEARTBEAT_OK if all is well
    5. Returns detailed issues if problems found
    """

    def __init__(
        self, gateway: "NavigGateway", config: Optional[HeartbeatConfig] = None
    ):
        self.gateway = gateway
        self.config = config or HeartbeatConfig()

        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Track heartbeat history
        self._history: List[HeartbeatResult] = []
        self._max_history = 100

        # Last heartbeat time
        self._last_heartbeat: Optional[datetime] = None
        self._next_heartbeat: Optional[datetime] = None

        # Callbacks
        self._on_issue_callbacks: List[Callable] = []
        self._on_complete_callbacks: List[Callable] = []

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start the heartbeat runner."""
        if self._running:
            logger.warning("Heartbeat runner already running")
            return

        if not self.config.enabled:
            logger.info("Heartbeat disabled in config")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())

        logger.info(
            f"Heartbeat runner started " f"(interval: {self.config.interval_minutes}m)"
        )

        # Emit start event
        if self.gateway.event_queue:
            from navig.gateway.system_events import EventTypes

            await self.gateway.event_queue.emit(
                EventTypes.HEARTBEAT_START,
                {"interval_minutes": self.config.interval_minutes},
            )

    async def stop(self) -> None:
        """Stop the heartbeat runner."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        logger.info("Heartbeat runner stopped")

    def get_time_until_next(self) -> str:
        """Get human-readable time until next heartbeat."""
        if not self._next_heartbeat:
            return "unknown"

        delta = self._next_heartbeat - datetime.now()
        if delta.total_seconds() <= 0:
            return "now"

        minutes = int(delta.total_seconds() / 60)
        seconds = int(delta.total_seconds() % 60)

        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def on_issue(self, callback: Callable) -> None:
        """Register callback for when issues are found."""
        self._on_issue_callbacks.append(callback)

    def on_complete(self, callback: Callable) -> None:
        """Register callback for heartbeat completion."""
        self._on_complete_callbacks.append(callback)

    async def _run_loop(self) -> None:
        """Main heartbeat loop."""
        # Initial delay (randomized to avoid thundering herd)
        import random

        initial_delay = random.randint(10, 60)
        logger.debug(f"Heartbeat initial delay: {initial_delay}s")
        await asyncio.sleep(initial_delay)

        while self._running:
            try:
                # Run heartbeat
                await self._execute_heartbeat()

                # Calculate next heartbeat time
                self._next_heartbeat = datetime.now() + timedelta(
                    minutes=self.config.interval_minutes
                )

                # Sleep until next heartbeat
                await asyncio.sleep(self.config.interval_minutes * 60)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat loop error: {e}")
                # Wait before retrying
                await asyncio.sleep(self.config.retry_delay_seconds)

    async def _execute_heartbeat(self) -> HeartbeatResult:
        """Execute a single heartbeat check."""
        start_time = datetime.now()
        logger.info("Starting heartbeat check...")

        try:
            # Get heartbeat instructions
            heartbeat_prompt = self._build_heartbeat_prompt()

            # Run agent
            response = await asyncio.wait_for(
                self._run_heartbeat_agent(heartbeat_prompt),
                timeout=self.config.timeout_seconds,
            )

            duration = (datetime.now() - start_time).total_seconds()

            result = HeartbeatResult(
                success=True,
                response=response,
                duration_seconds=duration,
                timestamp=datetime.now(),
            )

            # Parse for issues
            result.issues_found = self._parse_issues(response)

        except asyncio.TimeoutError:
            duration = (datetime.now() - start_time).total_seconds()
            result = HeartbeatResult(
                success=False,
                response="",
                duration_seconds=duration,
                timestamp=datetime.now(),
                error="Heartbeat timed out",
            )

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            result = HeartbeatResult(
                success=False,
                response="",
                duration_seconds=duration,
                timestamp=datetime.now(),
                error=str(e),
            )

        # Record result
        self._last_heartbeat = datetime.now()
        self._history.append(result)

        # Trim history
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

        # Handle result
        await self._handle_result(result)

        return result

    def _build_heartbeat_prompt(self) -> str:
        """Build the prompt for the heartbeat agent."""
        # Get workspace context
        from navig.gateway.config_watcher import WorkspaceManager

        workspace = WorkspaceManager(Path(self.gateway.config_manager.global_path))

        heartbeat_instructions = workspace.read_file("HEARTBEAT.md")

        # Get list of hosts to check
        config = self.gateway.config_manager.global_config
        hosts = config.get("hosts", {})
        host_list = list(hosts.keys()) if hosts else ["(no hosts configured)"]

        prompt = f"""You are performing a scheduled health check.

## Instructions
{heartbeat_instructions}

## Hosts to Check
{chr(10).join(f'- {h}' for h in host_list)}

## Required Response Format
If everything is healthy, respond with EXACTLY:
HEARTBEAT_OK

If there are issues, list them:
ISSUES FOUND:
- [severity] description
- [severity] description

Then provide recommended actions.

Begin the health check now. Be thorough but efficient.
"""
        return prompt

    async def _run_heartbeat_agent(self, prompt: str) -> str:
        """Run the AI agent for heartbeat."""
        # Use the gateway's agent turn method
        response = await self.gateway.run_agent_turn(
            agent_id="heartbeat",
            session_key="system:heartbeat",
            message=prompt,
        )

        return response

    def _parse_issues(self, response: str) -> List[str]:
        """Parse issues from heartbeat response."""
        issues = []

        if "HEARTBEAT_OK" in response:
            return issues

        # Look for issue patterns
        lines = response.split("\n")
        in_issues_section = False

        for line in lines:
            line = line.strip()

            if "ISSUES FOUND" in line.upper():
                in_issues_section = True
                continue

            if in_issues_section and line.startswith("-"):
                issues.append(line[1:].strip())

            # Also catch [severity] pattern
            if line.startswith("[") and "]" in line:
                issues.append(line)

        return issues

    async def _handle_result(self, result: HeartbeatResult) -> None:
        """Handle heartbeat result."""
        if result.suppressed:
            logger.info(f"Heartbeat OK (duration: {result.duration_seconds:.1f}s)")

            # Emit event but don't notify
            if self.gateway.event_queue:
                from navig.gateway.system_events import EventTypes

                await self.gateway.event_queue.emit(
                    EventTypes.HEARTBEAT_COMPLETE,
                    {
                        "success": True,
                        "suppressed": True,
                        "duration": result.duration_seconds,
                    },
                )
            return

        if not result.success:
            logger.error(f"Heartbeat failed: {result.error}")

            # Notify about failure
            await self._notify_issue(f"[!] Heartbeat check failed: {result.error}")

            if self.gateway.event_queue:
                from navig.gateway.system_events import EventTypes

                await self.gateway.event_queue.emit(
                    EventTypes.HEARTBEAT_FAILED,
                    {
                        "error": result.error,
                        "duration": result.duration_seconds,
                    },
                )
            return

        if result.issues_found:
            logger.warning(f"Heartbeat found {len(result.issues_found)} issues")

            # Format and send notification
            issue_text = "\n".join(f"- {i}" for i in result.issues_found)
            await self._notify_issue(f"[!] Health check found issues:\n{issue_text}")

            # Call issue callbacks
            for callback in self._on_issue_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(result.issues_found)
                    else:
                        callback(result.issues_found)
                except Exception as e:
                    logger.error(f"Issue callback error: {e}")

        # Complete event
        if self.gateway.event_queue:
            from navig.gateway.system_events import EventTypes

            await self.gateway.event_queue.emit(
                EventTypes.HEARTBEAT_COMPLETE,
                {
                    "success": True,
                    "suppressed": False,
                    "issues_count": len(result.issues_found),
                    "duration": result.duration_seconds,
                },
            )

        # Call complete callbacks
        for callback in self._on_complete_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(result)
                else:
                    callback(result)
            except Exception as e:
                logger.error(f"Complete callback error: {e}")

    async def _notify_issue(self, message: str) -> None:
        """Send notification about an issue."""
        # Get notification settings from config
        config = self.gateway.config_manager.global_config
        notify_config = config.get("notifications", {})

        # Get primary channel
        channel = notify_config.get("channel", "telegram")
        recipient = notify_config.get("recipient")

        if not recipient:
            logger.warning("No notification recipient configured")
            return

        # Use smart notification filter if available
        if hasattr(self.gateway, "notification_filter"):
            from navig.gateway.system_events import EventPriority

            should_send = await self.gateway.notification_filter.should_notify(
                "heartbeat_issue", message, EventPriority.HIGH
            )
            if not should_send:
                return

        # Send via channel
        await self.gateway.send_notification(
            channel=channel, recipient=recipient, message=message
        )

    def get_status(self) -> Dict[str, Any]:
        """Get heartbeat status."""
        return {
            "running": self._running,
            "enabled": self.config.enabled,
            "interval_minutes": self.config.interval_minutes,
            "last_heartbeat": (
                self._last_heartbeat.isoformat() if self._last_heartbeat else None
            ),
            "next_heartbeat": (
                self._next_heartbeat.isoformat() if self._next_heartbeat else None
            ),
            "time_until_next": self.get_time_until_next(),
            "history_count": len(self._history),
            "last_success": self._history[-1].success if self._history else None,
            "last_suppressed": self._history[-1].suppressed if self._history else None,
        }

    def get_history(self, limit: int = 10) -> List[Dict]:
        """Get recent heartbeat history."""
        return [
            {
                "success": r.success,
                "suppressed": r.suppressed,
                "duration": r.duration_seconds,
                "timestamp": r.timestamp.isoformat(),
                "issues_count": len(r.issues_found),
                "error": r.error,
            }
            for r in self._history[-limit:]
        ]

    async def trigger_now(self) -> HeartbeatResult:
        """Trigger an immediate heartbeat check."""
        logger.info("Manual heartbeat triggered")
        return await self._execute_heartbeat()
